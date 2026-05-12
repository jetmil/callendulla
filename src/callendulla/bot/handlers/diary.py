# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Voice diary: accept voice messages, store encrypted, list/play/forget.

Flow:

- User sends a voice message → :func:`handle_voice` downloads bytes
  via ``bot.download``, encrypts with Fernet
  (``Settings.diary_encryption_key``), persists a :class:`VoiceDiary`
  row. **Plaintext never lands on disk.** Reply is immediate.
- If STT is configured, an asyncio background task transcribes the
  audio, encrypts the transcript with the same key, and updates the
  row in place. The user gets a follow-up reply when ready.
- ``/diary`` (no args) → list of recent entries with id, date, duration
- ``/diary play <id>`` → decrypt in memory, send back as a voice message
- ``/diary transcript <id>`` → show the decrypted transcript
- ``/diary forget <id>`` → delete the row (GDPR-style erasure)
"""

from __future__ import annotations

import asyncio
from typing import BinaryIO

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.exc import NoResultFound

from callendulla.config import Settings
from callendulla.core.voice_crypto import DecryptionError, decrypt, encrypt
from callendulla.db.models import User, VoiceDiary
from callendulla.db.session import SessionFactory
from callendulla.stt.base import STTError, STTProvider

router = Router(name="diary")

# Keep strong refs to in-flight background STT tasks so the GC does
# not collect them — Python may drop the task otherwise mid-await.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


_HELP = (
    "<b>Голосовой дневник</b>\n\n"
    "Пришли голосовое сообщение — я сохраню зашифровано "
    "(ключ — твой <code>DIARY_ENCRYPTION_KEY</code>, кроме тебя никто не "
    "прочитает).\n\n"
    "Команды:\n"
    "• <code>/diary</code> — список последних записей\n"
    "• <code>/diary play &lt;id&gt;</code> — переслать запись обратно\n"
    "• <code>/diary transcript &lt;id&gt;</code> — показать расшифровку\n"
    "• <code>/diary forget &lt;id&gt;</code> — удалить запись"
)


@router.message(F.voice)
async def handle_voice(
    message: types.Message,
    user: User | None,
    bot: Bot,
    settings: Settings,
    session_factory: SessionFactory,
    stt: STTProvider | None = None,
) -> None:
    if user is None:
        # Not registered — don't store audio for anonymous users; they
        # would have no way to get it back anyway.
        return
    voice = message.voice
    if voice is None:  # defensive: F.voice already filtered, but mypy doesn't know
        return

    file = await bot.get_file(voice.file_id)
    if file.file_path is None:
        await message.reply("Не получилось скачать голосовое — попробуй ещё раз.")
        return

    buffer: BinaryIO | None = await bot.download_file(file.file_path)
    if buffer is None:
        await message.reply("Не получилось скачать голосовое — попробуй ещё раз.")
        return

    audio_plain = buffer.read()
    audio_ct = encrypt(audio_plain, key=settings.diary_encryption_key)
    # Empty transcript ciphertext as a placeholder until STT lands.
    # An empty bytes value still gets encrypted so the column shape
    # stays uniform.
    transcript_ct = encrypt(b"", key=settings.diary_encryption_key)

    async with session_factory() as session:
        entry = VoiceDiary(
            owner_user_id=user.id,
            audio_ciphertext=audio_ct,
            transcript_ciphertext=transcript_ct,
            duration_sec=float(voice.duration) if voice.duration else None,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        entry_id = entry.id

    reply_text = (
        f"🎙 Запись <b>#{entry_id}</b> сохранена.\n"
        f"<code>/diary play {entry_id}</code> — переслать обратно.\n"
    )
    if stt is not None:
        reply_text += "Расшифровка пишется в фоне — пришлю когда готова.\n"
    reply_text += f"<code>/diary forget {entry_id}</code> — удалить."

    await message.reply(reply_text)
    logger.info("diary: user {user_id} stored entry {entry_id}", user_id=user.id, entry_id=entry_id)

    # Fire-and-forget STT. If the process restarts mid-task the
    # transcript stays as the empty placeholder — the user can
    # request a re-run later (TODO command). Persistent queue is a
    # later concern. We stash the task on a module-level set so it
    # is not garbage-collected mid-flight (Python may drop unowned
    # tasks at any time).
    if stt is not None:
        task = asyncio.create_task(
            _transcribe_and_persist(
                bot=bot,
                chat_id=message.chat.id,
                entry_id=entry_id,
                audio_plain=audio_plain,
                stt=stt,
                settings=settings,
                session_factory=session_factory,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _transcribe_and_persist(
    *,
    bot: Bot,
    chat_id: int,
    entry_id: int,
    audio_plain: bytes,
    stt: STTProvider,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    """Background coroutine: transcribe → encrypt → update row.

    Lives outside ``handle_voice`` so it survives the handler return
    and can be unit-tested directly. Plaintext bytes are passed in
    rather than re-fetched: the upstream caller still has them in RAM
    from the download, no point round-tripping through the DB.
    """
    try:
        transcript = await stt.transcribe(audio_plain, fmt="ogg")
    except STTError as exc:
        logger.warning(
            "diary entry {entry_id}: transcription failed ({reason})",
            entry_id=entry_id,
            reason=str(exc),
        )
        return

    ciphertext = encrypt(transcript.encode("utf-8"), key=settings.diary_encryption_key)
    async with session_factory() as session:
        entry = (
            await session.execute(select(VoiceDiary).where(VoiceDiary.id == entry_id))
        ).scalar_one_or_none()
        if entry is None:
            # User /diary forgot it between voice arrival and transcript
            # ready — fine, drop the transcript.
            return
        entry.transcript_ciphertext = ciphertext
        await session.commit()

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"📝 Расшифровка #{entry_id} готова: <code>/diary transcript {entry_id}</code>",
        )
    except Exception:
        logger.exception(
            "diary entry {entry_id}: notify-after-transcript failed", entry_id=entry_id
        )


@router.message(Command("diary"))
async def handle_diary(
    message: types.Message,
    user: User | None,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await message.answer("Сначала зарегистрируйся — <code>/start</code>.")
        return

    args = (command.args or "").strip().split()
    if not args:
        await _list_entries(message, user, session_factory)
        return

    sub, *rest = args
    if sub == "play" and rest and rest[0].isdigit():
        await _play_entry(message, user, int(rest[0]), bot, settings, session_factory)
        return
    if sub == "transcript" and rest and rest[0].isdigit():
        await _show_transcript(message, user, int(rest[0]), settings, session_factory)
        return
    if sub == "forget" and rest and rest[0].isdigit():
        await _forget_entry(message, user, int(rest[0]), session_factory)
        return

    await message.answer(_HELP, disable_web_page_preview=True)


async def _list_entries(
    message: types.Message, user: User, session_factory: SessionFactory
) -> None:
    async with session_factory() as session:
        stmt = (
            select(VoiceDiary)
            .where(VoiceDiary.owner_user_id == user.id)
            .order_by(desc(VoiceDiary.created_at))
            .limit(10)
        )
        entries = list((await session.execute(stmt)).scalars())

    if not entries:
        await message.answer(_HELP, disable_web_page_preview=True)
        return

    lines = ["<b>Твои записи:</b>"]
    for e in entries:
        dur = f"{e.duration_sec:.0f}s" if e.duration_sec else "—"
        # SQLite drops tz; treat naive as UTC for display
        when = e.created_at.strftime("%Y-%m-%d %H:%M")
        lines.append(f"#{e.id} · {when} · {dur}")
    lines.append("\n<code>/diary play &lt;id&gt;</code> · <code>/diary forget &lt;id&gt;</code>")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


async def _play_entry(
    message: types.Message,
    user: User,
    entry_id: int,
    bot: Bot,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        stmt = select(VoiceDiary).where(
            VoiceDiary.id == entry_id,
            VoiceDiary.owner_user_id == user.id,
        )
        entry = (await session.execute(stmt)).scalar_one_or_none()
        if entry is None:
            # Same response for "not yours" and "doesn't exist".
            await message.answer(f"Запись #{entry_id} не найдена.")
            return
        ciphertext = entry.audio_ciphertext

    try:
        audio_plain = decrypt(ciphertext, key=settings.diary_encryption_key)
    except DecryptionError:
        await message.answer(
            f"Запись #{entry_id} не расшифровать. "
            "Возможно, ключ DIARY_ENCRYPTION_KEY был сменён без re-encrypt."
        )
        return

    voice_file = BufferedInputFile(audio_plain, filename=f"diary_{entry_id}.ogg")
    await bot.send_voice(chat_id=message.chat.id, voice=voice_file)


async def _show_transcript(
    message: types.Message,
    user: User,
    entry_id: int,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        stmt = select(VoiceDiary).where(
            VoiceDiary.id == entry_id,
            VoiceDiary.owner_user_id == user.id,
        )
        entry = (await session.execute(stmt)).scalar_one_or_none()
        if entry is None:
            await message.answer(f"Запись #{entry_id} не найдена.")
            return
        ciphertext = entry.transcript_ciphertext

    try:
        transcript = decrypt(ciphertext, key=settings.diary_encryption_key).decode(
            "utf-8", errors="replace"
        )
    except DecryptionError:
        await message.answer(
            f"Расшифровка #{entry_id} не открывается — DIARY_ENCRYPTION_KEY был ротирован "
            "без re-encrypt."
        )
        return

    if not transcript.strip():
        await message.answer(f"Расшифровка #{entry_id} ещё не готова или STT не настроен.")
        return
    await message.answer(f"📝 <b>#{entry_id}</b>\n\n{transcript}")


async def _forget_entry(
    message: types.Message,
    user: User,
    entry_id: int,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        stmt = select(VoiceDiary).where(
            VoiceDiary.id == entry_id,
            VoiceDiary.owner_user_id == user.id,
        )
        try:
            entry = (await session.execute(stmt)).scalar_one()
        except NoResultFound:
            await message.answer(f"Запись #{entry_id} не найдена.")
            return
        await session.delete(entry)
        await session.commit()

    await message.answer(f"🗑 Запись #{entry_id} удалена.")

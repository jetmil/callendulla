# syntax=docker/dockerfile:1.7

# ─── Stage 1: builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# system deps for psycopg2, cryptography, opus (TTS), git (alembic)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libssl-dev \
        libffi-dev \
        libopus0 libopus-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# requirements.txt пока. Переход на pyproject.toml + uv/pip install -e .
# по мере стабилизации API.
COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# ─── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/bin:$PATH"

# runtime deps only — no compilers
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libopus0 \
        ffmpeg \
        ca-certificates \
        tini \
        supervisor \
    && rm -rf /var/lib/apt/lists/*

# non-root user with explicit UID 1000 (avoid noexec/permission surprises
# on bind-mounted volumes — see SECURITY.md hardening notes)
RUN groupadd -g 1000 callendulla \
    && useradd -u 1000 -g 1000 -m -s /bin/bash callendulla

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=callendulla:callendulla . /app/

# pre-compile bytecode for faster cold start
RUN python -m compileall -q /app/src

USER callendulla

EXPOSE 8290

# Entrypoint цепочка:
#   tini → entrypoint.sh → supervisord → (api, bot, scheduler)
# tini reaps зомби и форвардит сигналы; entrypoint.sh ждёт postgres
# и делает alembic upgrade ровно один раз перед стартом supervisord
# (иначе три параллельных процесса дерутся за DDL).
ENTRYPOINT ["/usr/bin/tini", "--", "/app/deploy/entrypoint.sh"]
CMD ["supervisord", "-c", "/app/deploy/supervisord.conf", "-n"]

# start-period 60s: миграции + cold-start uvicorn + bot getMe ≈ 20-40s,
# берём с запасом. Если у тебя медленная PostgreSQL — поднимай ещё.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8290/health', timeout=5)" || exit 1

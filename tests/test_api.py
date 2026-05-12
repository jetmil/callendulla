# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for the FastAPI application, with focus on AGPL §13 compliance.

Why so much weight on AGPL: §13 is the only legal hook this project
has against silent SaaS-style closed-source forks. The header
:data:`SOURCE_HEADER` is the user-facing handshake — if it disappears
or points at the wrong URL, the operator is technically out of
compliance. Tests here treat that as a hard contract.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from callendulla.api import create_app
from callendulla.api.middleware.agpl_source import SOURCE_HEADER
from callendulla.config import Settings, get_settings

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32
_DEFAULT_SOURCE_URL = "https://github.com/jetmil/callendulla"


def _minimal_env() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
        "ALLOWED_HOSTS": "*",
    }


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    data = _minimal_env()
    for key in (
        *data,
        "AGPL_SOURCE_URL",
        "CORS_ORIGINS",
        "BOT_MODE",
        "WEBHOOK_HOST",
        "WEBHOOK_PATH",
        "WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in data.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield data
    get_settings.cache_clear()


@pytest.fixture
def client(env: dict[str, str]) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


class TestHealth:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_health_carries_source_header(self, client: TestClient) -> None:
        """AGPL §13: even /health must announce source."""
        r = client.get("/health")
        assert r.headers.get(SOURCE_HEADER) == _DEFAULT_SOURCE_URL


class TestAGPLSourceHeader:
    """Hard contract: every response carries ``X-Source-URL``."""

    @pytest.mark.parametrize("path", ["/health", "/source"])
    def test_header_present(self, client: TestClient, path: str) -> None:
        r = client.get(path)
        assert SOURCE_HEADER in r.headers
        assert r.headers[SOURCE_HEADER] == _DEFAULT_SOURCE_URL

    def test_header_on_404(self, client: TestClient) -> None:
        """Even error responses must carry the source URL."""
        r = client.get("/does-not-exist")
        assert r.status_code == 404
        assert r.headers.get(SOURCE_HEADER) == _DEFAULT_SOURCE_URL

    def test_header_reflects_env_override(
        self,
        env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Forks set ``AGPL_SOURCE_URL`` — must propagate end-to-end."""
        monkeypatch.setenv("AGPL_SOURCE_URL", "https://gitlab.com/me/fork-of-callendulla")
        get_settings.cache_clear()
        with TestClient(create_app()) as c:
            r = c.get("/health")
        assert r.headers.get(SOURCE_HEADER) == "https://gitlab.com/me/fork-of-callendulla"


class TestSourceEndpoint:
    def test_returns_structured_meta(self, client: TestClient) -> None:
        r = client.get("/source")
        assert r.status_code == 200
        body = r.json()
        assert body["license"] == "AGPL-3.0-or-later"
        assert body["source_url"].rstrip("/") == _DEFAULT_SOURCE_URL
        # commit_sha is either a 40-char hex string or the literal "unknown"
        assert isinstance(body["commit_sha"], str)
        assert body["commit_sha"]
        # version comes from _version.py
        assert isinstance(body["version"], str)
        assert "build_date" in body


class TestTrustedHost:
    """Host header allowlist is enforced when configured."""

    def test_wildcard_accepts_any(self, client: TestClient) -> None:
        # Default fixture has ALLOWED_HOSTS="*" → any Host wins.
        r = client.get("/health", headers={"Host": "evil.example.com"})
        assert r.status_code == 200

    def test_specific_host_rejects_others(
        self,
        env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ALLOWED_HOSTS", "callendulla.example.com")
        get_settings.cache_clear()
        with TestClient(create_app(), base_url="http://callendulla.example.com") as c:
            ok = c.get("/health")
            assert ok.status_code == 200
            bad = c.get("/health", headers={"Host": "attacker.example.com"})
            assert bad.status_code == 400


class TestCORS:
    """CORS is closed by default. Browser requests from a foreign origin
    must not see ``Access-Control-Allow-Origin``."""

    def test_no_cors_header_when_origins_empty(self, client: TestClient) -> None:
        r = client.options(
            "/health",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # When CORS is closed, Starlette returns 400 (preflight not
        # accepted) and never sets the ACA-Origin header.
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}

    def test_origin_allowed_when_listed(
        self,
        env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
        get_settings.cache_clear()
        with TestClient(create_app()) as c:
            r = c.options(
                "/health",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        # Starlette CORS responds 200 with ACA-Origin on a valid preflight.
        assert r.headers.get("access-control-allow-origin") == "https://app.example.com"


class TestFactoryAcceptsExplicitSettings:
    def test_passing_settings_bypasses_singleton(
        self,
        env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # build a Settings explicitly with a one-off source url
        monkeypatch.setenv("AGPL_SOURCE_URL", "https://example.com/explicit")
        get_settings.cache_clear()
        s = Settings()  # type: ignore[call-arg]
        with TestClient(create_app(settings=s)) as c:
            r = c.get("/health")
        assert r.headers.get(SOURCE_HEADER) == "https://example.com/explicit"


class TestLazyApp:
    def test_module_app_singleton(self, env: dict[str, str]) -> None:
        # Access twice — must return the same instance via PEP 562 hook.
        import callendulla.api.app as module  # noqa: PLC0415

        first = module.app  # type: ignore[attr-defined]
        second = module.app  # type: ignore[attr-defined]
        assert first is second

    def test_module_unknown_attribute_raises(self) -> None:
        import callendulla.api.app as module  # noqa: PLC0415

        with pytest.raises(AttributeError):
            _ = module.nonexistent_attribute  # type: ignore[attr-defined]

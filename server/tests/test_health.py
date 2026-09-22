"""The health endpoint reports what the server is missing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_demo.config import get_settings
from jev_demo.main import app
from jev_demo.services import token_store


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the server at a throwaway home directory and a stubbed keychain."""
    monkeypatch.setenv("JEV_DEMO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(token_store, "load", lambda: None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_health_reports_missing_auth_and_key() -> None:
    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body == {
        "status": "ok",
        "gmail_authenticated": False,
        "jev_key_present": False,
        "keychain_backend": "keyring.backends.macOS.Keyring",
    }


def test_health_sees_a_stored_token_and_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.setattr(token_store, "load", lambda: '{"refresh_token": "fake"}')
    get_settings.cache_clear()

    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body["gmail_authenticated"] is True
    assert body["jev_key_present"] is True


def test_startup_creates_the_home_directory() -> None:
    with TestClient(app):
        assert get_settings().home_dir.is_dir()


def test_startup_refuses_a_machine_with_no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.fail.Keyring")

    with pytest.raises(token_store.KeychainUnavailable), TestClient(app):
        pass


def test_health_stays_up_when_the_keychain_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode() -> str:
        raise token_store.KeychainUnavailable("locked")

    monkeypatch.setattr(token_store, "load", explode)

    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body["gmail_authenticated"] is False

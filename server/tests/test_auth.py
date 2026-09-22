"""Loading, refreshing, and reporting the Gmail token."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from jev_demo.config import get_settings
from jev_demo.main import app
from jev_demo.services import gmail_auth, token_store


def _naive_utc_now() -> datetime:
    """Now, with no timezone, which is the shape google-auth stores expiry in."""
    return datetime.now(UTC).replace(tzinfo=None)


def _token_json(expires_in: timedelta) -> str:
    """An authorized user token that expires the given distance from now."""
    return json.dumps(
        {
            "token": "access-token",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": gmail_auth.SCOPES,
            "expiry": (_naive_utc_now() + expires_in).isoformat(),
        }
    )


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """A stubbed keychain holding one slot, so tests can watch what lands in it."""
    slot: dict[str, str | None] = {"token": None}
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(token_store, "load", lambda: slot["token"])
    monkeypatch.setattr(token_store, "save", lambda value: slot.__setitem__("token", value))
    monkeypatch.setattr(token_store, "delete", lambda: slot.__setitem__("token", None))
    return slot


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the repo root and the real home directory out of these tests."""
    monkeypatch.setenv("JEV_DEMO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JEV_DEMO_CREDENTIALS", str(tmp_path / "credentials.json"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_no_stored_token_means_no_credentials(keychain: dict[str, str | None]) -> None:
    assert gmail_auth.load_credentials() is None


def test_a_live_token_is_used_as_is(keychain: dict[str, str | None]) -> None:
    keychain["token"] = _token_json(timedelta(hours=1))

    credentials = gmail_auth.load_credentials()

    assert credentials is not None
    assert credentials.token == "access-token"


def test_an_expired_token_is_refreshed_and_written_back(
    keychain: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain["token"] = _token_json(-timedelta(hours=1))

    def refresh(self: Credentials, request: object) -> None:
        self.token = "fresh-access-token"
        self.expiry = _naive_utc_now() + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)

    credentials = gmail_auth.load_credentials()

    assert credentials is not None
    assert credentials.token == "fresh-access-token"
    assert json.loads(keychain["token"] or "{}")["token"] == "fresh-access-token"


def test_a_revoked_token_is_dropped(
    keychain: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain["token"] = _token_json(-timedelta(hours=1))

    def refresh(self: Credentials, request: object) -> None:
        raise RefreshError("Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", refresh)

    assert gmail_auth.load_credentials() is None
    assert keychain["token"] is None


def test_status_says_no_when_nothing_is_stored(keychain: dict[str, str | None]) -> None:
    with TestClient(app) as client:
        assert client.get("/auth/status").json() == {"authenticated": False, "email": None}


def test_status_reports_the_mailbox(
    keychain: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain["token"] = _token_json(timedelta(hours=1))
    monkeypatch.setattr(gmail_auth, "account_email", lambda credentials=None: "me@example.com")

    with TestClient(app) as client:
        body = client.get("/auth/status").json()

    assert body == {"authenticated": True, "email": "me@example.com"}


def test_login_without_a_client_file_names_the_missing_path(
    keychain: dict[str, str | None],
) -> None:
    with TestClient(app) as client:
        response = client.post("/auth/login")

    assert response.status_code == 400
    assert "credentials.json" in response.json()["detail"]


def test_login_stores_the_token_it_gets_back(
    keychain: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    get_settings().credentials_path.write_text("{}")
    fresh = _token_json(timedelta(hours=1))

    class FakeFlow:
        def run_local_server(self, port: int) -> Credentials:
            return Credentials.from_authorized_user_info(json.loads(fresh), gmail_auth.SCOPES)

    monkeypatch.setattr(
        gmail_auth.InstalledAppFlow,
        "from_client_secrets_file",
        classmethod(lambda cls, path, scopes: FakeFlow()),
    )
    monkeypatch.setattr(gmail_auth, "account_email", lambda credentials=None: "me@example.com")

    with TestClient(app) as client:
        body = client.post("/auth/login").json()

    assert body == {"authenticated": True, "email": "me@example.com"}
    assert json.loads(keychain["token"] or "{}")["refresh_token"] == "refresh-token"


def test_a_locked_keychain_reports_unauthenticated_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode() -> str:
        raise token_store.KeychainUnavailable("locked")

    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(token_store, "load", explode)

    with TestClient(app) as client:
        assert client.get("/auth/status").json()["authenticated"] is False

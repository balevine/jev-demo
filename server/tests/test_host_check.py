"""The server only answers to names that mean this machine.

Nothing here asks for a password, so the Host header is what separates a
request somebody made on this machine from one a page in a browser made by
pointing a name it owns at 127.0.0.1.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_demo.config import LOOPBACK_HOSTS, get_settings
from jev_demo.main import _host_only, app
from jev_demo.services import token_store


@pytest.fixture(autouse=True)
def loopback_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The defaults the server ships with, rather than the one tests usually get."""
    monkeypatch.setenv("JEV_DEMO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JEV_DEMO_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(token_store, "chained_names", list)
    monkeypatch.setattr(token_store, "load", lambda: None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8000", "localhost:8000", "[::1]:8000"])
def test_this_machine_is_answered(host: str) -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Host": host})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    ["attacker.example.com", "attacker.example.com:8000", "", "127.0.0.1.attacker.example.com"],
    ids=["a-name", "a-name-with-a-port", "nothing", "a-name-ending-in-the-address"],
)
def test_anything_else_is_turned_away(host: str) -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Host": host})

    assert response.status_code == 400
    assert "does not answer to the name" in response.json()["detail"]


def test_a_write_from_a_foreign_name_never_reaches_gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check runs before anything else, so a refused request touches nothing."""
    from jev_demo.services import gmail_client

    def explode(*arguments: object, **keywords: object) -> None:
        raise AssertionError("Gmail was reached by a request that should have been refused.")

    monkeypatch.setattr(gmail_client, "require_user_labels", explode)
    monkeypatch.setattr(gmail_client, "apply_labels", explode)

    with TestClient(app) as client:
        response = client.post(
            "/apply",
            json={"thread_id": "18f", "label_ids": ["Label_12"]},
            headers={"Host": "attacker.example.com"},
        )

    assert response.status_code == 400


def test_the_shipped_default_is_loopback_and_nothing_else() -> None:
    assert get_settings().allowed_hosts == LOOPBACK_HOSTS


def test_the_allowed_names_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_DEMO_ALLOWED_HOSTS", "jev.internal, 10.0.0.4")
    get_settings.cache_clear()

    assert get_settings().allowed_hosts == frozenset({"jev.internal", "10.0.0.4"})


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("localhost", "localhost"),
        ("[::1]:8000", "::1"),
        ("[::1]", "::1"),
        ("", ""),
    ],
)
def test_the_port_is_dropped_without_mangling_an_ipv6_address(header: str, expected: str) -> None:
    assert _host_only(header) == expected

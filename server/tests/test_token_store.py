"""The token store refuses anything that is not a real keychain."""

from __future__ import annotations

import pytest

from jev_demo.services import token_store


@pytest.mark.parametrize(
    "backend",
    [
        "keyring.backends.fail.Keyring",
        "keyring.backends.null.Keyring",
        "keyrings.alt.file.PlaintextKeyring",
        "keyrings.alt.file.EncryptedKeyring",
    ],
)
def test_plaintext_and_missing_backends_are_rejected(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(token_store, "backend_name", lambda: backend)

    assert token_store.available() is False
    with pytest.raises(token_store.KeychainUnavailable):
        token_store.require_backend()


@pytest.mark.parametrize(
    "backend",
    [
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
        "keyring.backends.SecretService.Keyring",
    ],
)
def test_real_backends_are_accepted(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "backend_name", lambda: backend)

    assert token_store.available() is True
    token_store.require_backend()


def test_load_and_save_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(
        token_store.keyring,
        "set_password",
        lambda service, account, value: stored.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        token_store.keyring,
        "get_password",
        lambda service, account: stored.get((service, account)),
    )

    assert token_store.load() is None
    token_store.save('{"refresh_token": "fake"}')

    assert token_store.load() == '{"refresh_token": "fake"}'
    assert stored == {(token_store.SERVICE, token_store.ACCOUNT): '{"refresh_token": "fake"}'}

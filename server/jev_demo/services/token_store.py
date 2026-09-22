"""The Gmail OAuth token, kept in the OS keychain.

The token is a refresh token for a mailbox, so it never touches disk. There is no
file fallback. If the machine has no real keychain the server says so and stops,
because a silent fallback would put a long lived credential in a plaintext file
without the user knowing.
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

SERVICE = "jev-demo"
ACCOUNT = "gmail-oauth-token"

# Backends that either do nothing or write plaintext on disk. The keyrings.alt
# package is the plaintext and file based family, which defeats the point of
# using a keychain at all.
_REJECTED_BACKENDS = ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")


class KeychainUnavailable(RuntimeError):
    """Raised when there is no keychain worth trusting on this machine."""


def backend_name() -> str:
    """The dotted name of the backend keyring picked, for error messages."""
    return _name(keyring.get_keyring())


def chained_names() -> list[str]:
    """The backends a chainer would write through, in the order it would try them.

    Empty for an ordinary backend. A chainer is not one keychain but a list of
    them, and `set_password` walks that list and writes to the first that does
    not refuse, so the name of the chainer itself says nothing about where a
    token would land.
    """
    return [_name(chained) for chained in getattr(keyring.get_keyring(), "backends", [])]


def available() -> bool:
    """Whether a real keychain is present, and nothing plaintext sits behind it.

    Every backend a write could reach is checked, not just the one keyring
    named. A chainer passes its own name check and would still hand the token
    to a plaintext file further down the chain.
    """
    return not any(name.startswith(_REJECTED_BACKENDS) for name in _reachable())


def require_backend() -> None:
    """Stop with an explanation when no usable keychain is present."""
    if available():
        return
    raise KeychainUnavailable(
        f"No usable OS keychain was found (keyring selected {' then '.join(_reachable())}). "
        "jev-demo stores your Gmail token in the keychain and never on disk. "
        "On macOS and Windows this works out of the box. On Linux, install and "
        "run a Secret Service provider such as gnome-keyring or KeePassXC, and "
        "uninstall keyrings.alt if it is present."
    )


def _reachable() -> list[str]:
    """Every backend a write could end up in, starting with the one keyring named."""
    return [backend_name(), *chained_names()]


def _name(backend: object) -> str:
    """The dotted name of one backend."""
    kind = type(backend)
    return f"{kind.__module__}.{kind.__qualname__}"


def load() -> str | None:
    """Return the stored token JSON, or None when consent has not happened yet."""
    require_backend()
    try:
        return keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError as exc:
        raise KeychainUnavailable(f"Could not read from the keychain. {exc}") from exc


def save(token_json: str) -> None:
    """Store the token JSON, replacing whatever was there."""
    require_backend()
    try:
        keyring.set_password(SERVICE, ACCOUNT, token_json)
    except KeyringError as exc:
        raise KeychainUnavailable(f"Could not write to the keychain. {exc}") from exc


def delete() -> None:
    """Remove the stored token. Quiet when there is nothing to remove."""
    require_backend()
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass
    except KeyringError as exc:
        raise KeychainUnavailable(f"Could not delete from the keychain. {exc}") from exc


def has_token() -> bool:
    """Whether a token is stored. False when the keychain is unusable."""
    try:
        return load() is not None
    except KeychainUnavailable:
        return False

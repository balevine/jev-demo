"""Paths, environment, and settings.

The label descriptions live in ~/.jev-demo. The Google OAuth client and the two
Jinja templates live in the repo, because they are authored by hand. The OAuth
token is not a path at all. It lives in the OS keychain, handled in
services/token_store.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_HOME = Path.home() / ".jev-demo"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

# Dollars per million input tokens. Output tokens are free.
JEV_INPUT_TOKEN_COST = 0.042

DEFAULT_THRESHOLD = 0.5

# The names this server will answer to. It has live access to a mailbox and no
# authentication of its own, so it only takes requests addressed to this
# machine. A browser will happily send a page's request to 127.0.0.1 if a name
# that page controls resolves there, and that request counts as same origin, so
# no CORS rule would stop it. Checking the name it was addressed by does.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def _hosts_from_env(name: str, default: frozenset[str]) -> frozenset[str]:
    """A comma separated host list from the environment, or the default."""
    value = os.environ.get(name)
    if not value:
        return default
    return frozenset(entry.strip() for entry in value.split(",") if entry.strip())


@dataclass(frozen=True)
class Settings:
    """Resolved paths and environment for one server process."""

    home_dir: Path
    credentials_path: Path
    state_template: Path
    questions_template: Path
    typesafe_api_key: str | None
    allowed_hosts: frozenset[str]

    @property
    def descriptions_path(self) -> Path:
        """Label ID to description, edited through the settings screen."""
        return self.home_dir / "GMAIL_LABEL_DESCRIPTIONS.json"

    def ensure_home_dir(self) -> None:
        """Create ~/.jev-demo if it is not there yet."""
        self.home_dir.mkdir(mode=0o700, parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read settings from the environment once per process."""
    home_dir = _path_from_env("JEV_DEMO_HOME", DEFAULT_HOME)
    return Settings(
        home_dir=home_dir,
        credentials_path=_path_from_env("JEV_DEMO_CREDENTIALS", REPO_ROOT / "credentials.json"),
        state_template=_path_from_env("JEV_DEMO_STATE_TEMPLATE", REPO_ROOT / "STATE.json"),
        questions_template=_path_from_env(
            "JEV_DEMO_QUESTIONS_TEMPLATE", REPO_ROOT / "QUESTIONS.json"
        ),
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY") or None,
        allowed_hosts=_hosts_from_env("JEV_DEMO_ALLOWED_HOSTS", LOOPBACK_HOSTS),
    )

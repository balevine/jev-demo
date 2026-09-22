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


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class Settings:
    """Resolved paths and environment for one server process."""

    home_dir: Path
    credentials_path: Path
    state_template: Path
    questions_template: Path
    typesafe_api_key: str | None

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
    )

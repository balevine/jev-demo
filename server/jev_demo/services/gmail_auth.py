"""Google OAuth for Gmail.

One scope, `gmail.modify`, which covers `labels.list`, `threads.get`, and
`threads.modify`. Consent runs once in a browser. The token that comes back goes
straight into the OS keychain through token_store, and is refreshed in place from
there on every load, so a restart never opens a browser again.

This module also builds the Gmail API client, so there is exactly one place that
turns a stored token into something that can call Gmail.
"""

from __future__ import annotations

import json

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from jev_demo.config import get_settings
from jev_demo.services import token_store

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class AuthError(RuntimeError):
    """Base for the things that can be wrong with the Google credentials."""


class CredentialsFileMissing(AuthError):
    """The Google OAuth client file is not where the server expects it."""


class NotAuthenticated(AuthError):
    """No usable Gmail token is stored, so consent has to run."""


def load_credentials() -> Credentials | None:
    """Return live credentials from the keychain, or None when there are none.

    An expired access token is refreshed here and the refreshed token is written
    back, so the caller always gets something it can use. A token Google has
    revoked is dropped, because it will never work again and keeping it would
    only make the next login look like it failed.
    """
    raw = token_store.load()
    if raw is None:
        return None

    credentials = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if credentials.valid:
        return credentials
    if not credentials.refresh_token:
        token_store.delete()
        return None

    try:
        credentials.refresh(Request())
    except RefreshError:
        token_store.delete()
        return None

    token_store.save(credentials.to_json())
    return credentials


def log_in() -> Credentials:
    """Run the browser consent flow and store the resulting token."""
    settings = get_settings()
    if not settings.credentials_path.is_file():
        raise CredentialsFileMissing(
            f"No Google OAuth client at {settings.credentials_path}. Create an "
            "OAuth client of type Desktop app in Google Cloud, download it, and "
            "save it there. The README has the full walkthrough."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(settings.credentials_path), SCOPES)
    credentials = flow.run_local_server(port=0)
    token_store.save(credentials.to_json())
    return credentials


def require_credentials() -> Credentials:
    """Return live credentials, or say plainly that nobody has logged in yet."""
    credentials = load_credentials()
    if credentials is None:
        raise NotAuthenticated(
            "Gmail is not connected yet. Run `jev auth`, or POST /auth/login, to "
            "give the server access to your mailbox."
        )
    return credentials


def gmail_service(credentials: Credentials | None = None) -> Resource:
    """Build the Gmail API client, logging in first if that has not happened."""
    return build(
        "gmail",
        "v1",
        credentials=credentials or require_credentials(),
        cache_discovery=False,
    )


def account_email(credentials: Credentials | None = None) -> str:
    """The address of the mailbox the stored token belongs to."""
    profile = gmail_service(credentials).users().getProfile(userId="me").execute()
    return profile["emailAddress"]

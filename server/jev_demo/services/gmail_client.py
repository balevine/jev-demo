"""Reading Gmail.

Labels come from `labels.list`, and only the ones the user made are kept.
Gmail's own labels are not worth asking about. Nobody needs a model to decide
whether a thread belongs in INBOX.

Threads take two steps, because `threads.list` hands back IDs and nothing
else. The `threads.get` calls that follow run in a small pool, since a run of
25 threads is 25 round trips with no reason to wait on each other.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from jev_demo.schemas import Label, Thread, ThreadSelection
from jev_demo.services import descriptions, gmail_auth, parse

USER_LABEL = "user"

FETCH_WORKERS = 8

GMAIL_DATE = "%Y/%m/%d"


def list_labels(service: Resource | None = None) -> list[Label]:
    """Every label the user made, sorted by name, with its stored description.

    The description is joined on here rather than by each caller, because a
    label without one asks a weaker question and nothing would look wrong if a
    caller forgot.
    """
    service = service or gmail_auth.gmail_service()
    response = service.users().labels().list(userId="me").execute()
    labels = [
        Label(id=entry["id"], name=entry["name"])
        for entry in response.get("labels", [])
        if entry.get("type") == USER_LABEL
    ]
    return descriptions.describe(sorted(labels, key=lambda label: label.name.lower()))


def build_query(selection: ThreadSelection) -> str:
    """Turn a thread selection into Gmail search syntax.

    How many threads come back is `maxResults` rather than a search term, so it
    is not part of this.
    """
    terms: list[str] = []

    if selection.since:
        terms.append(f"after:{selection.since:{GMAIL_DATE}}")

    span = selection.date_range()
    if span:
        start, end = span
        terms.append(f"after:{start:{GMAIL_DATE}}")
        # Gmail reads before: as strictly earlier, so asking for the day after
        # the end of the range is what makes the range include its last day.
        terms.append(f"before:{end + timedelta(days=1):{GMAIL_DATE}}")

    if selection.query:
        terms.append(selection.query.strip())

    # Chats are stored as threads and read as nonsense, so they never count.
    terms.append("-in:chats")
    return " ".join(term for term in terms if term)


def list_thread_ids(service: Resource, selection: ThreadSelection) -> list[str]:
    """The IDs of the matching threads, newest first."""
    response = (
        service.users()
        .threads()
        .list(userId="me", q=build_query(selection), maxResults=selection.last)
        .execute()
    )
    return [entry["id"] for entry in response.get("threads", [])]


def get_thread(service: Resource, thread_id: str) -> dict:
    """One thread with every message in full."""
    return service.users().threads().get(userId="me", id=thread_id, format="full").execute()


def fetch_threads(selection: ThreadSelection) -> list[Thread]:
    """List the matching threads and fetch each one in full."""
    credentials = gmail_auth.require_credentials()
    service = gmail_auth.gmail_service(credentials)

    thread_ids = list_thread_ids(service, selection)
    if not thread_ids:
        return []

    return [parse.parse_thread(raw) for raw in _get_many(credentials, thread_ids)]


def apply_labels(thread_id: str, label_ids: list[str], service: Resource | None = None) -> None:
    """Add labels to one thread.

    Only `addLabelIds` is ever sent. This app never removes a label and never
    creates one, so a thread it has already labeled can be run again without
    anything happening twice.
    """
    service = service or gmail_auth.gmail_service()
    (
        service.users()
        .threads()
        .modify(userId="me", id=thread_id, body={"addLabelIds": label_ids})
        .execute()
    )


def _get_many(credentials: Credentials, thread_ids: list[str]) -> list[dict]:
    """Fetch full threads in parallel, in the order they were asked for.

    Each worker builds its own Gmail client. The httplib2 transport underneath
    is not safe to share between threads, and one client per worker is the
    documented way around that.
    """
    local = threading.local()

    def fetch(thread_id: str) -> dict:
        service = getattr(local, "service", None)
        if service is None:
            service = gmail_auth.gmail_service(credentials)
            local.service = service
        return get_thread(service, thread_id)

    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(thread_ids))) as pool:
        return list(pool.map(fetch, thread_ids))

"""Building the Gmail payloads the tests read, and a Gmail to read them from.

Gmail message bodies are base64url with the padding stripped, and headers are
a list of name and value pairs. Writing that by hand in every test buries the
thing each test is actually about.
"""

from __future__ import annotations

import base64


def encode(text: str) -> str:
    """The base64url Gmail hands back, padding stripped as Gmail strips it."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def headers(**values: str) -> list[dict[str, str]]:
    """Gmail's header list, written as keyword arguments. `from_` becomes `From`."""
    return [
        {"name": name.rstrip("_").replace("_", "-").title(), "value": value}
        for name, value in values.items()
    ]


def part(mime_type: str, text: str, filename: str = "") -> dict:
    """One leaf of a MIME tree."""
    return {"mimeType": mime_type, "filename": filename, "body": {"data": encode(text)}}


def text_payload(body: str, **header_values: str) -> dict:
    """A plain text message payload."""
    return {
        "mimeType": "text/plain",
        "filename": "",
        "headers": headers(**header_values),
        "body": {"data": encode(body)},
    }


def message(payload: dict, internal_date: str = "1789740131000", snippet: str = "") -> dict:
    """A Gmail message around a payload. The default date is 2026-09-18T14:02:11Z."""
    return {"internalDate": internal_date, "payload": payload, "snippet": snippet}


def thread(thread_id: str, subject: str, body: str, sender: str = "ada@example.com") -> dict:
    """A one message thread, as threads.get returns it."""
    return {
        "id": thread_id,
        "messages": [
            message(
                text_payload(body, from_=sender, subject=subject),
                snippet=body[:20],
            )
        ],
    }


class _Call:
    """What a Gmail request object does, which is hold a result until executed."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def execute(self) -> dict:
        return self._result


class FakeGmail:
    """The slice of the Gmail client this code touches."""

    def __init__(self, labels: list[dict], threads: list[dict]) -> None:
        self.labels_payload = labels
        self.threads_by_id = {entry["id"]: entry for entry in threads}
        self.list_arguments: dict[str, object] = {}
        self.fetched: list[str] = []
        self.modified: list[tuple[str, dict]] = []

    def users(self) -> FakeGmail:
        return self

    def labels(self) -> FakeGmail:
        return self

    def threads(self) -> FakeGmail:
        return self

    def list(self, userId: str, q: str | None = None, maxResults: int | None = None) -> _Call:
        if q is None:
            return _Call({"labels": self.labels_payload})
        self.list_arguments = {"q": q, "maxResults": maxResults}
        return _Call({"threads": [{"id": key} for key in self.threads_by_id]})

    def get(self, userId: str, id: str, format: str) -> _Call:
        assert format == "full"
        self.fetched.append(id)
        return _Call(self.threads_by_id[id])

    def modify(self, userId: str, id: str, body: dict) -> _Call:
        self.modified.append((id, body))
        return _Call({"id": id})

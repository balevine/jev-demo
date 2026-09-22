"""Asking Jev.

There is one endpoint and one request per thread. Every label rides in that
request as its own noul question, because questions sent together are answered
together against one state. Asking them one at a time would cost more and take
longer for the same answers.

A noul is a probability from 0 to 1 rather than a yes or a no, and a missing
one is not a zero. It is `float | None` here so a question Jev did not answer
stays apart from a question it answered with a zero. Reading one as the other
would quietly skip a label, or quietly apply one.

Parts of the API are undocumented, so nothing here leans on them. A failure
carries the raw response body rather than reaching into it, and no rate limit
header is read.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from jev_demo.config import JEV_API_URL, JEV_MODEL, get_settings
from jev_demo.schemas import Usage
from jev_demo.services.parse import truncate

QUESTION_TYPE = "noul"

# How long one attempt gets, and how the waits between attempts grow. These
# match the official TypeSafe clients.
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 2
FIRST_BACKOFF = 0.5
LONGEST_BACKOFF = 5.0
JITTER = 0.25

# Retry the statuses that mean "not now". Anything else is an answer about the
# request itself, and sending it again would only get it back.
RETRY_STATUSES = frozenset({408, 429})

# The longest wait a response is allowed to ask for. Past this it is better to
# fail and let the run move on.
LONGEST_RETRY_AFTER = 60.0

# How much of an error body goes in the message. The whole thing stays on the
# exception.
BODY_EXCERPT_CHARS = 2_000

_HINTS = {
    401: "Check that TYPESAFE_API_KEY is right.",
    403: "Check that TYPESAFE_API_KEY is right.",
    429: "That is the rate limit, and the retries are spent.",
}

# Named here so a test can walk the retry path without waiting on it.
_sleep = asyncio.sleep


class KeyMissing(RuntimeError):
    """Raised when there is no TYPESAFE_API_KEY to ask with."""


class JevError(RuntimeError):
    """Raised when a request does not come back with answers.

    The error body has no documented shape, so the raw text is carried along
    rather than picked apart.
    """

    def __init__(self, status_code: int, body: str, problem: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(" ".join(self._parts(status_code, body, problem)))

    @staticmethod
    def _parts(status_code: int, body: str, problem: str) -> list[str]:
        parts = [problem or f"Jev answered {status_code}."]
        hint = _HINTS.get(status_code)
        if hint:
            parts.append(hint)
        excerpt = truncate(body.strip(), BODY_EXCERPT_CHARS)
        if excerpt:
            parts.append(excerpt)
        return parts


class Answer(BaseModel):
    """One answer to one noul question.

    `noul` stays None when the answer arrives without one, which is not the
    same as arriving with a zero.
    """

    type: str = QUESTION_TYPE
    noul: float | None = None


class JevResponse(BaseModel):
    """One set of answers, keyed by the question IDs that were sent."""

    answers: dict[str, Answer]
    usage: Usage = Field(default_factory=Usage)


class JevClient:
    """One connection to Jev, reused for every thread in a run."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def ask(self, state: Any, questions: dict[str, Any]) -> JevResponse:
        """Send one state and every question about it, and return the answers."""
        body = {"state": state, "model": JEV_MODEL, "questions": questions}
        attempt = 0
        while True:
            response = await self._http.post(JEV_API_URL, json=body)
            if response.is_success:
                return _read(response)
            if attempt == MAX_RETRIES or not _retryable(response.status_code):
                raise JevError(response.status_code, response.text)
            await _sleep(retry_delay(response, attempt))
            attempt += 1


@asynccontextmanager
async def client() -> AsyncIterator[JevClient]:
    """A Jev client for the length of one run."""
    headers = {"Authorization": f"Bearer {require_key()}"}
    async with httpx.AsyncClient(headers=headers, timeout=REQUEST_TIMEOUT) as http:
        yield JevClient(http)


def require_key() -> str:
    """The API key, or a message saying where to put one."""
    key = get_settings().typesafe_api_key
    if not key:
        raise KeyMissing(
            "TYPESAFE_API_KEY is not set, and Jev cannot answer anything without it. "
            "Put it in the .env file at the repo root, or export it before starting "
            "the server."
        )
    return key


def retry_delay(response: httpx.Response, attempt: int) -> float:
    """How long to wait before trying again, in seconds.

    A response that asks for a wait gets it, as long as what it asks for is
    sane. Otherwise the wait doubles per attempt up to a ceiling.
    """
    asked = _retry_after(response.headers)
    return asked if asked is not None else _backoff(attempt)


def _retryable(status_code: int) -> bool:
    """Whether a status is worth sending the same request again."""
    return status_code in RETRY_STATUSES or status_code >= 500


def _backoff(attempt: int) -> float:
    """The wait for one attempt, jittered so parallel threads do not line up.

    The jitter only ever shortens the wait, so the ceiling stays a ceiling.
    """
    return min(FIRST_BACKOFF * 2**attempt, LONGEST_BACKOFF) * (1 - JITTER * random.random())


def _retry_after(headers: httpx.Headers) -> float | None:
    """What the response asked us to wait, when it asked for something usable.

    Both spellings are read. `retry-after-ms` is milliseconds, and
    `retry-after` is either seconds or a date to wait until.
    """
    for name, per_second in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is None:
            continue
        seconds = _seconds(raw, per_second)
        if seconds is not None and 0 < seconds <= LONGEST_RETRY_AFTER:
            return seconds
    return None


def _seconds(raw: str, per_second: float) -> float | None:
    """Read a header as a count, or as the date it wants us to wait until."""
    try:
        return float(raw) / per_second
    except ValueError:
        pass
    try:
        until = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return (until - datetime.now(UTC)).total_seconds()


def _read(response: httpx.Response) -> JevResponse:
    """Parse an answered request, and say so plainly when it cannot be read."""
    try:
        return JevResponse.model_validate_json(response.content)
    except ValidationError as exc:
        raise JevError(
            response.status_code,
            response.text,
            f"Jev answered {response.status_code}, but with a body this code "
            f"could not read. {exc.errors()[0]['msg']}.",
        ) from exc

"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator

from jev_demo.config import DEFAULT_THRESHOLD

DEFAULT_LAST = 25
MAX_LAST = 100

RANGE_SEPARATOR = ".."
RANGE_EXAMPLE = f"A date range looks like 2026-01-01{RANGE_SEPARATOR}2026-02-01."


class HealthResponse(BaseModel):
    """Liveness, plus whether the things the server needs are present."""

    status: str
    gmail_authenticated: bool
    jev_key_present: bool
    keychain_backend: str


class AuthStatusResponse(BaseModel):
    """Whether Gmail is connected, and to which mailbox."""

    authenticated: bool
    email: str | None = None


class Label(BaseModel):
    """One label the user made, with whatever description is stored for it."""

    id: str
    name: str
    description: str = ""


class Usage(BaseModel):
    """What a request cost. Input tokens are billed and output tokens are free."""

    input_tokens: int = 0


class Message(BaseModel):
    """One message, cut down to the fields a question can be asked about."""

    sender: str
    recipient: str
    date: str
    body: str
    has_attachments: bool


class Thread(BaseModel):
    """One Gmail thread, ready to be rendered into a Jev state."""

    id: str
    subject: str
    sender: str
    date: str
    snippet: str
    messages: list[Message]

    @property
    def message_count(self) -> int:
        """How many messages the thread carries."""
        return len(self.messages)


class ThreadSelection(BaseModel):
    """Which threads to work on.

    `last` caps how many come back. `since` and `between` narrow the window,
    and `query` adds raw Gmail search terms on top of whatever the rest
    produced.
    """

    last: int = Field(default=DEFAULT_LAST, ge=1, le=MAX_LAST)
    since: date | None = None
    between: str | None = None
    query: str | None = None

    @field_validator("between")
    @classmethod
    def _check_between(cls, value: str | None) -> str | None:
        if value is not None:
            _split_range(value)
        return value

    def date_range(self) -> tuple[date, date] | None:
        """The first and last day of `between`, or None when it is unset."""
        return _split_range(self.between) if self.between else None


class ClassifyRequest(ThreadSelection):
    """Which threads to classify, how sure Jev has to be, and whether to write.

    `apply` is off by default. Nothing reaches Gmail until somebody asks for it.
    """

    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)
    apply: bool = False


class LabelScore(BaseModel):
    """What Jev thought of one label for one thread.

    `noul` stays None when Jev did not answer that question, which is not the
    same as answering zero. Only a real number at or above the threshold sets
    `apply`.
    """

    id: str
    name: str
    noul: float | None
    apply: bool


class ThreadResult(BaseModel):
    """One line of a classify stream, written as soon as that thread is answered."""

    thread_id: str
    subject: str
    sender: str
    date: str
    snippet: str
    labels: list[LabelScore]
    applied: bool
    usage: Usage
    error: str | None = None


class RunTotals(BaseModel):
    """What a whole run came to."""

    threads: int
    input_tokens: int
    cost_usd: float


class RunSummary(BaseModel):
    """The last line of a classify stream, told apart from the rest by its one key."""

    totals: RunTotals


class ApplyRequest(BaseModel):
    """Labels to add to one thread. Nothing here can remove one."""

    thread_id: str
    label_ids: list[str] = Field(min_length=1)


class ApplyResponse(BaseModel):
    """What was written to a thread."""

    thread_id: str
    applied: list[str]


def _split_range(value: str) -> tuple[date, date]:
    """Read `a..b` as two dates, refusing anything that is not one."""
    start, separator, end = value.partition(RANGE_SEPARATOR)
    if not separator:
        raise ValueError(RANGE_EXAMPLE)
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError(RANGE_EXAMPLE) from exc
    if last < first:
        raise ValueError("A date range has to end on or after the day it starts.")
    return first, last

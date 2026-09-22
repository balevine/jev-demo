"""Turning a raw Gmail thread into the few fields a question can be asked about.

A message arrives as a MIME tree. This walks it, prefers `text/plain`, falls
back to `text/html` with the tags taken out, and notes whether anything was
attached without keeping the filenames. Jev cannot open an attachment, so a
filename would be context spent for nothing.

Quoted history is dropped and long threads are trimmed for the same reason.
Jev allows 32k tokens for the state plus the longest question, and a thread
that quotes itself on every reply is the fastest way to fill that with
repetition.

Gmail header lookup lives here and only here, so every caller reads a header
the same way.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape

from jev_demo.schemas import Message, Thread

# Per message, then for the whole state. A state is the messages that survive
# plus a little JSON around them. At the usual four characters per token,
# 48,000 characters is around 12,000 tokens, which still leaves room inside the
# 32k Jev allows for the state and the longest question together.
MAX_BODY_CHARS = 8_000
MAX_STATE_CHARS = 48_000

TRUNCATION_MARK = "…[truncated]"

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_LINE_BREAK = re.compile(r"<(br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_HORIZONTAL_SPACE = re.compile(r"[ \t\xa0]+")
_BLANK_RUN = re.compile(r"\n{3,}")

# The attribution line Gmail and most other clients put above quoted history.
# It wraps often, so the match is allowed to run past a newline.
_ATTRIBUTION = re.compile(r"^On\b.{0,400}?\bwrote:[ \t]*$", re.MULTILINE | re.DOTALL)
_QUOTED_LINE = re.compile(r"^\s*>")


def header(headers: Iterable[dict[str, str]], name: str) -> str:
    """The value of one Gmail header, matched without regard to case."""
    wanted = name.lower()
    for entry in headers:
        if entry.get("name", "").lower() == wanted:
            return entry.get("value", "")
    return ""


def strip_html(html: str) -> str:
    """Plain text out of an HTML body, keeping the line breaks that meant something."""
    text = _SCRIPT_OR_STYLE.sub(" ", html)
    text = _LINE_BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    return _tidy(unescape(text))


def strip_quotes(text: str) -> str:
    """Drop quoted history, which is context the model has already been given.

    Everything from an `On ... wrote:` attribution line onward goes, and so do
    the lines that start with `>`.
    """
    match = _ATTRIBUTION.search(text)
    if match:
        text = text[: match.start()]
    kept = [line for line in text.splitlines() if not _QUOTED_LINE.match(line)]
    return _tidy("\n".join(kept))


def parse_message(message: dict) -> Message:
    """One Gmail message reduced to sender, recipient, date, body, and attachments."""
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    body, has_attachments = _body_and_attachments(payload)
    return Message(
        sender=header(headers, "From"),
        recipient=header(headers, "To"),
        date=timestamp(message),
        body=truncate(body, MAX_BODY_CHARS),
        has_attachments=has_attachments,
    )


def parse_thread(thread: dict) -> Thread:
    """One Gmail thread, trimmed to fit the state budget.

    Subject, sender, and date come from the first message, because that is what
    a person means by who a thread is from and when it started.
    """
    raw_messages = thread.get("messages") or []
    messages = _fit([parse_message(raw) for raw in raw_messages], MAX_STATE_CHARS)
    first = raw_messages[0] if raw_messages else {}
    first_headers = (first.get("payload") or {}).get("headers") or []
    return Thread(
        id=thread.get("id", ""),
        subject=header(first_headers, "Subject"),
        sender=header(first_headers, "From"),
        date=timestamp(first),
        snippet=_snippet(thread, first),
        messages=messages,
    )


def _snippet(thread: dict, first_message: dict) -> str:
    """The preview line Gmail wrote, unescaped.

    `threads.get` puts a snippet on every message and `threads.list` puts one
    on the thread, so take whichever is there. Gmail escapes these for HTML,
    which is how an apostrophe arrives as `&#39;`.
    """
    return unescape(first_message.get("snippet") or thread.get("snippet") or "")


def timestamp(message: dict) -> str:
    """When a message arrived, as UTC in ISO 8601.

    Gmail's `internalDate` is milliseconds since the epoch and is what the
    server recorded. The `Date` header is the fallback, and it is whatever the
    sending client wrote.
    """
    internal = message.get("internalDate")
    if internal:
        return datetime.fromtimestamp(int(internal) / 1000, UTC).strftime(ISO_FORMAT)

    raw = header((message.get("payload") or {}).get("headers") or [], "Date")
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime(ISO_FORMAT)


def truncate(text: str, limit: int) -> str:
    """Cut text to a character budget, saying that something was cut."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + TRUNCATION_MARK


def _body_and_attachments(payload: dict) -> tuple[str, bool]:
    """The readable text of one message, and whether it carried an attachment."""
    plain: list[str] = []
    html: list[str] = []
    has_attachments = _walk(payload, plain, html)
    text = "\n".join(plain) if plain else strip_html("\n".join(html))
    return strip_quotes(text), has_attachments


def _walk(part: dict, plain: list[str], html: list[str]) -> bool:
    """Collect the text parts of a MIME tree, reporting whether any part was attached.

    A part with a filename is an attachment, whatever its type, so its content
    is skipped and its name is thrown away.
    """
    attached = bool(part.get("filename"))
    data = (part.get("body") or {}).get("data")
    if data and not attached:
        mime_type = part.get("mimeType", "")
        if mime_type == "text/plain":
            plain.append(_decode(data))
        elif mime_type == "text/html":
            html.append(_decode(data))

    for child in part.get("parts") or []:
        attached = _walk(child, plain, html) or attached
    return attached


def _decode(data: str) -> str:
    """Decode a base64url body, putting back the padding Gmail leaves off."""
    cleaned = "".join(data.split())
    padded = cleaned + "=" * (-len(cleaned) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _tidy(text: str) -> str:
    """Collapse runs of spaces and blank lines, and trim the edges."""
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def _fit(messages: list[Message], limit: int) -> list[Message]:
    """Keep the newest messages that fit the state budget.

    The oldest go first, because the later messages in a thread carry what it
    turned into. When anything is dropped the oldest survivor says so at the
    top of its body, so the model can tell it is reading part of a thread
    rather than all of it.
    """
    if not messages:
        return messages

    kept: list[Message] = []
    spent = 0
    for message in reversed(messages):
        if kept and spent + len(message.body) > limit:
            break
        kept.append(message)
        spent += len(message.body)
    kept.reverse()

    if len(kept) == len(messages):
        return kept

    oldest = kept[0]
    kept[0] = oldest.model_copy(update={"body": f"{TRUNCATION_MARK}\n{oldest.body}"})
    return kept

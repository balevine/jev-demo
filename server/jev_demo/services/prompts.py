"""Turning STATE.json and QUESTIONS.json into the two halves of a Jev request.

Both files are Jinja2 templates that render to text and are then parsed as
JSON. That is one job, so it happens in one function and the error handling
exists once.

A template that renders to something that is not JSON is the likeliest
authoring mistake, and a forgotten `tojson` is the likeliest cause, so the
error carries the rendered text with line numbers rather than just the
parser's complaint.

QUESTIONS.json is checked at startup, because the way it fails is silent. The
model never sees a question's ID, so a template that does not put the label
name in its instructions renders every question identically and every answer
is meaningless. That check is here too.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from jinja2 import Environment, StrictUndefined, Template
from jinja2 import TemplateError as JinjaTemplateError

from jev_demo.config import get_settings
from jev_demo.schemas import Label, Message, Thread

QUESTION_TYPE = "noul"

# How much of the rendered text to show around a JSON error.
ERROR_CONTEXT_LINES = 6

# What the startup check renders the question template against. The name is
# deliberately odd, so finding it in the rendered instructions means the
# template really did interpolate the label rather than happening to contain
# the same words.
PROBE_LABEL = Label(
    id="Label_probe",
    name="Startup Probe",
    description="a placeholder used to check the template",
)

PROBE_THREAD = Thread(
    id="probe",
    subject="Startup probe",
    sender="probe@example.com",
    date="2026-01-01T00:00:00Z",
    snippet="Startup probe",
    messages=[
        Message(
            sender="probe@example.com",
            recipient="me@example.com",
            date="2026-01-01T00:00:00Z",
            body="Startup probe.",
            has_attachments=False,
        )
    ],
)

_ENVIRONMENT = Environment(undefined=StrictUndefined, keep_trailing_newline=True)


class TemplateError(RuntimeError):
    """Raised when a template will not render, or does not render to JSON."""


def render_state(thread: Thread) -> Any:
    """The `state` half of a Jev request, rendered from STATE.json."""
    return _render_json(get_settings().state_template, thread=thread)


def render_questions(labels: list[Label], thread: Thread) -> dict[str, Any]:
    """The `questions` half of a Jev request, one noul per label.

    Keyed by Gmail label ID so an answer can be matched back to its label
    without comparing name strings. The ID is never shown to the model.
    """
    path = get_settings().questions_template
    return {label.id: _render_json(path, label=label, thread=thread) for label in labels}


def validate_templates() -> None:
    """Check both templates at startup, and stop with an explanation if either is wrong."""
    render_state(PROBE_THREAD)
    _validate_question(render_questions([PROBE_LABEL], PROBE_THREAD)[PROBE_LABEL.id])


def _validate_question(question: Any) -> None:
    """Refuse a question template that would make every answer meaningless."""
    path = get_settings().questions_template

    def refuse(problem: str) -> NoReturn:
        raise TemplateError(f"{path} {problem}")

    if not isinstance(question, dict):
        refuse(f"has to render to a JSON object. It rendered to {type(question).__name__}.")

    if question.get("type") != QUESTION_TYPE:
        refuse(f'needs "type" to be "{QUESTION_TYPE}". It rendered "{question.get("type")}".')

    instructions = question.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        refuse('needs "instructions" to be text, and it cannot be empty.')

    if PROBE_LABEL.name not in instructions:
        refuse(
            'has to put {{ label.name }} inside "instructions". Question IDs are never sent '
            "to the model, so without the name every label renders the same question and "
            "every answer is meaningless."
        )


def _render_json(path: Path, **context: Any) -> Any:
    """Render one template and parse what comes out as JSON.

    Both templates go through here, so a broken one reports the same way
    whichever it was.
    """
    text = _render(path, **context)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TemplateError(
            f"{path} did not render to valid JSON. {exc.msg}, at line {exc.lineno} "
            f"column {exc.colno}. A value dropped in without the tojson filter is the "
            f"usual cause.\n\n{_numbered(text, exc.lineno)}"
        ) from exc


def _render(path: Path, **context: Any) -> str:
    """Render one template to text.

    A template can call any filter and a filter can raise anything, so every
    failure is caught and reported against the file that caused it. Letting one
    through would show the user a traceback through Jinja's internals instead.
    """
    try:
        return _template(path).render(**context)
    except TemplateError:
        raise
    except Exception as exc:
        raise TemplateError(
            f"{path} would not render. {type(exc).__name__}, {exc}.{_hint(exc)}"
        ) from exc


def _hint(exc: Exception) -> str:
    """A nudge toward the cause, for the render failures with unhelpful messages."""
    if "Undefined" in f"{type(exc).__name__}{exc}":
        return " That usually means the template names a variable that does not exist."
    return ""


def _template(path: Path) -> Template:
    """The compiled template, recompiled when the file changes on disk."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError as exc:
        raise TemplateError(f"{path} could not be read. {exc.strerror}.") from exc
    return _compile(path, stamp)


@lru_cache(maxsize=8)
def _compile(path: Path, stamp: int) -> Template:
    """Compile one template. Cached, with the file's timestamp as part of the key."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"{path} could not be read. {exc.strerror}.") from exc
    try:
        return _ENVIRONMENT.from_string(source)
    except JinjaTemplateError as exc:
        raise TemplateError(f"{path} is not a valid template. {exc}") from exc


def _numbered(text: str, around: int) -> str:
    """The rendered text near a line, numbered, with that line marked."""
    lines = text.splitlines()
    first = max(1, around - ERROR_CONTEXT_LINES)
    last = min(len(lines), around + ERROR_CONTEXT_LINES)
    width = len(str(last))
    return "\n".join(
        f"{'>' if number == around else ' '} {number:>{width}} | {lines[number - 1]}"
        for number in range(first, last + 1)
    )

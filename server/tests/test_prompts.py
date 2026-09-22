"""Rendering the two templates into the two halves of a Jev request.

The check that matters most is the last one in the first group. Question IDs
are never sent to the model, so a question that does not name its label is a
question about nothing, and it fails without looking like a failure.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_demo.config import get_settings
from jev_demo.main import app
from jev_demo.schemas import Label, Message, Thread
from jev_demo.services import prompts

FIXTURES = Path(__file__).parent / "fixtures"

THREAD = Thread(
    id="18f0a2c",
    subject='Invoice #4021, the "September" one',
    sender="Ada <billing@acme.com>",
    date="2026-09-18T14:02:11Z",
    snippet="Your September invoice is ready",
    messages=[
        Message(
            sender="Ada <billing@acme.com>",
            recipient="me@example.com",
            date="2026-09-18T14:02:11Z",
            body="Your September invoice is attached.\nIt is due on the 30th.",
            has_attachments=True,
        ),
        Message(
            sender="me@example.com",
            recipient="Ada <billing@acme.com>",
            date="2026-09-19T09:00:00Z",
            body="Paid, thanks.",
            has_attachments=False,
        ),
    ],
)

LABELS = [
    Label(id="Label_12", name="Invoices", description="receipts, bills, and payment requests"),
    Label(id="Label_19", name="Recruiting"),
]

EXPECTED = json.loads((FIXTURES / "rendered_request.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Templates are resolved from the environment, so no test inherits another's."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Swap in a template written by the test."""

    def write(*, state: str | None = None, questions: str | None = None) -> None:
        for name, source in (("STATE", state), ("QUESTIONS", questions)):
            if source is None:
                continue
            path = tmp_path / f"{name}.json"
            path.write_text(source, encoding="utf-8")
            monkeypatch.setenv(f"JEV_DEMO_{name}_TEMPLATE", str(path))
        get_settings.cache_clear()

    return write


def test_the_state_renders_to_the_saved_json() -> None:
    assert prompts.render_state(THREAD) == EXPECTED["state"]


def test_one_question_renders_per_label() -> None:
    questions = prompts.render_questions(LABELS, THREAD)

    assert questions == EXPECTED["questions"]
    assert list(questions) == [label.id for label in LABELS]


def test_every_question_is_a_noul_that_names_its_own_label() -> None:
    questions = prompts.render_questions(LABELS, THREAD)

    for label in LABELS:
        question = questions[label.id]
        assert question["type"] == "noul"
        assert label.name in question["instructions"]

    # The names are what tell two questions apart, so two labels cannot render
    # the same instructions.
    instructions = {question["instructions"] for question in questions.values()}
    assert len(instructions) == len(LABELS)


def test_a_description_reaches_the_instructions_and_an_empty_one_is_left_out() -> None:
    questions = prompts.render_questions(LABELS, THREAD)

    assert "receipts, bills, and payment requests" in questions["Label_12"]["instructions"]
    assert questions["Label_19"]["instructions"].endswith("belongs on this email thread.")


def test_the_state_carries_the_thread_but_not_its_id() -> None:
    state = prompts.render_state(THREAD)

    assert state["thread"]["from"] == "Ada <billing@acme.com>"
    assert len(state["thread"]["messages"]) == THREAD.message_count
    assert state["thread"]["messages"][0]["has_attachments"] is True
    assert THREAD.id not in json.dumps(state)


def test_a_thread_with_no_messages_still_renders() -> None:
    empty = THREAD.model_copy(update={"messages": []})

    assert prompts.render_state(empty)["thread"]["messages"] == []


def test_quotes_and_newlines_in_an_email_stay_inside_the_json_string() -> None:
    # An email body is arbitrary text. Without tojson this is where the state
    # stops being JSON.
    hostile = THREAD.model_copy(
        update={
            "subject": 'He said "hello", \\ then left',
            "messages": [
                THREAD.messages[0].model_copy(
                    update={"body": 'Line one\n"quoted"\ttabbed \\ {"not": "json"}'}
                )
            ],
        }
    )

    state = prompts.render_state(hostile)

    assert state["thread"]["subject"] == 'He said "hello", \\ then left'
    assert state["thread"]["messages"][0]["text"] == 'Line one\n"quoted"\ttabbed \\ {"not": "json"}'


def test_a_quote_in_a_label_name_stays_inside_the_json_string() -> None:
    quoted = Label(id="Label_44", name='The "Urgent" one')

    question = prompts.render_questions([quoted], THREAD)["Label_44"]

    assert quoted.name in question["instructions"]


def test_a_template_that_forgets_tojson_reports_the_rendered_text_with_line_numbers(
    template: Callable[..., None],
) -> None:
    template(state='{\n  "a": 1,\n  "subject": {{ thread.subject }},\n  "b": 2\n}\n')

    with pytest.raises(prompts.TemplateError) as raised:
        prompts.render_state(THREAD)

    message = str(raised.value)
    assert "did not render to valid JSON" in message
    assert "tojson" in message
    # The rendered text, numbered, with the bad line marked.
    assert '> 3 |   "subject": Invoice #4021, the "September" one,' in message
    assert '  4 |   "b": 2' in message


def test_a_template_that_names_a_variable_that_does_not_exist_is_an_error(
    template: Callable[..., None],
) -> None:
    template(state='{"subject": {{ thread.topic | tojson }}}')

    with pytest.raises(prompts.TemplateError, match="would not render"):
        prompts.render_state(THREAD)


def test_a_missing_template_file_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_DEMO_STATE_TEMPLATE", str(tmp_path / "gone.json"))
    get_settings.cache_clear()

    with pytest.raises(prompts.TemplateError, match="could not be read"):
        prompts.render_state(THREAD)


def test_the_shipped_templates_pass_the_startup_check() -> None:
    prompts.validate_templates()


def test_the_startup_check_refuses_a_question_that_never_names_its_label(
    template: Callable[..., None],
) -> None:
    template(
        questions='{"type": "noul", "instructions": "This label belongs on this thread.",'
        ' "criteria": {"true": "yes", "false": "no"}}'
    )

    with pytest.raises(prompts.TemplateError, match="label.name"):
        prompts.validate_templates()


@pytest.mark.parametrize(
    ("questions", "problem"),
    [
        ('{"type": "score", "instructions": "{{ label.name }}"}', "noul"),
        ('{"type": "noul", "instructions": "  "}', "cannot be empty"),
        ('{"type": "noul"}', "cannot be empty"),
        ('["{{ label.name }}"]', "JSON object"),
    ],
    ids=["wrong-type", "blank-instructions", "no-instructions", "not-an-object"],
)
def test_the_startup_check_refuses_a_malformed_question(
    template: Callable[..., None], questions: str, problem: str
) -> None:
    template(questions=questions)

    with pytest.raises(prompts.TemplateError, match=problem):
        prompts.validate_templates()


def test_the_server_refuses_to_start_on_a_question_that_never_names_its_label(
    template: Callable[..., None], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_DEMO_HOME", str(tmp_path / "home"))
    template(questions='{"type": "noul", "instructions": "Does this label fit?"}')

    with pytest.raises(prompts.TemplateError, match="label.name"), TestClient(app):
        pass


def test_an_edited_template_is_picked_up_without_a_restart(
    template: Callable[..., None], tmp_path: Path
) -> None:
    # Compiled templates are cached, so the cache has to notice the file moving
    # on. The timestamp is set by hand here rather than trusting two writes in
    # the same test to land in different nanoseconds.
    template(state='{"one": 1}')
    assert prompts.render_state(THREAD) == {"one": 1}

    path = tmp_path / "STATE.json"
    path.write_text('{"two": 2}', encoding="utf-8")
    later = path.stat().st_mtime + 10
    os.utime(path, (later, later))

    assert prompts.render_state(THREAD) == {"two": 2}

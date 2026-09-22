"""Classifying threads and streaming the results.

Jev itself is stood in for here. What it says over the wire is tested in
test_jev.py, and what matters at this level is what happens to the answers
afterward. A noul that never arrived has to stay apart from a noul of zero, one
thread failing must not take the run with it, and the lines have to be written
as threads are answered rather than collected up and sent at the end.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jev_demo.config import JEV_INPUT_TOKEN_COST, get_settings
from jev_demo.main import app
from jev_demo.schemas import ClassifyRequest, Label
from jev_demo.services import classify, gmail_auth, jev, parse, token_store
from tests.helpers import FakeGmail, thread

LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "Label_12", "name": "Invoices", "type": "user"},
    {"id": "Label_19", "name": "Recruiting", "type": "user"},
]

THREADS = [
    thread("18f", "Invoice #4021", "Your September invoice is ready.", "billing@acme.com"),
    thread("18g", "Coffee?", "Free on Thursday?"),
]

# Keyed by subject, because that is what the fake reads out of the state it is
# handed. Answering on the rendered state is also what proves the state was
# rendered at all.
ANSWERS = {
    "Invoice #4021": {"Label_12": 0.93, "Label_19": 0.02},
    "Coffee?": {"Label_12": 0.01, "Label_19": 0.04},
}

TOKENS_PER_THREAD = 812

REQUEST = ClassifyRequest()


class FakeJev:
    """Jev, answering from a table instead of over the wire."""

    def __init__(self, answers: dict[str, dict[str, float | None]]) -> None:
        self.answers = answers
        self.asked: list[dict[str, Any]] = []
        self.fails: set[str] = set()
        self.delays: dict[str, float] = {}

    async def ask(self, state: Any, questions: dict[str, Any]) -> jev.JevResponse:
        subject = state["thread"]["subject"]
        self.asked.append({"subject": subject, "questions": questions})

        delay = self.delays.get(subject)
        if delay:
            await asyncio.sleep(delay)
        if subject in self.fails:
            raise jev.JevError(503, "the model is busy")

        answered = {
            label_id: {"type": "noul", "noul": noul} if noul is not None else {"type": "noul"}
            for label_id, noul in self.answers[subject].items()
        }
        return jev.JevResponse.model_validate(
            {"answers": answered, "usage": {"input_tokens": TOKENS_PER_THREAD}}
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the real home directory and the real key out of these tests."""
    monkeypatch.setenv("JEV_DEMO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """A fake Gmail standing in for the real one, with auth already satisfied."""
    service = FakeGmail(LABELS, THREADS)
    monkeypatch.setattr(gmail_auth, "require_credentials", lambda: object())
    monkeypatch.setattr(gmail_auth, "gmail_service", lambda credentials=None: service)
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    return service


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    """A fake Jev, handed out wherever the real client would be."""
    fake = FakeJev(ANSWERS)

    @asynccontextmanager
    async def client() -> Any:
        yield fake

    monkeypatch.setattr(jev, "client", client)
    return fake


def run(client: TestClient, **body: Any) -> list[dict]:
    """Every line of one classify stream, parsed."""
    response = client.post("/classify", json=body)
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines()]


def labels_of(line: dict) -> dict[str, float | None]:
    """The noul per label name on one result line."""
    return {entry["name"]: entry["noul"] for entry in line["labels"]}


def applied_in(line: dict) -> list[str]:
    """The names of the labels that made the threshold on one result line."""
    return [entry["name"] for entry in line["labels"] if entry["apply"]]


def test_every_line_is_one_json_object_and_the_last_one_is_the_totals(
    gmail: FakeGmail, model: FakeJev
) -> None:
    with TestClient(app) as client:
        lines = run(client, last=2)

    assert len(lines) == len(THREADS) + 1
    assert [line["thread_id"] for line in lines[:-1]] == ["18f", "18g"]
    assert lines[-1]["totals"]["threads"] == 2


def test_the_stream_is_ndjson(gmail: FakeGmail, model: FakeJev) -> None:
    with TestClient(app) as client:
        response = client.post("/classify", json={"last": 2})

    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.text.endswith("\n")


def test_a_label_at_or_above_the_threshold_is_the_one_that_gets_applied(
    gmail: FakeGmail, model: FakeJev
) -> None:
    with TestClient(app) as client:
        lines = run(client, last=2)

    invoice = next(line for line in lines[:-1] if line["thread_id"] == "18f")
    coffee = next(line for line in lines[:-1] if line["thread_id"] == "18g")

    assert labels_of(invoice) == {"Invoices": 0.93, "Recruiting": 0.02}
    assert applied_in(invoice) == ["Invoices"]
    assert applied_in(coffee) == []


def test_the_threshold_can_be_moved(gmail: FakeGmail, model: FakeJev) -> None:
    with TestClient(app) as client:
        lines = run(client, last=2, threshold=0.02)

    invoice = next(line for line in lines[:-1] if line["thread_id"] == "18f")

    assert applied_in(invoice) == ["Invoices", "Recruiting"]


def test_a_noul_that_never_arrived_is_not_a_noul_of_zero(gmail: FakeGmail, model: FakeJev) -> None:
    model.answers = {
        "Invoice #4021": {"Label_12": None, "Label_19": 0.0},
        "Coffee?": {"Label_12": 0.0, "Label_19": 0.0},
    }

    with TestClient(app) as client:
        lines = run(client, last=2, threshold=0.0)

    invoice = next(line for line in lines[:-1] if line["thread_id"] == "18f")

    # At a threshold of zero a real zero is enough, and a missing answer is
    # still not an answer.
    assert labels_of(invoice) == {"Invoices": None, "Recruiting": 0.0}
    assert applied_in(invoice) == ["Recruiting"]


def test_a_thread_that_fails_reports_it_and_the_rest_of_the_run_carries_on(
    gmail: FakeGmail, model: FakeJev
) -> None:
    model.fails = {"Invoice #4021"}

    with TestClient(app) as client:
        lines = run(client, last=2)

    failed = next(line for line in lines[:-1] if line["thread_id"] == "18f")
    fine = next(line for line in lines[:-1] if line["thread_id"] == "18g")

    assert "the model is busy" in failed["error"]
    assert failed["labels"] == []
    assert fine["error"] is None
    assert fine["labels"]


def test_nothing_is_written_to_gmail_unless_the_request_asks(
    gmail: FakeGmail, model: FakeJev
) -> None:
    with TestClient(app) as client:
        lines = run(client, last=2)

    assert gmail.modified == []
    assert all(line["applied"] is False for line in lines[:-1])


def test_asking_to_apply_adds_the_labels_that_made_the_threshold(
    gmail: FakeGmail, model: FakeJev
) -> None:
    with TestClient(app) as client:
        lines = run(client, last=2, apply=True)

    assert gmail.modified == [("18f", {"addLabelIds": ["Label_12"]})]

    invoice = next(line for line in lines[:-1] if line["thread_id"] == "18f")
    coffee = next(line for line in lines[:-1] if line["thread_id"] == "18g")
    assert invoice["applied"] is True
    assert coffee["applied"] is False


def test_the_totals_count_the_tokens_and_what_they_cost(gmail: FakeGmail, model: FakeJev) -> None:
    with TestClient(app) as client:
        totals = run(client, last=2)[-1]["totals"]

    spent = TOKENS_PER_THREAD * len(THREADS)
    assert totals["threads"] == 2
    assert totals["input_tokens"] == spent

    # The dollar figure is rounded to where it stops meaning anything, so it is
    # near the real number rather than exactly it.
    assert totals["cost_usd"] == pytest.approx(spent * JEV_INPUT_TOKEN_COST / 1_000_000, rel=1e-3)


def test_a_thread_that_failed_costs_nothing(gmail: FakeGmail, model: FakeJev) -> None:
    model.fails = {"Invoice #4021"}

    with TestClient(app) as client:
        totals = run(client, last=2)[-1]["totals"]

    assert totals["input_tokens"] == TOKENS_PER_THREAD


def test_every_label_is_asked_about_in_one_request_and_none_of_them_is_a_system_label(
    gmail: FakeGmail, model: FakeJev
) -> None:
    with TestClient(app) as client:
        run(client, last=2)

    assert len(model.asked) == len(THREADS)
    for request in model.asked:
        assert list(request["questions"]) == ["Label_12", "Label_19"]


def test_a_question_carries_its_label_name_because_the_model_never_sees_the_id(
    gmail: FakeGmail, model: FakeJev
) -> None:
    with TestClient(app) as client:
        run(client, last=2)

    questions = model.asked[0]["questions"]
    assert "Invoices" in questions["Label_12"]["instructions"]
    assert "Recruiting" in questions["Label_19"]["instructions"]


def test_a_mailbox_with_no_labels_of_its_own_says_so_instead_of_asking_nothing(
    gmail: FakeGmail, model: FakeJev
) -> None:
    gmail.labels_payload = [{"id": "INBOX", "name": "INBOX", "type": "system"}]

    with TestClient(app) as client:
        response = client.post("/classify", json={"last": 2})

    assert response.status_code == 400
    assert "no labels" in response.json()["detail"]
    assert model.asked == []


def test_a_missing_key_is_refused_before_the_stream_opens(
    gmail: FakeGmail, model: FakeJev, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY")
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.post("/classify", json={"last": 2})

    assert response.status_code == 503
    assert "TYPESAFE_API_KEY" in response.json()["detail"]


def test_nobody_logged_in_is_refused_before_the_stream_opens(
    model: FakeJev, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(token_store, "load", lambda: None)

    with TestClient(app) as client:
        response = client.post("/classify", json={"last": 2})

    assert response.status_code == 401
    assert "jev auth" in response.json()["detail"]


@pytest.mark.anyio
async def test_a_line_is_written_as_each_thread_is_answered_rather_than_at_the_end(
    model: FakeJev,
) -> None:
    """The point of the stream. A slow thread must not hold up a fast one."""
    model.delays = {"Invoice #4021": 0.2}

    labels = [Label(id="Label_12", name="Invoices"), Label(id="Label_19", name="Recruiting")]
    threads = [parse.parse_thread(entry) for entry in THREADS]

    written = [json.loads(line) async for line in classify.stream(labels, threads, REQUEST)]

    # Asked in order, answered out of it, and written in the order answered.
    assert [request["subject"] for request in model.asked] == ["Invoice #4021", "Coffee?"]
    assert [line["thread_id"] for line in written[:-1]] == ["18g", "18f"]


def test_the_cost_of_a_run_is_small_enough_to_still_show_up() -> None:
    # A demo run is a fraction of a cent, so the rounding has to leave
    # something behind.
    assert classify.cost(4_060) > 0.0
    assert classify.cost(0) == 0.0

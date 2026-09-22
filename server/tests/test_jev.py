"""Talking to Jev.

Two things here are worth more than the rest. A noul that never arrived has to
stay apart from a noul of zero, because the two mean opposite things once the
threshold is applied. And a failure has to carry the raw body, because the
error shape is undocumented and guessing at it would break the first time it
changed.

The last test in the file is the only one that leaves the machine. It is
skipped unless TYPESAFE_API_KEY is set.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx

from jev_demo.config import JEV_API_URL, get_settings
from jev_demo.services import jev

pytestmark = pytest.mark.anyio

# Read before any fixture replaces it, so the live test still has the real one.
LIVE_KEY = os.environ.get("TYPESAFE_API_KEY")

STATE = {"thread": {"subject": "Invoice #4021"}}

QUESTIONS = {
    "Label_12": {
        "type": "noul",
        "instructions": 'The Gmail label "Invoices" belongs on this email thread.',
        "criteria": {"true": "It is a bill.", "false": "It is not."},
    },
    "Label_19": {
        "type": "noul",
        "instructions": 'The Gmail label "Recruiting" belongs on this email thread.',
        "criteria": {"true": "It is about hiring.", "false": "It is not."},
    },
}

ANSWERED = {
    "answers": {
        "Label_12": {"type": "noul", "noul": 0.93},
        "Label_19": {"type": "noul", "noul": 0.02},
    },
    "usage": {"input_tokens": 812},
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A key every test can count on, read fresh rather than inherited."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the wait out of the retries. How long they wait is tested on its own."""

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(jev, "_sleep", no_wait)


def response(status_code: int, **kwargs: object) -> httpx.Response:
    """One canned reply from Jev."""
    return httpx.Response(status_code, **kwargs)


def json_body(route: respx.Route) -> dict:
    """What the last request to a route actually sent."""
    return json.loads(route.calls.last.request.content)


async def test_a_request_carries_the_state_the_model_and_the_questions() -> None:
    with respx.mock:
        route = respx.post(JEV_API_URL).mock(return_value=response(200, json=ANSWERED))
        async with jev.client() as jev_client:
            await jev_client.ask(STATE, QUESTIONS)

    sent = json_body(route)
    assert sent["state"] == STATE
    assert sent["model"] == "jev-latest"
    assert sent["questions"] == QUESTIONS
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


async def test_answers_come_back_keyed_by_the_question_ids_that_were_sent() -> None:
    with respx.mock:
        respx.post(JEV_API_URL).mock(return_value=response(200, json=ANSWERED))
        async with jev.client() as jev_client:
            answered = await jev_client.ask(STATE, QUESTIONS)

    assert list(answered.answers) == ["Label_12", "Label_19"]
    assert answered.answers["Label_12"].noul == 0.93
    assert answered.answers["Label_19"].noul == 0.02
    assert answered.usage.input_tokens == 812


async def test_an_answer_with_no_noul_is_not_an_answer_of_zero() -> None:
    body = {"answers": {"Label_12": {"type": "noul"}, "Label_19": {"type": "noul", "noul": 0.0}}}

    with respx.mock:
        respx.post(JEV_API_URL).mock(return_value=response(200, json=body))
        async with jev.client() as jev_client:
            answered = await jev_client.ask(STATE, QUESTIONS)

    assert answered.answers["Label_12"].noul is None
    assert answered.answers["Label_19"].noul == 0.0


async def test_a_429_is_tried_again_and_the_second_answer_is_the_one_used() -> None:
    with respx.mock:
        route = respx.post(JEV_API_URL).mock(
            side_effect=[response(429, text="slow down"), response(200, json=ANSWERED)]
        )
        async with jev.client() as jev_client:
            answered = await jev_client.ask(STATE, QUESTIONS)

    assert route.call_count == 2
    assert answered.answers["Label_12"].noul == 0.93


@pytest.mark.parametrize("status_code", [408, 500, 502, 503])
async def test_a_status_that_means_not_now_is_tried_twice_more_and_then_given_up_on(
    status_code: int,
) -> None:
    with respx.mock:
        route = respx.post(JEV_API_URL).mock(return_value=response(status_code, text="nope"))
        async with jev.client() as jev_client:
            with pytest.raises(jev.JevError) as raised:
                await jev_client.ask(STATE, QUESTIONS)

    assert route.call_count == jev.MAX_RETRIES + 1
    assert raised.value.status_code == status_code


async def test_a_422_surfaces_the_raw_body_and_is_not_tried_again() -> None:
    # The error shape is undocumented, so this is deliberately not the shape
    # the code would like it to be.
    raw = '{"trouble": {"code": 17, "why": "question 3 has no criteria"}}'

    with respx.mock:
        route = respx.post(JEV_API_URL).mock(return_value=response(422, text=raw))
        async with jev.client() as jev_client:
            with pytest.raises(jev.JevError) as raised:
                await jev_client.ask(STATE, QUESTIONS)

    assert route.call_count == 1
    assert raised.value.status_code == 422
    assert raised.value.body == raw
    assert raw in str(raised.value)


async def test_a_bad_key_says_to_check_the_key() -> None:
    with respx.mock:
        respx.post(JEV_API_URL).mock(return_value=response(401, text="invalid api key"))
        async with jev.client() as jev_client:
            with pytest.raises(jev.JevError) as raised:
                await jev_client.ask(STATE, QUESTIONS)

    assert "TYPESAFE_API_KEY" in str(raised.value)
    assert "invalid api key" in str(raised.value)


async def test_a_body_that_is_not_a_set_of_answers_is_an_error() -> None:
    with respx.mock:
        respx.post(JEV_API_URL).mock(return_value=response(200, text="<html>hello</html>"))
        async with jev.client() as jev_client:
            with pytest.raises(jev.JevError) as raised:
                await jev_client.ask(STATE, QUESTIONS)

    assert "could not read" in str(raised.value)
    assert raised.value.body == "<html>hello</html>"


async def test_an_error_body_is_cut_down_in_the_message_but_kept_on_the_error() -> None:
    raw = "x" * (jev.BODY_EXCERPT_CHARS + 500)

    with respx.mock:
        respx.post(JEV_API_URL).mock(return_value=response(422, text=raw))
        async with jev.client() as jev_client:
            with pytest.raises(jev.JevError) as raised:
                await jev_client.ask(STATE, QUESTIONS)

    assert raised.value.body == raw
    assert len(str(raised.value)) < len(raw)


async def test_a_missing_key_says_where_to_put_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY")
    get_settings.cache_clear()

    with pytest.raises(jev.KeyMissing, match="TYPESAFE_API_KEY"):
        async with jev.client():
            pass


@pytest.mark.parametrize("attempt", [0, 1, 2, 9])
def test_the_wait_doubles_per_attempt_up_to_a_ceiling(attempt: int) -> None:
    longest = min(jev.FIRST_BACKOFF * 2**attempt, jev.LONGEST_BACKOFF)

    # Jitter only shortens, so the ceiling stays a ceiling.
    for _ in range(50):
        delay = jev.retry_delay(response(429), attempt)
        assert longest * (1 - jev.JITTER) <= delay <= longest


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "3"}, 3.0),
        ({"retry-after": "0.75"}, 0.75),
        ({"retry-after-ms": "1500"}, 1.5),
        # Milliseconds win, since they are the more exact of the two.
        ({"retry-after-ms": "250", "retry-after": "9"}, 0.25),
    ],
    ids=["seconds", "fractional-seconds", "milliseconds", "both"],
)
def test_a_response_that_asks_for_a_wait_gets_it(headers: dict, expected: float) -> None:
    assert jev.retry_delay(response(429, headers=headers), 0) == pytest.approx(expected)


def test_a_retry_after_written_as_a_date_is_read_as_one() -> None:
    when = datetime.now(UTC) + timedelta(seconds=20)

    delay = jev.retry_delay(response(429, headers={"retry-after": format_datetime(when)}), 0)

    assert 15 <= delay <= 21


@pytest.mark.parametrize(
    "headers",
    [
        {"retry-after": "3600"},
        {"retry-after": "0"},
        {"retry-after": "-5"},
        {"retry-after": "soon"},
        {"retry-after-ms": "not a number"},
    ],
    ids=["too-long", "zero", "negative", "words", "words-in-ms"],
)
def test_a_wait_that_makes_no_sense_falls_back_to_the_backoff(headers: dict) -> None:
    delay = jev.retry_delay(response(429, headers=headers), 0)

    assert jev.FIRST_BACKOFF * (1 - jev.JITTER) <= delay <= jev.FIRST_BACKOFF


@pytest.mark.live
@pytest.mark.skipif(not LIVE_KEY, reason="needs a real TYPESAFE_API_KEY")
async def test_a_live_call_answers_every_question_it_was_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", LIVE_KEY or "")
    get_settings.cache_clear()

    async with jev.client() as jev_client:
        answered = await jev_client.ask(STATE, QUESTIONS)

    assert set(answered.answers) == set(QUESTIONS)
    for answer in answered.answers.values():
        assert answer.noul is not None
        assert 0.0 <= answer.noul <= 1.0
    assert answered.usage.input_tokens > 0

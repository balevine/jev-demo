"""Asking about every selected thread, and writing the answers out as they land.

One Jev request per thread carries every label as its own noul question. The
threads themselves run together in a small pool, so a run is roughly as slow as
its slowest thread rather than the sum of all of them.

Results are written the moment each thread is answered rather than collected and
sent at the end. Jev answers in about a tenth of a second, and rows appearing at
that rate is the whole point of the stream.

One thread failing is not the run failing. Whatever went wrong rides on that
thread's own line and the rest keep going.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jev_demo.config import JEV_INPUT_TOKEN_COST
from jev_demo.schemas import (
    ClassifyRequest,
    Label,
    LabelScore,
    RunSummary,
    RunTotals,
    Thread,
    ThreadResult,
    Usage,
)
from jev_demo.services import gmail_client, jev, prompts
from jev_demo.services.jev import Answer

NDJSON_MEDIA_TYPE = "application/x-ndjson"

# How many threads are in flight at once. Jev allows far more, so this is about
# not opening 100 sockets for a run nobody is watching that closely.
CLASSIFY_WORKERS = 8

# Dollars are rounded past the point where the number still means anything. A
# whole run costs a fraction of a cent, so there have to be enough digits left
# for it to show up at all.
COST_DIGITS = 8


class NothingToAsk(RuntimeError):
    """Raised when the mailbox has no labels, so there is no question to ask."""


def prepare(request: ClassifyRequest) -> tuple[list[Label], list[Thread]]:
    """Everything Gmail has to hand over before a single question is asked.

    This runs before the stream opens so a problem with the mailbox comes back
    as a status code. Once the first line is written the response is already a
    200 and there is no taking it back.
    """
    labels = gmail_client.list_labels()
    if not labels:
        raise NothingToAsk(
            "This mailbox has no labels of its own, so there is nothing to ask "
            "about. Make a label or two in Gmail and run this again."
        )
    return labels, gmail_client.fetch_threads(request)


async def stream(
    labels: list[Label], threads: list[Thread], request: ClassifyRequest
) -> AsyncIterator[str]:
    """One line of JSON per thread as it is answered, then a line of totals."""
    spent = 0
    async with jev.client() as jev_client:
        limit = asyncio.Semaphore(CLASSIFY_WORKERS)
        running = [
            asyncio.create_task(_classify(jev_client, labels, thread, request, limit))
            for thread in threads
        ]
        try:
            for finished in asyncio.as_completed(running):
                result = await finished
                spent += result.usage.input_tokens
                yield _line(result)
        finally:
            # A reader that walks away leaves the rest of the run with nowhere
            # to go, so it stops here rather than running on unwatched.
            for task in running:
                task.cancel()

    yield _line(
        RunSummary(totals=RunTotals(threads=len(threads), input_tokens=spent, cost_usd=cost(spent)))
    )


def cost(input_tokens: int) -> float:
    """What a token count came to in dollars. Output tokens are free."""
    return round(input_tokens * JEV_INPUT_TOKEN_COST / 1_000_000, COST_DIGITS)


def score(labels: list[Label], answers: dict[str, Answer], threshold: float) -> list[LabelScore]:
    """Line each label up with its answer and decide which ones belong.

    Every label that was asked about comes back, not just the ones that made
    the threshold, so a run shows what Jev thought rather than only what it
    concluded. A label Jev did not answer, or answered without a noul, is never
    applied. That is a different thing from an answer of zero and treating the
    two alike would quietly label a thread nobody asked to label.
    """
    scored = []
    for label in labels:
        answer = answers.get(label.id)
        noul = answer.noul if answer else None
        scored.append(
            LabelScore(
                id=label.id,
                name=label.name,
                noul=noul,
                apply=noul is not None and noul >= threshold,
            )
        )
    return scored


async def _classify(
    jev_client: jev.JevClient,
    labels: list[Label],
    thread: Thread,
    request: ClassifyRequest,
    limit: asyncio.Semaphore,
) -> ThreadResult:
    """Ask about one thread, and optionally write what came back to Gmail.

    Anything that goes wrong is caught and carried on the result. A template
    that will not render, a Jev request that will not succeed, and a Gmail write
    that is refused all end the same way here, which is with one thread
    reporting a problem and the run continuing.
    """
    scored: list[LabelScore] = []
    usage = Usage()
    applied = False
    error: str | None = None

    async with limit:
        try:
            state = prompts.render_state(thread)
            questions = prompts.render_questions(labels, thread)
            answered = await jev_client.ask(state, questions)
            usage = answered.usage
            scored = score(labels, answered.answers, request.threshold)
            if request.apply:
                applied = await _apply(thread.id, [entry.id for entry in scored if entry.apply])
        # Deliberately everything. A thread that fails in a way nobody thought
        # of still has to fail on its own line rather than take the run down.
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or type(exc).__name__

    return ThreadResult(
        thread_id=thread.id,
        subject=thread.subject,
        sender=thread.sender,
        date=thread.date,
        snippet=thread.snippet,
        labels=scored,
        applied=applied,
        usage=usage,
        error=error,
    )


async def _apply(thread_id: str, label_ids: list[str]) -> bool:
    """Write the labels that made the threshold, and say whether anything was written.

    The Gmail client is not async, so the call goes to a worker thread rather
    than blocking every other thread in the run.
    """
    if not label_ids:
        return False
    await asyncio.to_thread(gmail_client.apply_labels, thread_id, label_ids)
    return True


def _line(model: ThreadResult | RunSummary) -> str:
    """One NDJSON line, which is one JSON object and a newline."""
    return model.model_dump_json() + "\n"

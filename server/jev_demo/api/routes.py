"""HTTP endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from jev_demo.config import get_settings
from jev_demo.schemas import (
    ApplyRequest,
    ApplyResponse,
    AuthStatusResponse,
    ClassifyRequest,
    HealthResponse,
    Label,
    Thread,
    ThreadSelection,
)
from jev_demo.services import (
    classify,
    descriptions,
    gmail_auth,
    gmail_client,
    jev,
    token_store,
)

router = APIRouter()


@router.get("/health")
def health() -> HealthResponse:
    """Report liveness and whether Gmail auth and the Jev key are present."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        gmail_authenticated=token_store.has_token(),
        jev_key_present=settings.typesafe_api_key is not None,
        keychain_backend=token_store.backend_name(),
    )


@router.get("/auth/status")
def auth_status() -> AuthStatusResponse:
    """Whether Gmail is connected, and to which mailbox."""
    try:
        credentials = gmail_auth.load_credentials()
    except token_store.KeychainUnavailable:
        return AuthStatusResponse(authenticated=False)
    if credentials is None:
        return AuthStatusResponse(authenticated=False)
    return AuthStatusResponse(authenticated=True, email=gmail_auth.account_email(credentials))


@router.post("/auth/login")
def auth_login() -> AuthStatusResponse:
    """Run the browser consent flow and store the token.

    This blocks until consent finishes in the browser. FastAPI runs a plain
    `def` endpoint on a worker thread, so the server keeps answering while it
    waits.
    """
    credentials = gmail_auth.log_in()
    return AuthStatusResponse(authenticated=True, email=gmail_auth.account_email(credentials))


@router.get("/labels")
def labels() -> list[Label]:
    """Every label the user made, with whatever description is stored for it."""
    return gmail_client.list_labels()


@router.get("/label-descriptions")
def label_descriptions() -> dict[str, str]:
    """What each label means, keyed by label ID. Empty until something is saved."""
    return descriptions.load()


@router.put("/label-descriptions")
def replace_label_descriptions(replacement: dict[str, str]) -> dict[str, str]:
    """Replace every stored description with the ones given, and say what was kept.

    Blank descriptions are dropped rather than stored, so what comes back is
    not always what went in.
    """
    return descriptions.save(replacement)


@router.get("/threads")
def threads(selection: Annotated[ThreadSelection, Query()]) -> list[Thread]:
    """The threads a run would cover, exactly as Jev would be shown them.

    Nothing is asked and nothing is written. This is here so the Gmail read
    path can be checked by eye.
    """
    return gmail_client.fetch_threads(selection)


@router.post("/classify")
async def classify_threads(request: ClassifyRequest) -> StreamingResponse:
    """Ask Jev about every selected thread, one line of NDJSON per thread.

    Gmail is read and the key is checked before the stream opens, so those
    failures come back as a status code. After that the response is a 200 and a
    thread that fails carries its error on its own line instead.
    """
    jev.require_key()
    labels, threads = await asyncio.to_thread(classify.prepare, request)
    return StreamingResponse(
        classify.stream(labels, threads, request),
        media_type=classify.NDJSON_MEDIA_TYPE,
    )


@router.post("/apply")
def apply(request: ApplyRequest) -> ApplyResponse:
    """Add labels to one thread. This is the only endpoint that writes to Gmail.

    The label IDs are checked against the user's own labels first. They arrive
    from outside this server, and Gmail would take its own IDs here just as
    readily, which would turn a filing decision into a thread in the trash. One
    Gmail client serves both calls.
    """
    service = gmail_auth.gmail_service()
    gmail_client.require_user_labels(request.label_ids, service)
    gmail_client.apply_labels(request.thread_id, request.label_ids, service)
    return ApplyResponse(thread_id=request.thread_id, applied=request.label_ids)

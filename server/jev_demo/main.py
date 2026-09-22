"""FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from googleapiclient.errors import HttpError

from jev_demo.api.routes import router
from jev_demo.config import get_settings
from jev_demo.services import (
    classify,
    descriptions,
    gmail_auth,
    gmail_client,
    jev,
    prompts,
    token_store,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Check what the server needs before it takes any requests.

    The machine has to be able to hold a token, and both templates have to
    render. A question template that never names its label fails silently at
    run time, so it fails loudly here instead.
    """
    token_store.require_backend()
    prompts.validate_templates()
    get_settings().ensure_home_dir()
    yield


app = FastAPI(title="jev-demo", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def _only_this_machine(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Turn away anything not addressed to this machine by name.

    This server can read a mailbox and write to it, and it asks nobody for a
    password. Binding to the loopback address keeps it off the network, but a
    page open in a browser can still reach it by pointing a name it owns at
    127.0.0.1. The browser then treats the request as same origin and sends it
    without asking anyone, so the name in the Host header is what tells the two
    apart.

    The setting is read per request rather than when the middleware is added,
    so a test can say which names count.
    """
    host = _host_only(request.headers.get("host", ""))
    if host in get_settings().allowed_hosts:
        return await call_next(request)
    return JSONResponse(
        status_code=400,
        content={
            "detail": (
                f"This server does not answer to the name {host or '(none given)'}. It only "
                "takes requests addressed to this machine, because anything that reaches it "
                "can read and label your mail. Use http://127.0.0.1:8000."
            )
        },
    )


def _host_only(header: str) -> str:
    """The name out of a Host header, with any port dropped.

    An IPv6 address is bracketed and full of colons, so the port cannot be
    found by splitting on the first one.
    """
    if header.startswith("[") and "]" in header:
        return header[1 : header.index("]")]
    return header.split(":", 1)[0]


def _problem(status_code: int, exc: Exception) -> JSONResponse:
    """Turn an expected failure into its message instead of a traceback."""
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.exception_handler(token_store.KeychainUnavailable)
def _keychain_unavailable(request: Request, exc: Exception) -> JSONResponse:
    return _problem(503, exc)


@app.exception_handler(gmail_auth.CredentialsFileMissing)
def _credentials_file_missing(request: Request, exc: Exception) -> JSONResponse:
    return _problem(400, exc)


@app.exception_handler(gmail_auth.NotAuthenticated)
def _not_authenticated(request: Request, exc: Exception) -> JSONResponse:
    return _problem(401, exc)


@app.exception_handler(classify.NothingToAsk)
def _nothing_to_ask(request: Request, exc: Exception) -> JSONResponse:
    return _problem(400, exc)


@app.exception_handler(gmail_client.UnknownLabels)
def _unknown_labels(request: Request, exc: Exception) -> JSONResponse:
    return _problem(400, exc)


@app.exception_handler(prompts.TemplateError)
def _template_error(request: Request, exc: Exception) -> JSONResponse:
    return _problem(500, exc)


@app.exception_handler(descriptions.DescriptionsUnreadable)
def _descriptions_unreadable(request: Request, exc: Exception) -> JSONResponse:
    return _problem(500, exc)


@app.exception_handler(jev.KeyMissing)
def _jev_key_missing(request: Request, exc: Exception) -> JSONResponse:
    return _problem(503, exc)


@app.exception_handler(jev.JevError)
def _jev_error(request: Request, exc: Exception) -> JSONResponse:
    return _problem(502, exc)


@app.exception_handler(HttpError)
def _gmail_error(request: Request, exc: Exception) -> JSONResponse:
    return _problem(502, exc)

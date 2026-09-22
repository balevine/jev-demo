"""FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from googleapiclient.errors import HttpError

from jev_demo.api.routes import router
from jev_demo.config import get_settings
from jev_demo.services import (
    classify,
    descriptions,
    gmail_auth,
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

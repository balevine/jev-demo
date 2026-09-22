"""What every test needs before it builds a client.

The server only answers to names that mean this machine, and TestClient calls
itself `testserver`. Saying so here keeps the host check out of the way of
tests that are about something else. The tests covering the check itself set
this to whatever they are actually testing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jev_demo.config import get_settings


@pytest.fixture(autouse=True)
def allow_the_test_client_host(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Let requests from TestClient through the host check."""
    monkeypatch.setenv("JEV_DEMO_ALLOWED_HOSTS", "testserver")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

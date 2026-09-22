"""Listing labels, building the search query, and reading threads."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_demo.config import get_settings
from jev_demo.main import app
from jev_demo.schemas import ThreadSelection
from jev_demo.services import gmail_auth, gmail_client, token_store
from tests.helpers import FakeGmail, thread

LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "Label_19", "name": "Recruiting", "type": "user"},
    {"id": "Label_12", "name": "invoices", "type": "user"},
]

THREADS = [
    thread("18f", "Invoice #4021", "Your September invoice is ready."),
    thread("18g", "Coffee?", "Free on Thursday?"),
]


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """A fake Gmail standing in for the real one, with auth already satisfied."""
    service = FakeGmail(LABELS, THREADS)
    monkeypatch.setattr(gmail_auth, "require_credentials", lambda: object())
    monkeypatch.setattr(gmail_auth, "gmail_service", lambda credentials=None: service)
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    return service


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the real home directory out of these tests."""
    monkeypatch.setenv("JEV_DEMO_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_only_user_labels_come_back_and_they_are_sorted(gmail: FakeGmail) -> None:
    labels = gmail_client.list_labels(gmail)

    assert [(label.id, label.name) for label in labels] == [
        ("Label_12", "invoices"),
        ("Label_19", "Recruiting"),
    ]


def test_a_label_starts_with_no_description(gmail: FakeGmail) -> None:
    assert gmail_client.list_labels(gmail)[0].description == ""


def test_the_default_query_only_rules_out_chats() -> None:
    assert gmail_client.build_query(ThreadSelection()) == "-in:chats"


def test_since_becomes_an_after_term() -> None:
    selection = ThreadSelection(since=date(2026, 1, 1))

    assert gmail_client.build_query(selection) == "after:2026/01/01 -in:chats"


def test_between_becomes_a_range_that_includes_its_last_day() -> None:
    selection = ThreadSelection(between="2026-01-01..2026-02-01")

    assert gmail_client.build_query(selection) == "after:2026/01/01 before:2026/02/02 -in:chats"


def test_a_raw_query_is_added_on_top() -> None:
    selection = ThreadSelection(query="from:billing@acme.com")

    assert gmail_client.build_query(selection) == "from:billing@acme.com -in:chats"


@pytest.mark.parametrize(
    "value",
    ["2026-01-01", "2026-01-01..nonsense", "2026-02-01..2026-01-01"],
    ids=["no-separator", "not-a-date", "backwards"],
)
def test_a_bad_range_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        ThreadSelection(between=value)


def test_last_caps_how_many_threads_are_asked_for(gmail: FakeGmail) -> None:
    gmail_client.list_thread_ids(gmail, ThreadSelection(last=5))

    assert gmail.list_arguments == {"q": "-in:chats", "maxResults": 5}


def test_fetching_threads_gets_each_one_in_full(gmail: FakeGmail) -> None:
    threads = gmail_client.fetch_threads(ThreadSelection())

    assert gmail.fetched == ["18f", "18g"]
    assert [entry.subject for entry in threads] == ["Invoice #4021", "Coffee?"]
    assert threads[0].messages[0].body == "Your September invoice is ready."


def test_no_matching_threads_means_no_fetches(
    gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail.threads_by_id = {}

    assert gmail_client.fetch_threads(ThreadSelection()) == []
    assert gmail.fetched == []


def test_applying_labels_only_ever_adds_them(gmail: FakeGmail) -> None:
    gmail_client.apply_labels("18f", ["Label_12"], gmail)

    assert gmail.modified == [("18f", {"addLabelIds": ["Label_12"]})]


def test_the_apply_endpoint_writes_the_labels_it_was_given(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        body = client.post("/apply", json={"thread_id": "18f", "label_ids": ["Label_12"]}).json()

    assert body == {"thread_id": "18f", "applied": ["Label_12"]}
    assert gmail.modified == [("18f", {"addLabelIds": ["Label_12"]})]


def test_the_apply_endpoint_refuses_a_request_with_no_labels(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        response = client.post("/apply", json={"thread_id": "18f", "label_ids": []})

    assert response.status_code == 422
    assert gmail.modified == []


@pytest.mark.parametrize("label_id", ["TRASH", "SPAM", "UNREAD", "INBOX", "Label_nonexistent"])
def test_the_apply_endpoint_refuses_a_label_the_user_did_not_make(
    gmail: FakeGmail, label_id: str
) -> None:
    """Gmail takes its own label IDs here, and adding TRASH would bin the thread."""
    with TestClient(app) as client:
        response = client.post("/apply", json={"thread_id": "18f", "label_ids": [label_id]})

    assert response.status_code == 400
    assert label_id in response.json()["detail"]
    assert gmail.modified == []


def test_one_bad_label_stops_the_whole_write(gmail: FakeGmail) -> None:
    """Nothing is written at all, rather than the good ones going through first."""
    with TestClient(app) as client:
        response = client.post(
            "/apply", json={"thread_id": "18f", "label_ids": ["Label_12", "TRASH"]}
        )

    assert response.status_code == 400
    assert gmail.modified == []


def test_a_write_of_the_users_own_labels_is_let_through(gmail: FakeGmail) -> None:
    gmail_client.require_user_labels(["Label_12", "Label_19"], gmail)


def test_checking_labels_only_looks_at_the_ones_the_user_made(gmail: FakeGmail) -> None:
    with pytest.raises(gmail_client.UnknownLabels):
        gmail_client.require_user_labels(["INBOX"], gmail)


def test_the_labels_endpoint_returns_user_labels(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        body = client.get("/labels").json()

    assert body == [
        {"id": "Label_12", "name": "invoices", "description": ""},
        {"id": "Label_19", "name": "Recruiting", "description": ""},
    ]


def test_the_threads_endpoint_returns_parsed_threads(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        body = client.get("/threads", params={"last": 2}).json()

    assert [entry["subject"] for entry in body] == ["Invoice #4021", "Coffee?"]
    assert gmail.list_arguments["maxResults"] == 2


def test_the_threads_endpoint_refuses_a_bad_range(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        response = client.get("/threads", params={"between": "nonsense"})

    assert response.status_code == 422


def test_the_labels_endpoint_says_when_nobody_has_logged_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    monkeypatch.setattr(token_store, "load", lambda: None)

    with TestClient(app) as client:
        response = client.get("/labels")

    assert response.status_code == 401
    assert "jev auth" in response.json()["detail"]

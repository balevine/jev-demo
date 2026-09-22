"""Storing what each label means, and getting it into the question.

The last test is the one that matters. A description that is saved but never
reaches the rendered instructions is a setting that looks like it worked and
changes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_demo.config import get_settings
from jev_demo.main import app
from jev_demo.schemas import ThreadSelection
from jev_demo.services import descriptions, gmail_auth, gmail_client, prompts, token_store
from tests.helpers import FakeGmail, thread

LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "Label_12", "name": "Invoices", "type": "user"},
    {"id": "Label_19", "name": "Recruiting", "type": "user"},
]

THREADS = [thread("18f", "Invoice #4021", "Your September invoice is ready.")]

INVOICES = "receipts, bills, and payment requests"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the store at a fresh directory, so no test sees the real one."""
    directory = tmp_path / "home"
    monkeypatch.setenv("JEV_DEMO_HOME", str(directory))
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """A fake Gmail standing in for the real one, with auth already satisfied."""
    service = FakeGmail(LABELS, THREADS)
    monkeypatch.setattr(gmail_auth, "require_credentials", lambda: object())
    monkeypatch.setattr(gmail_auth, "gmail_service", lambda credentials=None: service)
    monkeypatch.setattr(token_store, "backend_name", lambda: "keyring.backends.macOS.Keyring")
    return service


def test_nothing_is_stored_until_something_is_saved(home: Path) -> None:
    assert descriptions.load() == {}
    assert not home.exists()


def test_a_saved_description_comes_back() -> None:
    descriptions.save({"Label_12": INVOICES})

    assert descriptions.load() == {"Label_12": INVOICES}


def test_saving_replaces_what_was_there_rather_than_adding_to_it() -> None:
    descriptions.save({"Label_12": INVOICES})
    descriptions.save({"Label_19": "job applications"})

    assert descriptions.load() == {"Label_19": "job applications"}


def test_a_blank_description_is_dropped_rather_than_stored() -> None:
    kept = descriptions.save({"Label_12": INVOICES, "Label_19": "   "})

    assert kept == {"Label_12": INVOICES}
    assert descriptions.load() == {"Label_12": INVOICES}


def test_surrounding_whitespace_is_trimmed() -> None:
    descriptions.save({"Label_12": f"  {INVOICES}\n"})

    assert descriptions.load() == {"Label_12": INVOICES}


def test_the_file_is_created_on_the_first_save(home: Path) -> None:
    descriptions.save({"Label_12": INVOICES})

    assert get_settings().descriptions_path.is_file()
    assert home.is_dir()


def test_a_file_that_is_not_json_says_so() -> None:
    get_settings().ensure_home_dir()
    get_settings().descriptions_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(descriptions.DescriptionsUnreadable, match="not valid JSON"):
        descriptions.load()


@pytest.mark.parametrize(
    "contents", ['["Label_12"]', '{"Label_12": 12}'], ids=["a-list", "a-number"]
)
def test_a_file_that_is_not_a_map_of_text_says_so(contents: str) -> None:
    get_settings().ensure_home_dir()
    get_settings().descriptions_path.write_text(contents, encoding="utf-8")

    with pytest.raises(descriptions.DescriptionsUnreadable, match="label ID"):
        descriptions.load()


def test_the_label_list_carries_the_stored_descriptions(gmail: FakeGmail) -> None:
    descriptions.save({"Label_12": INVOICES})

    labels = gmail_client.list_labels(gmail)

    assert [(label.id, label.description) for label in labels] == [
        ("Label_12", INVOICES),
        ("Label_19", ""),
    ]


def test_a_description_for_a_label_that_no_longer_exists_is_ignored(gmail: FakeGmail) -> None:
    descriptions.save({"Label_gone": "a label deleted in Gmail"})

    assert [label.description for label in gmail_client.list_labels(gmail)] == ["", ""]


def test_the_endpoints_round_trip_a_description(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        assert client.get("/label-descriptions").json() == {}

        saved = client.put("/label-descriptions", json={"Label_12": INVOICES, "Label_19": ""})

        assert saved.json() == {"Label_12": INVOICES}
        assert client.get("/label-descriptions").json() == {"Label_12": INVOICES}
        assert client.get("/labels").json() == [
            {"id": "Label_12", "name": "Invoices", "description": INVOICES},
            {"id": "Label_19", "name": "Recruiting", "description": ""},
        ]


def test_the_put_endpoint_refuses_something_that_is_not_a_map_of_text(gmail: FakeGmail) -> None:
    with TestClient(app) as client:
        response = client.put("/label-descriptions", json={"Label_12": 12})

    assert response.status_code == 422


def test_an_unreadable_file_is_reported_rather_than_traced(gmail: FakeGmail) -> None:
    get_settings().ensure_home_dir()
    get_settings().descriptions_path.write_text("{not json", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/label-descriptions")

    assert response.status_code == 500
    assert "not valid JSON" in response.json()["detail"]


def test_a_saved_description_shows_up_in_the_question_for_that_label(gmail: FakeGmail) -> None:
    # The whole point of the store. Everything above it is plumbing.
    with TestClient(app) as client:
        client.put("/label-descriptions", json={"Label_12": INVOICES})

    labels = gmail_client.list_labels(gmail)
    invoice = gmail_client.fetch_threads(ThreadSelection(last=1))[0]
    questions = prompts.render_questions(labels, invoice)

    assert INVOICES in questions["Label_12"]["instructions"]
    assert questions["Label_19"]["instructions"].endswith("belongs on this email thread.")

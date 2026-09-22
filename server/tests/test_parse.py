"""Getting readable text out of a Gmail message.

Walking the MIME tree is the part of this app that is easiest to get quietly
wrong, so these cover the shapes real mail actually arrives in.
"""

from __future__ import annotations

import pytest

from jev_demo.services import parse
from tests.helpers import encode, headers, message, part, text_payload

PLAINTEXT = text_payload(
    "The invoice is attached below.",
    from_="Ada <ada@example.com>",
    to="me@example.com",
    subject="Invoice",
)

ALTERNATIVE = {
    "mimeType": "multipart/alternative",
    "filename": "",
    "headers": headers(from_="Ada <ada@example.com>"),
    "body": {},
    "parts": [
        part("text/plain", "The plain one."),
        part("text/html", "<p>The HTML one.</p>"),
    ],
}

HTML_ONLY = {
    "mimeType": "text/html",
    "filename": "",
    "headers": headers(from_="Ada <ada@example.com>"),
    "body": {
        "data": encode(
            "<html><head><style>p { color: red }</style></head>"
            "<body><p>Your order shipped.</p><p>Tracking is 12&nbsp;345.</p>"
            "<script>track()</script></body></html>"
        )
    },
}

WITH_ATTACHMENT = {
    "mimeType": "multipart/mixed",
    "filename": "",
    "headers": headers(from_="Ada <ada@example.com>"),
    "body": {},
    "parts": [
        part("text/plain", "September invoice."),
        part("application/pdf", "%PDF-1.7 binary", filename="acme-september-invoice.pdf"),
    ],
}


@pytest.mark.parametrize(
    ("payload", "expected_body", "expected_attachments"),
    [
        (PLAINTEXT, "The invoice is attached below.", False),
        (ALTERNATIVE, "The plain one.", False),
        (HTML_ONLY, "Your order shipped.\nTracking is 12 345.", False),
        (WITH_ATTACHMENT, "September invoice.", True),
    ],
    ids=["plaintext", "multipart-alternative", "html-only", "attachment"],
)
def test_bodies_come_out_readable(
    payload: dict, expected_body: str, expected_attachments: bool
) -> None:
    parsed = parse.parse_message(message(payload))

    assert parsed.body == expected_body
    assert parsed.has_attachments is expected_attachments


def test_an_attachment_name_never_reaches_the_body() -> None:
    parsed = parse.parse_message(message(WITH_ATTACHMENT))

    assert "acme-september-invoice.pdf" not in parsed.body
    assert "PDF" not in parsed.body


def test_base64url_survives_missing_padding_and_url_safe_characters() -> None:
    # These characters force the + and / of standard base64, so a decoder that
    # is not url safe comes back wrong rather than merely empty.
    text = "Ünicode ~ ?? >> ÿÿÿ"

    assert parse.parse_message(message(text_payload(text))).body == text


def test_headers_are_read_without_regard_to_case() -> None:
    parsed = parse.parse_message(message(PLAINTEXT))

    assert parsed.sender == "Ada <ada@example.com>"
    assert parsed.recipient == "me@example.com"


def test_quoted_history_is_dropped() -> None:
    body = (
        "Sounds good, shipping it today.\n"
        "\n"
        "On Mon, Sep 21, 2026 at 9:14 AM Ada <ada@example.com>\n"
        "wrote:\n"
        "> Can you ship the September order?\n"
        "> Thanks.\n"
    )

    assert parse.parse_message(message(text_payload(body))).body == (
        "Sounds good, shipping it today."
    )


def test_stray_quoted_lines_go_even_without_an_attribution() -> None:
    assert parse.strip_quotes("Agreed.\n> the old text\nOne more thing.") == (
        "Agreed.\nOne more thing."
    )


def test_a_long_body_is_cut_at_the_budget_and_says_so() -> None:
    parsed = parse.parse_message(message(text_payload("x" * (parse.MAX_BODY_CHARS + 500))))

    assert parsed.body.endswith(parse.TRUNCATION_MARK)
    assert len(parsed.body) == parse.MAX_BODY_CHARS + len(parse.TRUNCATION_MARK)


def test_a_body_exactly_at_the_budget_is_left_alone() -> None:
    body = "x" * parse.MAX_BODY_CHARS

    assert parse.parse_message(message(text_payload(body))).body == body


def test_a_thread_takes_its_identity_from_the_first_message() -> None:
    thread = {
        "id": "18f",
        "messages": [
            message(PLAINTEXT, snippet="It&#39;s attached"),
            message(
                text_payload("Paid, thanks.", from_="me@example.com", to="ada@example.com"),
                internal_date="1789826531000",
            ),
        ],
    }

    parsed = parse.parse_thread(thread)

    assert parsed.id == "18f"
    assert parsed.subject == "Invoice"
    assert parsed.sender == "Ada <ada@example.com>"
    assert parsed.date == "2026-09-18T14:02:11Z"
    assert parsed.snippet == "It's attached"
    assert parsed.message_count == 2
    assert parsed.messages[1].body == "Paid, thanks."


def test_a_thread_over_the_state_budget_drops_its_oldest_messages() -> None:
    # Each body fills the per message budget, so two more than fit cannot fit.
    count = (parse.MAX_STATE_CHARS // parse.MAX_BODY_CHARS) + 2
    messages = [
        message(
            text_payload(f"{index} " + "x" * parse.MAX_BODY_CHARS, from_=f"p{index}@example.com")
        )
        for index in range(count)
    ]

    parsed = parse.parse_thread({"id": "18f", "messages": messages, "snippet": ""})

    assert parsed.message_count < count
    assert parsed.messages[-1].sender == f"p{count - 1}@example.com"
    assert parsed.messages[0].body.startswith(parse.TRUNCATION_MARK)
    assert sum(len(entry.body) for entry in parsed.messages[1:]) <= parse.MAX_STATE_CHARS


def test_a_date_header_stands_in_when_gmail_gives_no_internal_date() -> None:
    payload = text_payload("hello", date="Fri, 18 Sep 2026 10:02:11 -0400")

    assert parse.timestamp({"payload": payload}) == "2026-09-18T14:02:11Z"


def test_a_message_with_no_date_at_all_is_empty_rather_than_an_error() -> None:
    assert parse.timestamp({}) == ""


def test_an_empty_thread_parses_to_an_empty_thread() -> None:
    parsed = parse.parse_thread({"id": "18f"})

    assert parsed.message_count == 0
    assert parsed.subject == ""

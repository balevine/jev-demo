"""What each label means, in the user's own words.

A label name on its own is thin. "Ops" could be anything. The description is
the sentence that gets dropped into the question, so it is the one place a
person can steer what a label actually catches.

Descriptions live in ~/.jev-demo/GMAIL_LABEL_DESCRIPTIONS.json as a flat map of
Gmail label ID to text. Keying by ID rather than by name means renaming a label
in Gmail keeps its description, and a label deleted in Gmail simply stops
appearing. Its leftover entry sits there harmlessly.

The file does not exist until something is saved, and a mailbox with no
descriptions at all works fine. Every label just asks a question that names
itself and nothing more.
"""

from __future__ import annotations

import json

from jev_demo.config import get_settings
from jev_demo.schemas import Label


class DescriptionsUnreadable(RuntimeError):
    """Raised when the description file is there but is not what it should be."""


def load() -> dict[str, str]:
    """The stored descriptions, or an empty map when nothing has been saved."""
    path = get_settings().descriptions_path
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise DescriptionsUnreadable(f"{path} could not be read. {exc.strerror}.") from exc

    try:
        stored = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DescriptionsUnreadable(
            f"{path} is not valid JSON. {exc.msg}, at line {exc.lineno} column {exc.colno}."
        ) from exc

    if not isinstance(stored, dict) or not all(isinstance(value, str) for value in stored.values()):
        raise DescriptionsUnreadable(
            f"{path} has to be a JSON object mapping each label ID to a line of text."
        )
    return stored


def save(descriptions: dict[str, str]) -> dict[str, str]:
    """Replace the stored descriptions, and return what was kept.

    Blank text is dropped rather than stored. The settings screen sends a row
    for every label whether or not it was filled in, and an empty description
    and no description mean the same thing to the question template.
    """
    settings = get_settings()
    kept = {label_id: text.strip() for label_id, text in descriptions.items() if text.strip()}
    settings.ensure_home_dir()
    settings.descriptions_path.write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return kept


def describe(labels: list[Label]) -> list[Label]:
    """Attach each label's stored description, leaving it empty when it has none."""
    stored = load()
    return [label.model_copy(update={"description": stored.get(label.id, "")}) for label in labels]

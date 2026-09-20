import json
from uuid import uuid4
import pytest
from photobook_workshop.review_store import ReviewStore, Conflict
from photobook_workshop.book import options_hash

BOOK = {
    "bookId": "invented-garden",
    "pages": [
        {
            "id": "sun",
            "caption": "A sunny corner.",
            "options": [
                {"id": "gentle", "text": "The garden wakes."},
                {"id": "playful", "text": "Leaves stretch before breakfast."},
            ],
        },
        {"id": "moon", "options": [{"id": "quiet", "text": "The pond rests."}]},
    ],
}


def command(store, **kw):
    return {
        "bookId": BOOK["bookId"],
        "optionsHash": store.key,
        "pageId": "sun",
        "operationId": str(uuid4()),
        "baseRevision": 0,
        "optionId": "gentle",
        "note": "",
        **kw,
    }


def test_unselected_is_unselected_and_save_survives_restart(tmp_path):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    assert s.load()["choices"] == {}
    s.save(command(s))
    assert ReviewStore(BOOK, s.path).load()["choices"]["sun"]["optionId"] == "gentle"


def test_lost_reply_retry_does_not_overwrite_later_choice(tmp_path):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    first = command(s)
    s.save(first)
    s.save(command(s, baseRevision=1, optionId="playful"))
    reply = s.save(first)
    assert reply["acceptedRevision"] == 1 and reply["current"]["optionId"] == "playful"
    with pytest.raises(ValueError):
        s.save({**first, "note": "Changed payload"})


def test_two_tabs_conflict_preserves_both_and_other_pages_are_independent(tmp_path):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    stale = command(s, optionId="playful", note="A draft")
    s.save(command(s))
    with pytest.raises(Conflict) as info:
        s.save(stale)
    assert info.value.current["optionId"] == "gentle"
    assert stale["note"] == "A draft"
    s.save(command(s, pageId="moon", optionId="quiet"))
    s.save(
        command(s, baseRevision=1, optionId=None, note="Review a different sentence")
    )
    assert s.load()["choices"]["sun"]["optionId"] is None


def test_corrupt_or_changed_dataset_is_never_reset(tmp_path):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    s.path.write_text("{not-json")
    with pytest.raises(ValueError):
        s.load()
    assert s.path.read_text() == "{not-json"
    s.path.unlink()
    s.save(command(s))
    before = s.path.read_bytes()
    other = {
        **BOOK,
        "pages": [
            {"id": "sun", "options": [{"id": "different", "text": "Changed wording"}]}
        ],
    }
    with pytest.raises(ValueError):
        ReviewStore(other, s.path).load()
    assert s.path.read_bytes() == before


def test_invalid_saves_do_not_create_a_file(tmp_path):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    for kw in [
        {"pageId": "missing"},
        {"optionId": "missing"},
        {"baseRevision": True},
        {"operationId": "bad"},
        {"note": "x" * 3001},
    ]:
        with pytest.raises(ValueError):
            s.save(command(s, **kw))
    assert not s.path.exists()


def test_deleted_established_file_cannot_be_recreated_by_stale_write(tmp_path):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    stale = command(s)
    s.save(command(s))
    s.path.unlink()
    reopened = ReviewStore(BOOK, s.path)
    with pytest.raises(ValueError):
        reopened.load()
    with pytest.raises(ValueError):
        reopened.save(stale)
    assert not s.path.exists()


@pytest.mark.parametrize(
    "mutation",
    ["missing-option", "negative-receipt", "missing-receipt-page", "future-receipt"],
)
def test_incomplete_or_inconsistent_durable_state_is_never_accepted(tmp_path, mutation):
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    cmd = command(s)
    s.save(cmd)
    state = json.loads(s.path.read_text())
    if mutation == "missing-option":
        del state["choices"]["sun"]["optionId"]
    elif mutation == "negative-receipt":
        state["receipts"][cmd["operationId"]]["revision"] = -8
    elif mutation == "missing-receipt-page":
        state["receipts"][cmd["operationId"]].pop("pageId", None)
    else:
        state["receipts"][cmd["operationId"]]["revision"] = 10
    s.path.write_text(json.dumps(state))
    before = s.path.read_bytes()
    with pytest.raises(ValueError):
        s.load()
    with pytest.raises(ValueError):
        s.save(cmd)
    assert s.path.read_bytes() == before


def test_unicode_survives_non_utf8_platform_default_and_unrelated_save(
    tmp_path, monkeypatch
):
    from pathlib import Path

    original = Path.read_text

    def legacy_default(path, encoding=None, errors=None):
        return original(path, encoding=encoding or "cp1252", errors=errors)

    monkeypatch.setattr(Path, "read_text", legacy_default)
    s = ReviewStore(BOOK, tmp_path / "choices.json")
    s.save(command(s, note="Café, naïve, 星."))
    assert (
        ReviewStore(BOOK, s.path).load()["choices"]["sun"]["note"] == "Café, naïve, 星."
    )
    s.save(command(s, pageId="moon", optionId="quiet"))
    assert s.load()["choices"]["sun"]["note"] == "Café, naïve, 星."

import copy
from pathlib import Path
import pytest
from photobook_workshop.book import load_book, validate_book
from photobook_workshop.proof import build_book, paper_proofs, render_pdf

ROOT = Path(__file__).parents[1] / "src/photobook_workshop/demo"


def test_cover_scrim_choices_change_actual_pdf(tmp_path):
    book = load_book(ROOT / "book.json")
    book["pages"] = [book["pages"][0]]
    a = tmp_path / "none.pdf"
    b = tmp_path / "bottom.pdf"
    book["pages"][0]["scrim"] = "none"
    render_pdf(book, ROOT, a)
    book["pages"][0]["scrim"] = "bottom"
    render_pdf(book, ROOT, b)
    assert a.read_bytes() != b.read_bytes()


def test_short_caption_groups_stay_together_across_page_breaks(tmp_path):
    book = {
        "title": "A garden of invented sentences",
        "pages": [
            {
                "id": f"p{i}",
                "subject": f"Subject {i}",
                "caption": "A sentence.",
                "options": [
                    {"id": "a", "text": "First short option."},
                    {"id": "b", "text": "Second short option."},
                ],
            }
            for i in range(20)
        ],
    }
    result = paper_proofs(book, tmp_path)
    for kind, report in result.items():
        for p in book["pages"]:
            assert (
                len({r["sheet"] for r in report["rows"] if r["pageId"] == p["id"]}) == 1
            ), (kind, p["id"])


def test_placeholders_text_panels_dots_and_explicit_exclusions_survive_both_renderers(
    tmp_path,
):
    b = {
        "schemaVersion": 1,
        "bookId": "invented-slots",
        "title": "An empty place for a future photo",
        "assets": [],
        "pages": [
            {
                "id": "opening",
                "kind": "rows",
                "subject": "A place to begin",
                "caption": "Words for later.",
                "cropLimit": 0.5,
                "images": [
                    {
                        "id": "slot",
                        "placeholder": {
                            "width": 600,
                            "height": 400,
                            "label": "Choose a garden photograph",
                        },
                    }
                ],
                "rows": [{"images": ["slot"], "height": 300, "y": 20}],
                "pattern": "dots",
                "textBlocks": [
                    {
                        "id": "side-note",
                        "text": "A small extra thought.",
                        "rect": [30, 330, 500, 60],
                        "size": 20,
                    }
                ],
            }
        ],
    }
    book = validate_book(b, tmp_path)
    out = tmp_path / "proof"
    report = build_book(book, tmp_path, out)
    html = (out / "html/pages/opening.html").read_text()
    assert (
        "Choose a garden photograph" in html
        and "A small extra thought." in html
        and "radial-gradient" in html
    )
    assert (
        report["pdf"]["pages"][0]["placements"][0]["placeholder"]["label"]
        == "Choose a garden photograph"
    )
    assert not report["pdf"]["images"]


def test_failed_build_keeps_previous_complete_proof_and_removes_only_new_folder(
    tmp_path,
):
    book = load_book(ROOT / "book.json")
    out = tmp_path / "good"
    report = build_book(book, ROOT, out)
    original = (out / "book-proof.pdf").read_bytes()
    with pytest.raises(FileExistsError):
        build_book(book, ROOT, out)
    bad = copy.deepcopy(book)
    bad["pages"][0]["caption"] = "A very long thought. " * 400
    with pytest.raises(ValueError):
        build_book(bad, ROOT, tmp_path / "bad")
    assert (
        not (tmp_path / "bad").exists()
        and (out / "book-proof.pdf").read_bytes() == original
    )
    assert report["pdf"]["source_min_ppi"] >= report["pdf"]["embedded_min_ppi"]


def test_long_option_flows_with_its_heading_without_blank_sheets(tmp_path):
    sentence = "word " * 732
    book = {
        "title": "Long invented review",
        "pages": [
            {
                "id": "long",
                "subject": "W" * 200,
                "caption": sentence,
                "options": [{"id": "long-option", "text": sentence}],
            }
        ],
    }
    reports = paper_proofs(book, tmp_path)
    for report in reports.values():
        for sheet in range(1, report["sheets"] + 1):
            rows = [r for r in report["rows"] if r["sheet"] == sheet]
            assert rows and any(r.get("role") == "wording" for r in rows)
        wording = " ".join(
            r["text"]
            for r in report["rows"]
            if r.get("role") == "wording" and r["optionId"] != "write-in"
        )
        assert wording.split() == sentence.split()

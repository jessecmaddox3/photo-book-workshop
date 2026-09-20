from copy import deepcopy
from pathlib import Path
from PIL import Image
import pytest
from photobook_workshop.book import normalize_asset, validate_book
from photobook_workshop.layout import plan_page


def make_book(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), "red").save(source)
    return {
        "schemaVersion": 1,
        "bookId": "sample",
        "title": "Sample",
        "physical": {"width": 8.01},
        "assets": [{"id": "wide", "path": "wide.png"}],
        "pages": [
            {
                "id": "wide",
                "kind": "rows",
                "caption": "",
                "images": [{"id": "a", "asset": "wide"}],
                "rows": [{"height": 100, "images": ["a"]}],
                "gutter": 0,
                "cropLimit": 0.95,
            }
        ],
    }


def test_placement_cannot_override_verified_asset_dimensions(tmp_path):
    b = make_book(tmp_path)
    b["pages"][0]["images"][0].update(width=100, height=100)
    with pytest.raises(ValueError):
        validate_book(b, tmp_path)


def test_text_page_cannot_silently_drop_selected_images(tmp_path):
    b = make_book(tmp_path)
    b["pages"][0]["kind"] = "text"
    with pytest.raises(ValueError):
        validate_book(b, tmp_path)


def test_modified_same_size_derivative_is_preserved_and_rejected(tmp_path):
    b = make_book(tmp_path)
    out = tmp_path / "normalized"
    a = normalize_asset(tmp_path / "wide.png", out, "wide")
    target = out / a["path"]
    Image.new("RGB", (400, 100), "blue").save(target)
    before = target.read_bytes()
    with pytest.raises(ValueError):
        normalize_asset(tmp_path / "wide.png", out, "wide")
    assert target.read_bytes() == before


def test_rows_close_at_fractional_physical_width(tmp_path):
    b = validate_book(make_book(tmp_path), tmp_path)
    p = plan_page(b["pages"][0], {a["id"]: a for a in b["assets"]}, b["physical"])
    rect = p["placements"][0]["rect"]
    assert rect[0] + rect[2] == pytest.approx(p["width"])

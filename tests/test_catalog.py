import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from photobook_workshop.photo_catalog import (
    scan_archives,
    prepare_batch,
    validate_caption_result,
    ingest_caption_result,
)

URL = "https://example.invalid/photos/illustration-001"
MEMBER = "Takeout/Google Photos/Cloud Garden/scene.png"


def picture():
    image = Image.new("RGBA", (90, 60), (20, 100, 140, 128))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def sidecar(**changes):
    return {
        "url": URL,
        "title": "scene.png",
        "photoTakenTime": {"timestamp": "0"},
        "geoData": {"latitude": 0, "longitude": 0},
        "people": [],
        **changes,
    }


def archive(path, *, member=MEMBER, metadata=None, media=False):
    with zipfile.ZipFile(path, "w") as z:
        if metadata is not None:
            z.writestr(member + ".supplemental-metadata.json", json.dumps(metadata))
        if media:
            z.writestr(member, picture())
    return path


def row(db):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM photos").fetchone())


def test_media_only_part_recovers_missing_photo_without_rescanning_sidecar(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "one.zip", metadata=sidecar())
    b = archive(tmp_path / "two.zip", media=True)
    scan_archives([a], db)
    assert row(db)["status"] == "missing_media"
    scan_archives([b], db)
    assert row(db)["status"] == "pending"
    assert len(prepare_batch(db, tmp_path / "work")["items"]) == 1


def test_multiple_albums_and_partial_rescan_preserve_source_and_captions(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "one.zip", metadata=sidecar(), media=True)
    b = archive(
        tmp_path / "two.zip",
        member=MEMBER.replace("Cloud Garden", "Favorite Scenes"),
        metadata=sidecar(),
    )
    scan_archives([a, b], db)
    assert row(db)["source_zip"] == str(a.resolve())
    with sqlite3.connect(db) as c:
        assert {
            r[0]
            for r in c.execute("SELECT album FROM photo_albums WHERE url=?", (URL,))
        } == {"Cloud Garden", "Favorite Scenes"}
        c.execute("UPDATE photos SET status='captioned',caption_json='{}'")
        c.commit()
    scan_archives([b], db)
    assert row(db)["status"] == "captioned"
    assert row(db)["source_zip"] == str(a.resolve())


def test_zero_coordinates_and_epoch_are_values(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    scan_archives([archive(tmp_path / "a.zip", metadata=sidecar(), media=True)], db)
    r = row(db)
    assert r["latitude"] == 0 and r["longitude"] == 0 and r["taken_ts"] == 0
    assert r["taken_iso"] == "1970-01-01T00:00:00+00:00"


def test_malformed_sidecars_do_not_stop_valid_records(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    good = archive(
        tmp_path / "good.zip",
        metadata=sidecar(photoTakenTime=[], people=["invalid"], geoData=[]),
        media=True,
    )
    bad = archive(tmp_path / "bad.zip", metadata=[])
    result = scan_archives([bad, good], db)
    assert result["photos"] == 1 and result["invalid_sidecars"] == 1
    assert row(db)["people_json"] == "[]"


def caption():
    return {
        "url": URL,
        "caption": "A painted balloon above an invented valley.",
        "keywords": ["balloon"],
        "unnamed_people": [],
        "pets": [],
        "quality": {
            "sharpness": "good",
            "exposure": "good",
            "eyes_open": "not_applicable",
            "faces_uncut": "not_applicable",
        },
        "print_recommendation": True,
        "print_reason": "Clear illustrative shapes.",
        "hero_candidate": False,
        "composition": {
            "subject_position": "center",
            "square_crop": "possible",
            "headroom": "Room above.",
        },
        "visible_text": [],
        "non_photo": True,
        "non_photo_type": "illustration",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"hero_candidate": "yes"},
        {"quality": {"sharpness": "excellent"}},
        {"keywords": [9]},
        {"unexpected": "field"},
    ],
)
def test_manual_caption_ingestion_uses_full_schema(change):
    with pytest.raises(ValueError):
        validate_caption_result({"captions": [{**caption(), **change}]}, [URL])


def test_missing_archive_is_resumable_and_default_preview_context_is_minimal(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "a.zip", metadata=sidecar(), media=True)
    scan_archives([a], db)
    a.rename(tmp_path / "away.zip")
    failed = prepare_batch(db, tmp_path / "work")
    assert len(failed["errors"]) == 1
    (tmp_path / "away.zip").rename(a)
    result = prepare_batch(db, tmp_path / "work")
    assert len(result["items"]) == 1
    assert not (
        {"latitude", "longitude", "people", "album", "taken_at", "title"}
        & result["items"][0].keys()
    )
    with Image.open(result["items"][0]["preview"]) as image:
        assert image.mode == "RGB" and not image.getexif()


def test_bad_reimport_preserves_caption_and_duplicates_fail(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    scan_archives([archive(tmp_path / "a.zip", metadata=sidecar(), media=True)], db)
    ingest_caption_result(db, {"captions": [caption()]}, [URL])
    before = row(db)
    with pytest.raises(ValueError):
        ingest_caption_result(
            db, {"captions": [{**caption(), "hero_candidate": "yes"}]}, [URL]
        )
    assert row(db) == before
    with pytest.raises(ValueError):
        validate_caption_result({"captions": [caption(), caption()]}, [URL])


def test_export_cannot_overwrite_database_or_archive(tmp_path):
    from photobook_workshop.photo_catalog import export_jsonl

    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "a.zip", metadata=sidecar(), media=True)
    scan_archives([a], db)
    alias = tmp_path / "alias.jsonl"
    alias.hardlink_to(db)
    for target in [db, alias, a]:
        before = target.read_bytes()
        with pytest.raises(ValueError):
            export_jsonl(db, target)
        assert target.read_bytes() == before
    output = tmp_path / "catalog.jsonl"
    assert export_jsonl(db, output) == 1
    assert json.loads(output.read_text())["albums"] == ["Cloud Garden"]


def test_rescan_recovers_when_old_zip_exists_but_lost_member(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "a.zip", metadata=sidecar(), media=True)
    scan_archives([a], db)
    with zipfile.ZipFile(a, "w"):
        pass
    b = archive(tmp_path / "b.zip", media=True)
    scan_archives([b], db)
    assert row(db)["source_zip"] == str(b.resolve())
    assert len(prepare_batch(db, tmp_path / "work")["items"]) == 1


def test_palette_transparency_becomes_white(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "a.zip", metadata=sidecar())
    image = Image.new("P", (60, 60), 0)
    image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    buf = io.BytesIO()
    image.save(buf, "PNG", transparency=0)
    with zipfile.ZipFile(a, "a") as z:
        z.writestr(MEMBER, buf.getvalue())
    scan_archives([a], db)
    result = prepare_batch(db, tmp_path / "work")
    with Image.open(result["items"][0]["preview"]) as out:
        assert min(out.getpixel((30, 30))) >= 250


def test_caption_import_cannot_report_success_for_unknown_database_identity(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    scan_archives([archive(tmp_path / "a.zip", metadata=sidecar(), media=True)], db)
    unknown = "https://example.invalid/photos/missing"
    with pytest.raises(ValueError):
        ingest_caption_result(
            db, {"captions": [{**caption(), "url": unknown}]}, [unknown]
        )
    assert row(db)["status"] == "pending"


def test_all_known_archive_versions_remain_protected_and_decoding_can_fall_back(
    tmp_path,
):
    from photobook_workshop.photo_catalog import export_jsonl

    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "a.zip", metadata=sidecar(), media=True)
    scan_archives([a], db)
    b = archive(tmp_path / "b.zip", metadata=sidecar(), media=True)
    scan_archives([b], db)
    for target in [a, b]:
        before = target.read_bytes()
        with pytest.raises(ValueError):
            export_jsonl(db, target)
        assert target.read_bytes() == before
    with zipfile.ZipFile(a, "w") as z:
        z.writestr(MEMBER, b"broken image")
    result = prepare_batch(db, tmp_path / "work")
    assert len(result["items"]) == 1 and not result["errors"]
    assert row(db)["source_zip"] == str(b.resolve())


def test_rgb_transparency_becomes_white(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    a = archive(tmp_path / "a.zip", metadata=sidecar())
    image = Image.new("RGB", (60, 60), (255, 0, 0))
    buf = io.BytesIO()
    image.save(buf, "PNG", transparency=(255, 0, 0))
    with zipfile.ZipFile(a, "a") as z:
        z.writestr(MEMBER, buf.getvalue())
    scan_archives([a], db)
    result = prepare_batch(db, tmp_path / "work")
    with Image.open(result["items"][0]["preview"]) as out:
        assert min(out.getpixel((30, 30))) >= 250

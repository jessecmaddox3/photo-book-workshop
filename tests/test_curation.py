import json
import pytest
from photobook_workshop.curation import rank_candidates, selected_ids


def records():
    return [
        {
            "id": str(i),
            "people": ["River", "Sky"] if i == 1 else ["Sky"],
            "albums": ["Invented Garden", "Favorite Scenes"]
            if i == 1
            else ["Invented Garden"],
            "taken_at": date,
            "caption": {
                "caption": "A green balloon.",
                "keywords": ["balloon"],
                "print_recommendation": True,
                "hero_candidate": i == 1,
                "quality": {"sharpness": "good", "exposure": "good"},
                "non_photo": False,
            },
        }
        for i, date in [(1, "2030-01-01"), (2, None), (3, None), (4, "2030-01-01")]
    ]


def test_filters_quality_stable_ids_and_unknown_date_diversity():
    r = records()
    q = {
        "allPeople": ["River"],
        "anyPeople": ["Sky"],
        "albums": ["Favorite Scenes"],
        "terms": ["balloon"],
        "since": "2030-01-01",
        "before": "2030-01-02",
    }
    assert [p["id"] for p in rank_candidates(r, q)] == ["1"]
    assert [p["id"] for p in rank_candidates(r, {"dateDiversity": True})] == [
        "1",
        "2",
        "3",
    ]
    r[1]["caption"]["non_photo"] = True
    assert "2" not in [p["id"] for p in rank_candidates(r, {})]
    assert "2" in [p["id"] for p in rank_candidates(r, {"includeNonPhotos": True})]
    r[2]["caption"]["quality"]["sharpness"] = "poor"
    assert rank_candidates(r, {"includeNonPhotos": True})[-1]["id"] == "3"


def test_selections_bind_pool_hash_and_positions_become_ids():
    r = rank_candidates(records(), {})
    from photobook_workshop.book import digest

    pool = {"schemaVersion": 1, "candidates": r, "poolHash": digest(r)}
    assert selected_ids(pool, {"poolHash": pool["poolHash"], "positions": [1, 3]}) == [
        r[0]["id"],
        r[2]["id"],
    ]
    assert selected_ids(pool, {"poolHash": pool["poolHash"], "ids": ["2", "1"]}) == [
        "2",
        "1",
    ]
    with pytest.raises(ValueError):
        selected_ids(pool, {"poolHash": "stale", "positions": [1]})
    with pytest.raises(ValueError):
        selected_ids(pool, {"poolHash": pool["poolHash"], "positions": [1, 1]})
    pool["candidates"].reverse()
    with pytest.raises(ValueError):
        selected_ids(pool, {"poolHash": pool["poolHash"], "positions": [1]})


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 1001},
        {"since": "tomorrow"},
        {"before": "2020-99-99"},
        {"terms": ["x" * 201]},
        {"wat": True},
    ],
)
def test_invalid_queries_fail(query):
    with pytest.raises(ValueError):
        rank_candidates(records(), query)


def test_duplicates_fail_instead_of_selecting_wrong_photo():
    with pytest.raises(ValueError):
        rank_candidates(records() * 2, {})


def test_catalog_coordinates_reach_geographic_filter(tmp_path):
    import sqlite3
    from photobook_workshop.photo_catalog import SCHEMA
    from photobook_workshop.curation import catalog_records

    db = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(db) as c:
        c.executescript(SCHEMA)
        c.execute(
            "INSERT INTO photos(url,album,title,people_json,raw_metadata_json,media_ext,updated_at,latitude,longitude) VALUES('invented-zero','Garden','Scene','[]','{}','.png','2030-01-01',0,0)"
        )
    found = rank_candidates(catalog_records(db), {"bounds": [-1, -1, 1, 1]})
    assert len(found) == 1


def test_decoder_size_rejection_still_tries_another_archive(tmp_path):
    import io, sqlite3, zipfile, struct, zlib
    from PIL import Image
    from photobook_workshop.photo_catalog import SCHEMA
    from photobook_workshop.curation import resolve_photo

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(buf, "PNG")
    valid = buf.getvalue()
    bad = bytearray(valid)
    bad[16:24] = struct.pack(">II", 200000, 200000)
    bad[29:33] = struct.pack(">I", zlib.crc32(bad[12:29]))
    db = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(db) as c:
        c.executescript(SCHEMA)
        for name, data in [("a.zip", bad), ("b.zip", valid)]:
            p = tmp_path / name
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("scene.png", data)
            c.execute(
                "INSERT INTO photo_sources VALUES(?,?,?)",
                ("invented", "scene.png", str(p)),
            )
    assert resolve_photo(db, "invented") == valid

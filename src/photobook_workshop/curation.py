"""Search candidate pools, numbered contact sheets and stable-ID selections."""

from __future__ import annotations
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .book import atomic_json, digest, number, normalize_asset, normalized_image


def catalog_records(db):
    with closing(
        sqlite3.connect(Path(db).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    ) as c:
        c.row_factory = sqlite3.Row
        records = []
        for row in c.execute(
            "SELECT url,taken_iso,people_json,caption_json,latitude,longitude FROM photos ORDER BY url"
        ):
            caption = json.loads(row["caption_json"]) if row["caption_json"] else {}
            records.append(
                {
                    "id": hashlib.sha256(row["url"].encode()).hexdigest(),
                    "url": row["url"],
                    "taken_at": row["taken_iso"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "people": json.loads(row["people_json"]),
                    "albums": [
                        r[0]
                        for r in c.execute(
                            "SELECT album FROM photo_albums WHERE url=? ORDER BY album",
                            (row["url"],),
                        )
                    ],
                    "caption": caption,
                }
            )
        return records


def rank_candidates(records, query):
    allowed = {
        "allPeople",
        "anyPeople",
        "albums",
        "terms",
        "boostTerms",
        "bonusPeople",
        "personBonus",
        "since",
        "before",
        "bounds",
        "includeNonPhotos",
        "dateDiversity",
        "limit",
    }
    if not isinstance(query, dict) or set(query) - allowed:
        raise ValueError("Unknown candidate query field")
    terms = {}
    for key in [
        "allPeople",
        "anyPeople",
        "albums",
        "terms",
        "boostTerms",
        "bonusPeople",
    ]:
        values = query.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 50
            or any(not isinstance(x, str) or not 0 < len(x) <= 200 for x in values)
        ):
            raise ValueError("Search fields need up to 50 short literal strings")
        terms[key] = {v.casefold() for v in values}
    limit = query.get("limit", 60)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Choose 1-1000 candidates")
    bonus = number(query.get("personBonus", 1), 0, 10, "person bonus")
    since = date.fromisoformat(query["since"]) if query.get("since") else None
    before = date.fromisoformat(query["before"]) if query.get("before") else None
    if since and before and since >= before:
        raise ValueError("Date range is empty")
    bounds = query.get("bounds")
    if bounds is not None:
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("Bounds need south, west, north, east")
        for i, v in enumerate(bounds):
            number(
                v, -90 if i % 2 == 0 else -180, 90 if i % 2 == 0 else 180, "coordinate"
            )
        if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            raise ValueError("Use ordered coordinate bounds")
    seen = set()
    found = []
    for record in records:
        ident = record.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError("Missing or duplicate candidate identity")
        seen.add(ident)
        caption = record.get("caption") or {}
        people = {s.casefold() for s in record.get("people", [])}
        albums = {s.casefold() for s in record.get("albums", [])}
        if caption.get("non_photo") and not query.get("includeNonPhotos", False):
            continue
        if (
            not terms["allPeople"] <= people
            or (terms["anyPeople"] and not terms["anyPeople"] & people)
            or (terms["albums"] and not terms["albums"] & albums)
        ):
            continue
        try:
            taken = date.fromisoformat((record.get("taken_at") or "")[:10])
        except ValueError:
            taken = None
        if (since and (taken is None or taken < since)) or (
            before and (taken is None or taken >= before)
        ):
            continue
        if bounds is not None:
            lat, lon = record.get("latitude"), record.get("longitude")
            if (
                lat is None
                or lon is None
                or not bounds[0] <= lat <= bounds[2]
                or not bounds[1] <= lon <= bounds[3]
            ):
                continue
        searchable = " ".join(
            [
                caption.get("caption", ""),
                *caption.get("keywords", []),
                *caption.get("visible_text", []),
            ]
        ).casefold()
        if any(term not in searchable for term in terms["terms"]):
            continue
        quality = caption.get("quality", {})
        score = 3 * bool(caption.get("print_recommendation")) + 5 * bool(
            caption.get("hero_candidate")
        )
        score += {"good": 2, "fair": 0, "poor": -4}.get(quality.get("sharpness"), 0) + {
            "good": 1,
            "fair": 0,
            "poor": -3,
        }.get(quality.get("exposure"), 0)
        score -= 2 * sum(quality.get(k) == "no" for k in ["eyes_open", "faces_uncut"])
        score += bonus * len(terms["bonusPeople"] & people) + 2 * sum(
            term in searchable for term in terms["boostTerms"]
        )
        found.append(
            {**record, "score": score, "dateKey": taken.isoformat() if taken else None}
        )
    found.sort(key=lambda r: (-r["score"], r["id"]))
    answer = []
    dates = set()
    for record in found:
        key = record["dateKey"] or ("unknown:" + record["id"])
        if query.get("dateDiversity") and key in dates:
            continue
        answer.append(record)
        dates.add(key)
        if len(answer) == limit:
            break
    return answer


def selected_ids(pool, selection):
    candidates = pool["candidates"]
    key = digest(candidates)
    if pool.get("poolHash") != key or selection.get("poolHash") != key:
        raise ValueError(
            "This selection belongs to another contact sheet or pool ordering"
        )
    ids = [r["id"] for r in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate pool identity")
    if ("ids" in selection) == ("positions" in selection):
        raise ValueError("Choose IDs or numbered positions")
    if "ids" in selection:
        chosen = selection["ids"]
    else:
        positions = selection["positions"]
        if not isinstance(positions, list) or any(
            type(p) is not int or not 1 <= p <= len(ids) for p in positions
        ):
            raise ValueError("Contact sheet position is out of range")
        chosen = [ids[p - 1] for p in positions]
    if (
        not isinstance(chosen, list)
        or len(set(chosen)) != len(chosen)
        or any(p not in ids for p in chosen)
    ):
        raise ValueError("Missing or repeated selection")
    return chosen


def resolve_photo(db, url):
    """Return a verified decodable source from explicit catalog archive pointers."""
    with closing(
        sqlite3.connect(Path(db).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    ) as c:
        sources = c.execute(
            "SELECT source_zip,media_member FROM photo_sources WHERE url=? AND source_zip!='' ORDER BY source_zip,media_member",
            (url,),
        ).fetchall()
    for archive, member in sources:
        try:
            with zipfile.ZipFile(archive) as z:
                if z.getinfo(member).file_size > 128_000_000:
                    continue
                raw = z.read(member)
            image = normalized_image(raw)
            image.close()
            return raw
        except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile):
            continue
    raise ValueError("No usable archive source for selected catalog identity")


def contact_sheets(pool, output, resolver, *, per_sheet=12):
    if type(per_sheet) is not int or not 1 <= per_sheet <= 24:
        raise ValueError("Use 1-24 images per contact sheet")
    if pool.get("poolHash") != digest(pool["candidates"]):
        raise ValueError("Pool content changed")
    output = Path(output)
    if output.exists():
        raise FileExistsError("Choose a new contact-sheet folder")
    output.mkdir(parents=True)
    rows = []
    files = []
    font = ImageFont.truetype(str(Path(__file__).parent / "fonts/Fredoka.ttf"), 19)
    try:
        for start in range(0, len(pool["candidates"]), per_sheet):
            group = pool["candidates"][start : start + per_sheet]
            sheet = Image.new("RGB", (1080, math_rows(len(group)) * 300), "white")
            draw = ImageDraw.Draw(sheet)
            for offset, record in enumerate(group):
                i = start + offset
                x = (offset % 3) * 360
                y = (offset // 3) * 300
                image = normalized_image(resolver(record))
                image.thumbnail((336, 235), Image.Resampling.LANCZOS)
                sheet.paste(
                    image, (x + (360 - image.width) // 2, y + (245 - image.height) // 2)
                )
                image.close()
                label = f"{i + 1}. {record.get('dateKey') or 'Undated'}   {record['id'][:12]}"
                draw.text((x + 12, y + 250), label, font=font, fill="black")
                rows.append(
                    {
                        "position": i + 1,
                        "id": record["id"],
                        "sheet": start // per_sheet + 1,
                        "label": label,
                    }
                )
            name = f"sheet-{start // per_sheet + 1:03d}.jpg"
            sheet.save(output / name, quality=92)
            files.append(name)
        atomic_json(
            output / "index.json",
            {"poolHash": pool["poolHash"], "rows": rows, "sheets": files},
        )
        (output / "index.txt").write_text(
            "\n".join(f"{r['position']}\t{r['id']}\t{r['label']}" for r in rows) + "\n",
            encoding="utf-8",
        )
        return {"count": len(rows), "sheets": files}
    except Exception:
        shutil.rmtree(output)
        raise


def math_rows(count):
    return (count + 2) // 3


def normalize_selection(pool, selection, output, resolver):
    chosen = selected_ids(pool, selection)
    records = {r["id"]: r for r in pool["candidates"]}
    output = Path(output)
    if output.exists():
        raise FileExistsError("Choose a new selected-assets folder")
    output.mkdir(parents=True)
    assets = []
    try:
        for ident in chosen:
            raw = resolver(records[ident])
            with tempfile.TemporaryDirectory(dir=output) as temporary:
                source = Path(temporary) / "source"
                source.write_bytes(raw)
                assets.append(normalize_asset(source, output, ident))
        atomic_json(
            output / "selection.json",
            {
                "schemaVersion": 1,
                "poolHash": pool["poolHash"],
                "ids": chosen,
                "assets": assets,
            },
        )
        return assets
    except Exception:
        shutil.rmtree(output)
        raise

"""Build and query a resumable catalog from Google Photos Takeout ZIP parts."""

from __future__ import annotations

import argparse

from contextlib import closing

import hashlib

import io

import json
import math
import os
import tempfile
from jsonschema import Draft202012Validator

import sqlite3

import subprocess

import uuid

import zipfile

from datetime import datetime, timezone

from pathlib import Path

from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError

SIDECAR_SUFFIX = ".supplemental-metadata.json"

STILL_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".webp",
    ".gif",
    ".tif",
    ".tiff",
    ".bmp",
    ".dng",
    ".arw",
    ".cr2",
    ".nef",
    ".raf",
}

REQUIRED_CAPTION_FIELDS = {
    "url",
    "caption",
    "keywords",
    "unnamed_people",
    "pets",
    "quality",
    "print_recommendation",
    "print_reason",
    "hero_candidate",
    "composition",
    "visible_text",
    "non_photo",
    "non_photo_type",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    url TEXT PRIMARY KEY,
    album TEXT NOT NULL,
    title TEXT NOT NULL,
    taken_ts INTEGER,
    taken_iso TEXT,
    latitude REAL,
    longitude REAL,
    people_json TEXT NOT NULL,
    raw_metadata_json TEXT NOT NULL,
    source_zip TEXT,
    media_member TEXT,
    media_ext TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    caption_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photo_albums (url TEXT NOT NULL, album TEXT NOT NULL, PRIMARY KEY(url,album));
CREATE TABLE IF NOT EXISTS photo_sources (url TEXT NOT NULL, media_member TEXT NOT NULL, source_zip TEXT NOT NULL, PRIMARY KEY(url,media_member,source_zip));
CREATE INDEX IF NOT EXISTS photos_status_idx ON photos(status);
CREATE INDEX IF NOT EXISTS photos_taken_idx ON photos(taken_ts);
CREATE INDEX IF NOT EXISTS photos_album_idx ON photos(album);
"""


def _album_and_relative_name(member: str) -> tuple[str, str] | None:
    prefix = "Takeout/Google Photos/"
    if not member.startswith(prefix):
        return None
    rest = member[len(prefix) :]
    if "/" not in rest:
        return None
    return rest.split("/", 1)


def _iso_time(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _coordinate(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and abs(value) <= maximum else None


def scan_archives(
    zip_paths: Iterable[Path | str], db_path: Path | str
) -> dict[str, int]:
    """Index explicit Takeout ZIPs; preserve all albums and recovered media pointers."""
    paths = list(dict.fromkeys(Path(path).resolve(strict=True) for path in zip_paths))
    media = {}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if (
                    not info.is_dir()
                    and Path(info.filename).suffix.lower() in STILL_EXTENSIONS
                ):
                    media.setdefault(info.filename, []).append(str(path))
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    seen_urls = set()
    invalid = 0
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(SCHEMA)
        for path in paths:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if not info.filename.endswith(SIDECAR_SUFFIX):
                        continue
                    member = info.filename.removesuffix(SIDECAR_SUFFIX)
                    ext = Path(member).suffix.lower()
                    location = _album_and_relative_name(member)
                    if ext not in STILL_EXTENSIONS or location is None:
                        continue
                    try:
                        if info.file_size > 1_000_000:
                            raise ValueError("Sidecar too large")
                        raw = json.loads(archive.read(info))
                        if (
                            not isinstance(raw, dict)
                            or not isinstance(raw.get("url"), str)
                            or not 0 < len(raw["url"]) <= 4096
                        ):
                            raise ValueError("Invalid identity")
                    except (ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError):
                        invalid += 1
                        continue
                    url = raw["url"]
                    album = location[0]
                    title = (
                        raw.get("title")
                        if isinstance(raw.get("title"), str)
                        else Path(member).name
                    )
                    timestamp = raw.get("photoTakenTime")
                    timestamp = (
                        timestamp.get("timestamp")
                        if isinstance(timestamp, dict)
                        else None
                    )
                    try:
                        taken = (
                            None
                            if timestamp is None or isinstance(timestamp, bool)
                            else int(timestamp)
                        )
                    except (TypeError, ValueError, OverflowError):
                        taken = None
                    iso = _iso_time(taken)
                    if iso is None:
                        taken = None
                    geo = raw.get("geoData")
                    geo = geo if isinstance(geo, dict) else {}
                    people = raw.get("people")
                    people = people if isinstance(people, list) else []
                    people = [
                        x["name"]
                        for x in people
                        if isinstance(x, dict)
                        and isinstance(x.get("name"), str)
                        and x["name"]
                    ]
                    now = datetime.now(timezone.utc).isoformat()
                    source = next(iter(media.get(member, [])), None)
                    conn.execute(
                        """INSERT INTO photos (url,album,title,taken_ts,taken_iso,latitude,longitude,people_json,raw_metadata_json,source_zip,media_member,media_ext,status,updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                     title=excluded.title,taken_ts=COALESCE(excluded.taken_ts,photos.taken_ts),taken_iso=COALESCE(excluded.taken_iso,photos.taken_iso),
                     latitude=COALESCE(excluded.latitude,photos.latitude),longitude=COALESCE(excluded.longitude,photos.longitude),
                     people_json=excluded.people_json,raw_metadata_json=excluded.raw_metadata_json,updated_at=excluded.updated_at""",
                        (
                            url,
                            album,
                            title,
                            taken,
                            iso,
                            _coordinate(geo.get("latitude"), 90),
                            _coordinate(geo.get("longitude"), 180),
                            json.dumps(people, ensure_ascii=False),
                            json.dumps(raw, ensure_ascii=False),
                            source,
                            member if source else None,
                            ext,
                            "pending" if source else "missing_media",
                            now,
                        ),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO photo_albums VALUES (?,?)", (url, album)
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO photo_sources VALUES (?,?,?)",
                        (url, member, source or ""),
                    )
                    seen_urls.add(url)
        # Media-only later parts can recover sidecars seen in an earlier invocation.
        for member, sources in media.items():
            for source in sources:
                conn.execute(
                    "INSERT OR IGNORE INTO photo_sources SELECT url,media_member,? FROM photo_sources WHERE media_member=?",
                    (source, member),
                )
        members_cache = {}

        def usable(z, m):
            if not z or not m:
                return False
            if z not in members_cache:
                try:
                    with zipfile.ZipFile(z) as archive:
                        members_cache[z] = set(archive.namelist())
                except (OSError, zipfile.BadZipFile):
                    members_cache[z] = set()
            return m in members_cache[z]

        for (url,) in conn.execute("SELECT url FROM photos").fetchall():
            current = conn.execute(
                "SELECT source_zip,media_member,status FROM photos WHERE url=?", (url,)
            ).fetchone()
            sources = conn.execute(
                "SELECT source_zip,media_member FROM photo_sources WHERE url=? AND source_zip IS NOT NULL AND source_zip!='' ORDER BY media_member,source_zip",
                (url,),
            ).fetchall()
            chosen = next(
                ((z, m) for z, m in ([current[:2]] + sources) if usable(z, m)), None
            )
            if chosen:
                conn.execute(
                    "UPDATE photos SET source_zip=?,media_member=?,status=CASE WHEN status IN ('missing_media','preview_error') THEN 'pending' ELSE status END WHERE url=?",
                    (*chosen, url),
                )
        conn.commit()
        photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        missing = conn.execute(
            "SELECT COUNT(*) FROM photos WHERE source_zip IS NULL OR media_member IS NULL"
        ).fetchone()[0]
    return {
        "photos": photos,
        "missing_media": missing,
        "seen_urls": len(seen_urls),
        "invalid_sidecars": invalid,
    }


def export_jsonl(db_path: Path | str, output_path: Path | str) -> int:
    """Atomically export a PRIVATE local catalog; never replace its DB or sources."""
    db = Path(db_path).resolve(strict=True)
    output = Path(output_path).resolve()
    count = 0
    temporary = None

    def same(a, b):
        return a == b or (a.exists() and b.exists() and a.samefile(b))

    with closing(sqlite3.connect(db)) as conn:
        protected = [
            db,
            Path(str(db) + "-wal"),
            Path(str(db) + "-shm"),
            Path(str(db) + "-journal"),
        ]
        protected.extend(
            Path(r[0]).resolve()
            for r in conn.execute(
                "SELECT source_zip FROM photo_sources WHERE source_zip IS NOT NULL AND source_zip!='' UNION SELECT source_zip FROM photos WHERE source_zip IS NOT NULL"
            )
        )
        if any(same(output, p) for p in protected):
            raise ValueError(
                "Export must not replace the catalog or an original archive."
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        conn.row_factory = sqlite3.Row
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output.parent,
                prefix=".catalog-",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                for row in conn.execute("SELECT * FROM photos ORDER BY url"):
                    item = {
                        "url": row["url"],
                        "album": row["album"],
                        "albums": [
                            r[0]
                            for r in conn.execute(
                                "SELECT album FROM photo_albums WHERE url=? ORDER BY album",
                                (row["url"],),
                            )
                        ],
                        "title": row["title"],
                        "taken_at": row["taken_iso"],
                        "latitude": row["latitude"],
                        "longitude": row["longitude"],
                        "people": json.loads(row["people_json"]),
                        "media_type": row["media_ext"],
                        "status": row["status"],
                        "caption": json.loads(row["caption_json"])
                        if row["caption_json"]
                        else None,
                        "raw_metadata": json.loads(row["raw_metadata_json"]),
                    }
                    handle.write(
                        json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    count += 1
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    return count


def prepare_batch(
    db_path: Path | str,
    work_dir: Path | str,
    *,
    limit: int = 12,
    preview_max: int = 1024,
    urls: Iterable[str] | None = None,
    metadata_fields: Iterable[str] = (),
) -> dict:
    """Write EXIF-normalized JPEG previews for the next pending photos."""
    if not 1 <= limit <= 500 or not 32 <= preview_max <= 4096:
        raise ValueError("Use limit 1-500 and preview size 32-4096")
    fields = set(metadata_fields)
    if fields - {"album", "title", "taken_at", "latitude", "longitude", "people"}:
        raise ValueError("Unknown metadata field")
    work = Path(work_dir)
    batch_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    batch_dir = (work / batch_id).resolve()
    batch_dir.mkdir(parents=True, exist_ok=False)

    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        selected_urls = list(urls) if urls is not None else None
        if selected_urls:
            placeholders = ",".join("?" for _ in selected_urls)
            rows = conn.execute(
                f"""
                SELECT url, album, title, taken_iso, latitude, longitude, people_json,
                       source_zip, media_member
                FROM photos
                WHERE status IN ('pending', 'retry', 'preview_error')
                  AND source_zip IS NOT NULL
                  AND media_member IS NOT NULL
                  AND url IN ({placeholders})
                ORDER BY COALESCE(taken_ts, 0), url
                LIMIT ?
                """,
                (*selected_urls, limit),
            ).fetchall()
        elif selected_urls == []:
            rows = []
        else:
            rows = conn.execute(
                """
                SELECT url, album, title, taken_iso, latitude, longitude, people_json,
                       source_zip, media_member
                FROM photos
                WHERE status IN ('pending', 'retry', 'preview_error')
                  AND source_zip IS NOT NULL
                  AND media_member IS NOT NULL
                ORDER BY COALESCE(taken_ts, 0), url
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    open_archives: dict[str, zipfile.ZipFile] = {}
    items = []
    errors = []
    try:
        for index, row in enumerate(rows):
            try:
                with closing(sqlite3.connect(db_path)) as conn:
                    alternatives = conn.execute(
                        "SELECT source_zip,media_member FROM photo_sources WHERE url=? AND source_zip!='' ORDER BY source_zip,media_member",
                        (row["url"],),
                    ).fetchall()
                candidates = list(
                    dict.fromkeys(
                        [(row["source_zip"], row["media_member"]), *alternatives]
                    )
                )
                failure = None
                for archive_path, member in candidates:
                    try:
                        if archive_path not in open_archives:
                            if len(open_archives) >= 32:
                                open_archives.pop(next(iter(open_archives))).close()
                            open_archives[archive_path] = zipfile.ZipFile(archive_path)
                        archive = open_archives[archive_path]
                        if archive.getinfo(member).file_size > 128_000_000:
                            raise ValueError("Image file exceeds 128 MB")
                        raw_bytes = archive.read(member)
                        from .book import normalized_image

                        source_image = normalized_image(raw_bytes)
                        with closing(sqlite3.connect(db_path)) as conn:
                            conn.execute(
                                "UPDATE photos SET source_zip=?,media_member=?,status=CASE WHEN status='preview_error' THEN 'pending' ELSE status END WHERE url=?",
                                (archive_path, member, row["url"]),
                            )
                            conn.commit()
                        break
                    except Exception as error:
                        failure = error
                else:
                    raise failure or ValueError("No usable original media source")
                with source_image as source:
                    image = ImageOps.exif_transpose(source)
                    image.load()
                    image.thumbnail(
                        (preview_max, preview_max), Image.Resampling.LANCZOS
                    )
                    if "transparency" in image.info:
                        image = image.convert("RGBA")
                    if image.mode not in ("RGB", "L"):
                        background = Image.new("RGB", image.size, "white")
                        if "A" in image.getbands():
                            background.paste(image, mask=image.getchannel("A"))
                        else:
                            background.paste(image.convert("RGB"))
                        image = background
                    elif image.mode == "L":
                        image = image.convert("RGB")
                    digest = hashlib.sha256(row["url"].encode()).hexdigest()[:12]
                    preview = batch_dir / f"{index:03d}-{digest}.jpg"
                    image.save(preview, format="JPEG", quality=88, optimize=True)
                context = {
                    "album": row["album"],
                    "title": row["title"],
                    "taken_at": row["taken_iso"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "people": json.loads(row["people_json"]),
                }
                items.append(
                    {
                        "url": row["url"],
                        "preview": str(preview),
                        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                        **{k: context[k] for k in fields},
                    }
                )
            except Exception as error:
                marker = f"preview {type(error).__name__}: {str(error)[:250]}"
                errors.append(
                    {"url": row["url"], "title": row["title"], "error": marker}
                )
                with closing(sqlite3.connect(db_path)) as conn:
                    conn.execute(
                        "UPDATE photos SET status='preview_error',last_error=?,updated_at=? WHERE url=?",
                        (marker, datetime.now(timezone.utc).isoformat(), row["url"]),
                    )
                    conn.commit()
    finally:
        for archive in open_archives.values():
            archive.close()

    manifest = {"batch_id": batch_id, "items": items, "errors": errors}
    (batch_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def validate_caption_result(
    payload: dict, expected_urls: Iterable[str]
) -> dict[str, dict]:
    """Validate a structured response and return captions keyed by photo URL."""
    schema = json.loads(
        Path(__file__).with_name("caption-schema.json").read_text(encoding="utf-8")
    )
    error = next(Draft202012Validator(schema).iter_errors(payload), None)
    if error:
        raise ValueError("Invalid caption field: " + error.json_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("captions"), list):
        raise ValueError("result must contain a captions list")
    expected = set(expected_urls)
    validated: dict[str, dict] = {}
    for position, caption in enumerate(payload["captions"]):
        if not isinstance(caption, dict):
            raise ValueError(f"caption {position} is not an object")
        missing = REQUIRED_CAPTION_FIELDS - set(caption)
        if missing:
            raise ValueError(f"caption {position} missing fields: {sorted(missing)}")
        url = caption["url"]
        if url not in expected:
            raise ValueError(f"caption {position} has unexpected URL: {url}")
        if url in validated:
            raise ValueError(f"duplicate caption URL: {url}")
        if not isinstance(caption["caption"], str) or not caption["caption"].strip():
            raise ValueError(f"caption {position} has an empty caption")
        for field in ("keywords", "unnamed_people", "pets", "visible_text"):
            if not isinstance(caption[field], list):
                raise ValueError(f"caption {position} field {field} must be a list")
        if not isinstance(caption["quality"], dict) or not isinstance(
            caption["composition"], dict
        ):
            raise ValueError(
                f"caption {position} quality and composition must be objects"
            )
        validated[url] = caption
    return validated


def ingest_caption_result(
    db_path: Path | str,
    payload: dict,
    expected_urls: Iterable[str],
    *,
    expected_snapshot: dict | None = None,
) -> dict[str, int]:
    """Persist all valid returned captions and leave missing results retryable."""
    expected = list(expected_urls)
    validated = validate_caption_result(payload, expected)
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if len(set(expected)) != len(expected):
            raise ValueError("Duplicate expected photo identity")
        for url in expected:
            current = conn.execute(
                "SELECT status FROM photos WHERE url=?", (url,)
            ).fetchone()
            if current is None:
                raise ValueError("Expected photo is absent from this catalog")
            if current[0] == "submitted":
                raise ValueError(
                    "Photo is reserved by a submitted batch; collect or reconcile it first"
                )
            if expected_snapshot is not None:
                snapshot = conn.execute(
                    "SELECT status,caption_json,attempts,updated_at FROM photos WHERE url=?",
                    (url,),
                ).fetchone()
                if snapshot != expected_snapshot.get(url):
                    raise ValueError(
                        "Catalog changed while Codex was working. Its result is preserved; review the conflict before importing."
                    )
        for url in expected:
            caption = validated.get(url)
            if caption is not None:
                conn.execute(
                    """
                    UPDATE photos
                    SET status='captioned', caption_json=?, attempts=attempts+1,
                        last_error=NULL, updated_at=?
                    WHERE url=?
                    """,
                    (
                        json.dumps(caption, ensure_ascii=False, separators=(",", ":")),
                        now,
                        url,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE photos
                    SET status='retry', attempts=attempts+1,
                        last_error='caption missing from batch response', updated_at=?
                    WHERE url=? AND status != 'captioned'
                    """,
                    (now, url),
                )
        conn.commit()
    return {"captioned": len(validated), "retry": len(expected) - len(validated)}


def build_caption_prompt(manifest: dict) -> str:
    context_lines = []
    for number, item in enumerate(manifest["items"], 1):
        context_lines.append(
            f"Image {number}: url={item['url']}; date={item.get('taken_at') or 'unknown'}; "
            f"album={item.get('album') or 'unknown'}; people tags="
            f"{json.dumps(item.get('people') or [], ensure_ascii=False)}; "
            f"GPS=({item.get('latitude')},{item.get('longitude')})"
        )
    context = "\n".join(context_lines)
    return f"""Caption each attached image in attachment order. Do not use tools.

Return one structured record per image. Copy each URL exactly. Any supplied metadata is context, not evidence locating or identifying faces. Do not infer which tagged person is shown or invent identities or relationships. Describe visible clothing and position instead. Describe unnamed people by approximate age and presentation. Write a rich, concrete paragraph like a family member would write on the back of a print: actions, expressions, mood, setting, clothing, objects, and what makes the moment distinctive. Avoid generic filler.

Also provide useful search keywords, visible text, pets, technical and print quality, hero potential, square-crop/layout notes, and whether the image is a screenshot, document, meme, or scan. For eyes_open and faces_uncut use yes, no, mixed, not_visible, or not_applicable. For sharpness and exposure use good, fair, or poor. For square_crop use good, possible, or poor.

{context}
"""


def build_codex_command(
    manifest: dict,
    schema_path: Path | str,
    output_path: Path | str,
    *,
    prompt: str | None = None,
    model: str,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--output-schema",
        str(Path(schema_path).resolve()),
        "--output-last-message",
        str(Path(output_path).resolve()),
    ]
    for item in manifest["items"]:
        command.extend(["--image", item["preview"]])
    command.extend(
        ["--", prompt if prompt is not None else build_caption_prompt(manifest)]
    )
    return command


def run_caption_batch(
    db_path: Path | str,
    manifest_path: Path | str,
    schema_path: Path | str,
    *,
    model: str,
    allow_upload: bool = False,
    timeout: int = 600,
) -> dict[str, int]:
    if allow_upload is not True:
        raise ValueError("Explicit permission is required to send images to Codex")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Choose an explicit Codex model")
    if type(timeout) is not int or not 10 <= timeout <= 3600:
        raise ValueError("Use a timeout from 10 to 3600 seconds")
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    snapshots = {}
    with closing(
        sqlite3.connect(
            Path(db_path).resolve(strict=True).as_uri() + "?mode=ro", uri=True
        )
    ) as conn:
        for item in manifest["items"]:
            snapshot = conn.execute(
                "SELECT status,caption_json,attempts,updated_at FROM photos WHERE url=?",
                (item["url"],),
            ).fetchone()
            if snapshot is None or snapshot[0] == "submitted":
                raise ValueError(
                    "A caption photo is absent or reserved by an API job; reconcile it before using Codex"
                )
            snapshots[item["url"]] = snapshot
    attempt = manifest_file.parent / ("codex-" + uuid.uuid4().hex)
    attempt.mkdir()
    # The model needs a stable join key, not a private source URL or filename.
    from .api_batch import custom_id_for_url

    identities = {
        custom_id_for_url(item["url"]): item["url"] for item in manifest["items"]
    }
    if not identities or len(identities) != len(manifest["items"]):
        raise ValueError("Missing or duplicate caption identities")
    safe_items = []
    for item in manifest["items"]:
        ident = custom_id_for_url(item["url"])
        preview = attempt / (ident + ".jpg")
        from .book import normalized_image

        with normalized_image(Path(item["preview"]).read_bytes()) as image:
            image.thumbnail((1024, 1024))
            image.save(preview, "JPEG", quality=90)
        safe_items.append({**item, "url": ident, "preview": str(preview.resolve())})
    result_path = attempt / "result.json"
    command = build_codex_command(
        {"items": safe_items}, schema_path, result_path, model=model
    )
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=attempt,
        )
    except subprocess.TimeoutExpired as error:
        for name, value in [("stdout", error.stdout), ("stderr", error.stderr)]:
            (attempt / ("codex." + name + ".log")).write_text(
                value.decode("utf-8", errors="replace")
                if isinstance(value, bytes)
                else value or "",
                encoding="utf-8",
            )
        raise RuntimeError(
            f"Codex timed out. The service may have processed these images. Inspect {attempt} before choosing another attempt; no captions were imported."
        ) from None
    (attempt / "codex.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (attempt / "codex.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Codex exited {completed.returncode}; see {attempt}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    validate_caption_result(payload, list(identities))
    for caption in payload["captions"]:
        caption["url"] = identities[caption["url"]]
    return ingest_caption_result(
        db_path,
        payload,
        [item["url"] for item in manifest["items"]],
        expected_snapshot=snapshots,
    )

"""Validated, portable book specifications and immutable normalized images."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def number(value, low, high, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{label} must be between {low} and {high}")
    return value


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(
            "Use a short stable ID with letters, digits, underscores or hyphens"
        )
    return value


def text(value, maximum=6000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("Text is missing or too long")
    return value


def confined(root, relative, *, exists=True):
    root = Path(root).resolve()
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("Use a file inside the book folder")
    result = (root / p).resolve(strict=exists)
    if not result.is_relative_to(root):
        raise ValueError("File leaves the book folder")
    return result


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".workshop-",
            delete=False,
        ) as f:
            temporary = Path(f.name)
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def normalized_image(raw: bytes):
    if len(raw) > 128_000_000:
        raise ValueError("Image exceeds 128 MB")
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass
    try:
        source = Image.open(io.BytesIO(raw))
    except Image.DecompressionBombError as e:
        raise ValueError("Image exceeds decoder safety limit") from e
    except UnidentifiedImageError:
        try:
            import rawpy

            with rawpy.imread(io.BytesIO(raw)) as decoded:
                source = Image.fromarray(
                    decoded.postprocess(use_camera_wb=True, half_size=False)
                )
        except ImportError as e:
            raise ValueError(
                "This format needs the optional decoders installation"
            ) from e
        except Exception as e:
            raise ValueError("Optional decoder could not read this image") from e
    with source:
        if source.width * source.height > 64_000_000:
            raise ValueError("Image exceeds 64 megapixels")
        image = ImageOps.exif_transpose(source)
        image.load()
        if "transparency" in image.info:
            image = image.convert("RGBA")
        if "A" in image.getbands():
            out = Image.new("RGB", image.size, "white")
            out.paste(image.convert("RGB"), mask=image.getchannel("A"))
        else:
            out = image.convert("RGB")
        out.info.clear()
        return out


def normalize_asset(source, output_dir, asset_id):
    """Preserve source bytes; write a metadata-free full-resolution derivative."""
    identifier(asset_id)
    raw = Path(source).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    image = normalized_image(raw)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = sha + "-normalized-v1.jpg"
    destination = output_dir / name
    encoded = io.BytesIO()
    image.save(encoded, "JPEG", quality=97, subsampling=0)
    expected = encoded.getvalue()
    if destination.exists():
        if destination.read_bytes() != expected:
            raise ValueError(
                "A normalized derivative or decoder recipe changed; preserve it and use a new output folder"
            )
    else:
        with destination.open("xb") as f:
            f.write(expected)
            f.flush()
            os.fsync(f.fileno())
    return {
        "id": asset_id,
        "path": name,
        "width": image.width,
        "height": image.height,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "source_sha256": sha,
    }


def validate_book(value, root):
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("Unsupported book format")
    book = json.loads(json.dumps(value))
    identifier(book.get("bookId"))
    text(book.get("title"), 300)
    physical = book.get("physical", {})
    for key, default, lo, hi in [
        ("width", 8, 3, 16),
        ("height", 8, 3, 16),
        ("bleed", 0, 0, 1),
        ("safe", 0.25, 0, 2),
        ("dpi", 180, 72, 600),
    ]:
        physical[key] = number(physical.get(key, default), lo, hi, key)
    if physical["safe"] * 2 >= min(physical["width"], physical["height"]):
        raise ValueError("Safe area is larger than the page")
    book["physical"] = physical
    assets = book.get("assets")
    if not isinstance(assets, list) or not 0 <= len(assets) <= 10000:
        raise ValueError("Provide at most 10000 assets")
    ids = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Assets must be objects")
        ident = identifier(asset.get("id"))
        if ident in ids:
            raise ValueError("Duplicate asset ID")
        ids.add(ident)
        file = confined(root, asset["path"])
        if not file.is_file():
            raise ValueError("Asset must be a file")
        if file.stat().st_size > 128_000_000:
            raise ValueError("Image exceeds 128 MB")
        with Image.open(file) as image:
            if image.getexif().get(274, 1) != 1:
                raise ValueError(
                    "Normalize images before layout to bake in EXIF orientation"
                )
            width, height = image.size
        number(width * height, 1, 64_000_000, "image pixels")
        asset["width"] = width
        asset["height"] = height
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if asset.get("sha256") and asset["sha256"] != actual:
            raise ValueError("Asset bytes changed; update its manifest deliberately")
        asset["sha256"] = actual
    pages = book.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 256:
        raise ValueError("Provide 1-256 pages")
    covers = book.get("covers", [])
    if not isinstance(covers, list) or len(covers) > 32:
        raise ValueError("Use at most 32 cover variants")
    page_ids = set()
    for page in pages + covers:
        if not isinstance(page, dict):
            raise ValueError("Pages must be objects")
        ident = identifier(page.get("id"))
        if ident in page_ids:
            raise ValueError("Duplicate page or cover ID")
        page_ids.add(ident)
        text(page.get("subject", ""), 200)
        text(page.get("caption", ""))
        if page.get("kind", "mosaic") not in {"mosaic", "rows", "cover", "text"}:
            raise ValueError("Unknown layout kind")
        placements = page.get("images", [])
        if not isinstance(placements, list) or len(placements) > 24:
            raise ValueError(
                "Use at most 24 photos per page; split larger groups into pages"
            )
        if page.get("kind") == "text" and placements:
            raise ValueError(
                "Text pages cannot contain selected images; use a photo layout"
            )
        page["images"] = placements
        placement_ids = set()
        for entry in placements:
            if not isinstance(entry, dict) or set(entry) - {
                "id",
                "asset",
                "focus",
                "placeholder",
            }:
                raise ValueError("Unknown image placement field")
            name = identifier(entry.get("id"))
            if name in placement_ids:
                raise ValueError("Duplicate placement")
            if ("asset" in entry) == ("placeholder" in entry):
                raise ValueError("A placement needs one asset or one placeholder")
            if "asset" in entry and entry["asset"] not in ids:
                raise ValueError("Missing asset")
            if "placeholder" in entry:
                slot = entry["placeholder"]
                if not isinstance(slot, dict) or set(slot) != {
                    "width",
                    "height",
                    "label",
                }:
                    raise ValueError("A placeholder needs dimensions and a label")
                number(slot["width"], 1, 100000, "placeholder width")
                number(slot["height"], 1, 100000, "placeholder height")
                text(slot["label"], 300)
            placement_ids.add(name)
            focus = entry.setdefault("focus", [0.5, 0.5])
            if not isinstance(focus, list) or len(focus) != 2:
                raise ValueError("Focus needs two fractions")
            for v in focus:
                number(v, 0, 1, "focus")
        if page.get("kind") == "cover" and len(placements) != 1:
            raise ValueError("A cover uses one complete image")
        if page.get("kind", "mosaic") != "text" and not placements:
            raise ValueError("This page has no images")
        options = page.get("options", [])
        if not isinstance(options, list) or len(options) > 12:
            raise ValueError("Use at most 12 caption options")
        page["options"] = options
        option_ids = set()
        for option in options:
            if not isinstance(option, dict):
                raise ValueError("Caption options must be objects")
            oid = identifier(option.get("id"))
            if oid in option_ids:
                raise ValueError("Duplicate caption option ID")
            option_ids.add(oid)
            text(option.get("text"))
        for key, default, lo, hi in [
            ("cropLimit", 0.18, 0, 0.95),
            ("gutter", 0.08, 0, 0.5),
            ("fontSize", 22, 8, 100),
            ("titleSize", 42, 10, 200),
        ]:
            page[key] = number(page.get(key, default), lo, hi, key)
        if page.get("band", "bottom") not in {"top", "bottom", "overlay"}:
            raise ValueError("Unknown caption band")
        if page.get("pattern", "plain") not in {"plain", "dots"}:
            raise ValueError("Unknown background pattern")
        if page.get("scrim", "bottom") not in {"top", "bottom", "none"}:
            raise ValueError("Unknown cover scrim")
        width = (physical["width"] + physical["bleed"] * 2) * 72
        height = (physical["height"] + physical["bleed"] * 2) * 72

        def rectangle(rect):
            if not isinstance(rect, list) or len(rect) != 4:
                raise ValueError(
                    "Text rectangles need x, y, width and height in points"
                )
            x, y, w, h = rect
            number(x, 0, width, "text x")
            number(y, 0, height, "text y")
            number(w, 1, width, "text width")
            number(h, 1, height, "text height")
            if x + w > width or y + h > height:
                raise ValueError("Text rectangle leaves the page")

        if "textRect" in page:
            rectangle(page["textRect"])
        blocks = page.setdefault("textBlocks", [])
        if not isinstance(blocks, list) or len(blocks) > 12:
            raise ValueError("Use at most 12 additional text panels")
        block_ids = set()
        for block in blocks:
            if not isinstance(block, dict) or set(block) - {
                "id",
                "text",
                "rect",
                "size",
                "font",
                "align",
                "ink",
                "panel",
            }:
                raise ValueError("Unknown text panel field")
            bid = identifier(block.get("id"))
            if bid in block_ids:
                raise ValueError("Duplicate text panel ID")
            block_ids.add(bid)
            text(block.get("text"))
            rectangle(block.get("rect"))
            block["size"] = number(
                block.get("size", 22), 8, 100, "text panel font size"
            )
            for key, default, allowed in [
                ("font", "Fredoka", {"Fredoka", "Baloo2"}),
                ("align", "center", {"center", "left"}),
                ("ink", "dark", {"dark", "white"}),
                ("panel", "white", {"white", "none"}),
            ]:
                block[key] = block.get(key, default)
                if block[key] not in allowed:
                    raise ValueError("Unknown text panel style")
        exclusions = page.setdefault("exclusions", [])
        if not isinstance(exclusions, list) or len(exclusions) > 1000:
            raise ValueError("Invalid excluded-photo decisions")
        for exclusion in exclusions:
            if (
                not isinstance(exclusion, dict)
                or set(exclusion) != {"asset", "reason"}
                or exclusion["asset"] not in ids
            ):
                raise ValueError("An exclusion needs a known asset and reason")
            text(exclusion["reason"], 1000)

    return book


def load_book(path):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 8_000_000:
        raise ValueError("Book specification is too large")
    return validate_book(json.loads(path.read_text(encoding="utf-8")), path.parent)


def options_hash(book):
    return digest(
        {
            "bookId": book["bookId"],
            "pages": [
                {
                    "id": p["id"],
                    "options": p.get("options", []),
                    "caption": p.get("caption", ""),
                }
                for p in book["pages"]
            ],
        }
    )


def apply_choices(book, state):
    if state["bookId"] != book["bookId"] or state["optionsHash"] != options_hash(book):
        raise ValueError("Caption choices belong to another book or option version")
    result = json.loads(json.dumps(book))
    for page in result["pages"]:
        choice = state.get("choices", {}).get(page["id"], {}).get("optionId")
        if choice is not None:
            option = next(
                (o for o in page.get("options", []) if o["id"] == choice), None
            )
            if option is None:
                raise ValueError("Saved option no longer exists")
            page["caption"] = option["text"]
    return result

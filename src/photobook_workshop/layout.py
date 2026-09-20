"""Two preserved layouts: recursive slicing mosaics and authored justified rows.

Geometry uses points and a top-left origin. No I/O or source photos are needed.
"""

from __future__ import annotations
import itertools
import math
from functools import lru_cache
from .book import number


def photo_trees(photos):
    """Exact original slicing search, bounded to six photos before enumeration."""
    if not 1 <= len(photos) <= 6:
        raise ValueError("Exact mosaic search supports 1-6 images")
    for p in photos:
        number(p["width"], 1, 100000, "width")
        number(p["height"], 1, 100000, "height")

    @lru_cache(None)
    def choices(ids):
        if len(ids) == 1:
            p = photos[ids[0]]
            return [(p["width"] / p["height"], ids[0])]
        found = []
        for count in range(1, len(ids)):
            for rest in itertools.combinations(ids[1:], count - 1):
                left = (ids[0],) + rest
                right = tuple(i for i in ids if i not in left)
                for ar1, a in choices(left):
                    for ar2, b in choices(right):
                        found.extend(
                            [
                                (ar1 + ar2, ("h", ar1, ar2, a, b)),
                                (1 / (1 / ar1 + 1 / ar2), ("v", ar1, ar2, a, b)),
                            ]
                        )
        return found

    return choices(tuple(range(len(photos))))


def bounded_trees(photos, beam=24):
    """Deterministic bounded alternatives for larger pages; every leaf retained."""
    if not 1 <= len(photos) <= 24:
        raise ValueError("Split pages with more than 24 images")

    @lru_cache(None)
    def choices(ids):
        if len(ids) == 1:
            return [(photos[ids[0]]["width"] / photos[ids[0]]["height"], ids[0])]
        candidates = []
        for cut in sorted(
            {max(1, len(ids) // 3), len(ids) // 2, min(len(ids) - 1, 2 * len(ids) // 3)}
        ):
            for ar1, a in choices(ids[:cut]):
                for ar2, b in choices(ids[cut:]):
                    candidates.extend(
                        [
                            (ar1 + ar2, ("h", ar1, ar2, a, b)),
                            (1 / (1 / ar1 + 1 / ar2), ("v", ar1, ar2, a, b)),
                        ]
                    )
        candidates.sort(key=lambda v: v[0])
        # Retain a spread of shapes; never allocate the unbounded subset search.
        if len(candidates) > beam:
            return [
                candidates[round(i * (len(candidates) - 1) / (beam - 1))]
                for i in range(beam)
            ]
        return candidates

    return choices(tuple(range(len(photos))))


def boxes(tree, x, y, w, h, gutter):
    if w <= 0 or h <= 0:
        raise ValueError("Gutters leave no room for an image")
    if isinstance(tree, int):
        return [(tree, x, y, w, h)]
    axis, ar1, ar2, a, b = tree
    if axis == "h":
        w1 = (w - gutter) * ar1 / (ar1 + ar2)
        return boxes(a, x, y, w1, h, gutter) + boxes(
            b, x + w1 + gutter, y, w - gutter - w1, h, gutter
        )
    h1 = (h - gutter) * (1 / ar1) / (1 / ar1 + 1 / ar2)
    return boxes(a, x, y, w, h1, gutter) + boxes(
        b, x, y + h1 + gutter, w, h - gutter - h1, gutter
    )


def crop_transform(source, rect, focus=(0.5, 0.5)):
    x, y, w, h = rect
    sw, sh = source["width"], source["height"]
    for value in [w, h, sw, sh]:
        number(value, 0.000001, 1e9, "image dimension")
    for v in focus:
        number(v, 0, 1, "focus")
    scale = max(w / sw, h / sh)
    dw, dh = sw * scale, sh * scale
    return {
        "draw": [x - (dw - w) * focus[0], y - (dh - h) * focus[1], dw, dh],
        "crop_fraction": 1 - min(w / dw, h / dh),
        "source_ppi": 72 / scale,
    }


def mosaic(photos, region, gutter=6, crop_limit=0.18, height_range=None):
    x, y, w, h = region
    best = None
    minimum, maximum = height_range or (h, h)
    trees = photo_trees(photos) if len(photos) <= 6 else bounded_trees(photos)
    for aspect, tree in trees:
        height = max(minimum, min(maximum, w / aspect))
        try:
            rects = boxes(tree, x, y, w, height, gutter)
        except ValueError:
            continue
        crops = []
        small = 0
        fractions = []
        for i, bx, by, bw, bh in rects:
            crops.append(crop_transform(photos[i], [bx, by, bw, bh])["crop_fraction"])
            small += max(0, min(w, height) * 0.15 - min(bw, bh)) ** 2 / 100
            fractions.append(bw * bh / (w * height))
        crop_max = max(crops)
        if crop_max > crop_limit + 1e-9:
            continue
        imbalance = sum((v - 1 / len(photos)) ** 2 for v in fractions)
        score = (
            crop_max * 70
            + sum(crops) * 8
            + small
            + imbalance * 18
            + (maximum - height) / 150
        )
        if best is None or score < best[0]:
            best = (score, height, rects, crop_max)
    if best is None:
        raise ValueError(
            "No mosaic meets the crop limit. Change the grouping, photo region or crop limit deliberately; no photo was dropped."
        )
    return best


def justified_rows(photos, rows, canvas_width, canvas_height, border=0, initial_y=0):
    """Keep authored row heights; cumulative edges close without rounding seams."""
    lookup = {p["id"]: p for p in photos}
    used = set()
    rects = []
    y = number(initial_y, 0, canvas_height, "grid start")
    row_bounds = []
    if not isinstance(rows, list) or not rows:
        raise ValueError("Provide complete authored rows")
    for row in rows:
        height = number(row.get("height"), 1, canvas_height, "row height")
        top = number(row.get("y", y), 0, canvas_height, "row top")
        ids = row.get("images", [])
        if (
            not ids
            or len(set(ids)) != len(ids)
            or any(i not in lookup or i in used for i in ids)
        ):
            raise ValueError("Missing, repeated or unknown row placement")
        if top + height > canvas_height + 1e-6 or any(
            top < end - 1e-6 and top + height > start + 1e-6
            for start, end in row_bounds
        ):
            raise ValueError("Rows overlap or leave the page")
        aspects = [lookup[i]["width"] / lookup[i]["height"] for i in ids]
        total = sum(aspects)
        cumulative = 0
        left = 0
        for ident, aspect in zip(ids, aspects):
            cumulative += aspect
            right = (
                canvas_width
                if ident == ids[-1]
                else round(canvas_width * cumulative / total)
            )
            width = right - left
            if min(width, height) <= 2 * border:
                raise ValueError("Cell borders leave no image interior")
            rects.append(
                (
                    ident,
                    left + border,
                    top + border,
                    width - 2 * border,
                    height - 2 * border,
                )
            )
            left = right
        row_bounds.append((top, top + height))
        y = top + height
        used.update(ids)
    if used != set(lookup):
        raise ValueError("Every selected placement must appear in the rows")
    return rects


def plan_page(page, assets, physical):
    width = (physical["width"] + physical["bleed"] * 2) * 72
    height = (physical["height"] + physical["bleed"] * 2) * 72
    margin = (physical["safe"] + physical["bleed"]) * 72
    gutter = page["gutter"] * 72
    images = [
        {**(assets[p["asset"]] if "asset" in p else p["placeholder"]), **p}
        for p in page.get("images", [])
    ]
    kind = page.get("kind", "mosaic")
    band = page.get("band", "bottom")
    band_height = number(
        page.get("bandHeight", height * 0.23), 0, height * 0.8, "caption band height"
    )
    placements = []
    regions = []
    if kind == "cover":
        rects = [(0, 0, 0, width, height)]
    elif kind == "text":
        rects = []
    elif kind == "rows":
        raw = justified_rows(
            images, page.get("rows"), width, height, gutter / 2, page.get("gridY", 0)
        )
        index = {p["id"]: i for i, p in enumerate(images)}
        rects = [(index[i], x, y, w, h) for i, x, y, w, h in raw]
    else:
        start = margin + (band_height if band == "top" else 0)
        available = height - 2 * margin - (band_height if band != "overlay" else 0)
        low = available * number(
            page.get("minPhotoHeight", 0.88), 0.25, 1, "minimum photo height fraction"
        )
        _, _, rects, _ = mosaic(
            images,
            (margin, start, width - 2 * margin, available),
            gutter,
            page["cropLimit"],
            (low, available),
        )
    for i, x, y, w, h in rects:
        p = images[i]
        transform = crop_transform(p, [x, y, w, h], p["focus"])
        if (
            "placeholder" not in p
            and transform["crop_fraction"] > page["cropLimit"] + 1e-9
        ):
            raise ValueError(
                "Page "
                + page["id"]
                + " exceeds its deliberate crop limit for "
                + p["id"]
            )
        placements.append(
            {
                "id": p["id"],
                "asset": p.get("asset"),
                "rect": [x, y, w, h],
                "focus": p["focus"],
                **transform,
                **(
                    {
                        "placeholder": p["placeholder"],
                        "source_ppi": None,
                        "crop_fraction": 0,
                    }
                    if "placeholder" in p
                    else {}
                ),
            }
        )
    if kind == "text":
        regions = [margin, margin, width - 2 * margin, height - 2 * margin]
    elif kind == "cover" or band == "overlay":
        regions = [
            margin,
            margin if kind == "cover" and page.get("scrim") == "top" else height * 0.68,
            width - 2 * margin,
            height * 0.25,
        ]
    else:
        regions = [
            margin,
            margin if band == "top" else height - margin - band_height,
            width - 2 * margin,
            band_height,
        ]
    if "textRect" in page:
        regions = page["textRect"]
    return {
        "pageId": page["id"],
        "width": width,
        "height": height,
        "placements": placements,
        "textRect": regions,
        "kind": kind,
        "textBlocks": page.get("textBlocks", []),
        "exclusions": page.get("exclusions", []),
    }

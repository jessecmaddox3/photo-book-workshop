"""An independently invented end-to-end book. No accounts or outside requests."""

from __future__ import annotations
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from .book import atomic_json, load_book, validate_book, digest
from .photo_catalog import scan_archives, ingest_caption_result
from .curation import (
    catalog_records,
    rank_candidates,
    contact_sheets,
    normalize_selection,
    resolve_photo,
)
from .proof import build_book

PACKAGE = Path(__file__).resolve().parent
SCENES = {
    "greenhouse": (
        "A greenhouse opens onto an imaginary flower garden.",
        ["greenhouse", "garden", "flowers"],
    ),
    "balloon": (
        "A striped balloon drifts above an invented green valley.",
        ["balloon", "sky", "valley"],
    ),
    "lighthouse": (
        "A lighthouse stands beside an imaginary blue sea.",
        ["lighthouse", "sea", "coast"],
    ),
    "bicycle": (
        "A bicycle waits by a sunlit wall with a basket of flowers.",
        ["bicycle", "flowers", "wall"],
    ),
    "fox": ("A fox watches an invented valley at sunset.", ["fox", "valley", "sunset"]),
    "pond": (
        "A quiet pond reflects a willow beside a little footbridge.",
        ["pond", "willow", "bridge"],
    ),
}


def create_demo(directory):
    """Create a fresh workspace through the same catalog/curation/build interfaces."""
    root = Path(directory).resolve()
    if root.exists():
        raise FileExistsError(
            "The example folder already exists. Use start to reopen it, or choose a new folder."
        )
    root.mkdir(parents=True)
    try:
        (root / "inputs").mkdir()
        (root / "book").mkdir()
        archives = [
            root / "inputs" / "invented-part-1.zip",
            root / "inputs" / "invented-part-2.zip",
        ]
        captions = []
        names = {}
        with (
            zipfile.ZipFile(archives[0], "w", zipfile.ZIP_DEFLATED) as first,
            zipfile.ZipFile(archives[1], "w", zipfile.ZIP_DEFLATED) as second,
        ):
            for index, (name, (caption, keywords)) in enumerate(SCENES.items()):
                url = "https://example.invalid/photo-book/" + name
                names[name] = hashlib.sha256(url.encode()).hexdigest()
                member = "Takeout/Google Photos/Invented Scenes/" + name + ".png"
                first.writestr(
                    member + ".supplemental-metadata.json",
                    json.dumps(
                        {
                            "url": url,
                            "title": name + ".png",
                            "photoTakenTime": {
                                "timestamp": str(1893456000 + index * 86400)
                            },
                            "people": [],
                        }
                    ),
                )
                (first if index % 2 == 0 else second).writestr(
                    member, (PACKAGE / "demo/assets" / (name + ".png")).read_bytes()
                )
                captions.append(
                    {
                        "url": url,
                        "caption": caption,
                        "keywords": keywords,
                        "unnamed_people": [],
                        "pets": [],
                        "quality": {
                            "sharpness": "good",
                            "exposure": "good",
                            "eyes_open": "not_applicable",
                            "faces_uncut": "not_applicable",
                        },
                        "print_recommendation": True,
                        "print_reason": "Clear invented illustration.",
                        "hero_candidate": name == "lighthouse",
                        "composition": {
                            "subject_position": "center",
                            "square_crop": "possible",
                            "headroom": "Check the subject before cropping.",
                        },
                        "visible_text": [],
                        "non_photo": True,
                        "non_photo_type": "illustration",
                    }
                )
        db = root / "catalog.sqlite3"
        scan = scan_archives(archives, db)
        atomic_json(root / "captions.json", {"captions": captions})
        ingest_caption_result(db, {"captions": captions}, [c["url"] for c in captions])
        query = {"includeNonPhotos": True, "limit": 6}
        candidates = rank_candidates(catalog_records(db), query)
        pool = {
            "schemaVersion": 1,
            "query": query,
            "candidates": candidates,
            "poolHash": digest(candidates),
        }
        atomic_json(root / "candidates.json", pool)
        resolve = lambda record: resolve_photo(db, record["url"])
        contact_sheets(pool, root / "contact-sheets", resolve)
        selection = {
            "poolHash": pool["poolHash"],
            "ids": [names[name] for name in SCENES],
        }
        atomic_json(root / "selection.json", selection)
        assets = normalize_selection(pool, selection, root / "book/assets", resolve)
        book = load_book(PACKAGE / "demo/book.json")
        book["assets"] = [
            {**a, "path": "assets/" + a["path"], "alt": SCENES[name][0]}
            for name, a in zip(SCENES, assets)
        ]
        for page in book["pages"] + book.get("covers", []):
            for placement in page["images"]:
                placement["asset"] = names[placement["asset"]]
        book = validate_book(book, root / "book")
        atomic_json(root / "book/book.json", book)
        build_book(book, root / "book", root / "first-proof")
        atomic_json(
            root / "demo-workspace.json",
            {
                "schemaVersion": 1,
                "kind": "wholly-invented-example",
                "book": "book/book.json",
                "review": "review",
                "scan": scan,
            },
        )
        return {
            "directory": str(root),
            "book": str(root / "book/book.json"),
            "review": str(root / "review"),
            "photos": len(assets),
            "pages": len(book["pages"]),
        }
    except Exception:
        shutil.rmtree(root)
        raise

"""Explicit local commands. AI tools are never started by demo, start or review."""

from __future__ import annotations
import argparse
import json
import sys
import webbrowser
from pathlib import Path
from . import __version__
from .book import atomic_json, load_book, apply_choices, digest


def json_file(path):
    path = Path(path)
    if path.stat().st_size > 16_000_000:
        raise ValueError("JSON file is too large")
    return json.loads(path.read_text(encoding="utf-8"))


def new_json(path, value):
    path = Path(path)
    if path.exists():
        raise FileExistsError("Choose a new output file")
    atomic_json(path, value)


def parser():
    p = argparse.ArgumentParser(
        description="Gather photos, choose captions and build a book locally."
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_text in [
        (
            "demo",
            "Create the complete invented example, including catalog and first proof.",
        ),
        (
            "start",
            "Create or reopen the invented example and open its local review app.",
        ),
    ]:
        c = sub.add_parser(name, help=help_text)
        c.add_argument("--directory", type=Path, required=True)
        if name == "start":
            c.add_argument("--no-browser", action="store_true")
            c.add_argument("--port", type=int, default=0)
    c = sub.add_parser(
        "review", help="Review an existing book in a separate local workspace."
    )
    c.add_argument("--book", type=Path, required=True)
    c.add_argument("--workspace", type=Path, required=True)
    c.add_argument("--port", type=int, default=0)
    c.add_argument("--no-browser", action="store_true")
    c = sub.add_parser(
        "build", help="Build a new PDF, HTML pages and paper checklists."
    )
    c.add_argument("--book", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--choices", type=Path)
    c = sub.add_parser(
        "capture", help="Capture already-built browser pages at a chosen DPI."
    )
    c.add_argument("--proof", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--dpi", type=int, default=180)
    c = sub.add_parser(
        "normalize",
        help="Make a metadata-free image derivative, preserving the source.",
    )
    c.add_argument("--source", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--id", required=True)
    catalog = sub.add_parser(
        "catalog", help="Index explicit Takeout ZIPs and manage optional captions."
    ).add_subparsers(dest="action", required=True)
    c = catalog.add_parser("scan")
    c.add_argument("--db", type=Path, required=True)
    c.add_argument("--zip", type=Path, nargs="+", required=True)
    c = catalog.add_parser("status")
    c.add_argument("--db", type=Path, required=True)
    c = catalog.add_parser("export")
    c.add_argument("--db", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c = catalog.add_parser("prepare")
    c.add_argument("--db", type=Path, required=True)
    c.add_argument("--work", type=Path, required=True)
    c.add_argument("--limit", type=int, default=20)
    c.add_argument(
        "--metadata",
        action="append",
        default=[],
        choices=["album", "title", "taken_at", "latitude", "longitude", "people"],
    )
    c = catalog.add_parser("prompt")
    c.add_argument("--manifest", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c = catalog.add_parser("ingest")
    c.add_argument("--db", type=Path, required=True)
    c.add_argument("--manifest", type=Path, required=True)
    c.add_argument("--response", type=Path, required=True)
    c = catalog.add_parser("codex")
    c.add_argument("--db", type=Path, required=True)
    c.add_argument("--manifest", type=Path, required=True)
    c.add_argument("--model", required=True)
    c.add_argument("--allow-upload", action="store_true")
    c = sub.add_parser("query", help="Rank photos with explicit literal search rules.")
    c.add_argument("--db", type=Path, required=True)
    c.add_argument("--query", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    for name in ["contact-sheets", "select"]:
        c = sub.add_parser(name)
        c.add_argument("--db", type=Path, required=True)
        c.add_argument("--pool", type=Path, required=True)
        c.add_argument("--output", type=Path, required=True)
        if name == "select":
            c.add_argument("--selection", type=Path, required=True)
    from .api_cli import add_parsers

    add_parsers(sub)
    return p


def serve(book, workspace, port, no_browser):
    if not 0 <= port <= 65535:
        raise ValueError("Use port 0 for a free local port, or a number up to 65535")
    from .server import make_server

    server = make_server(book, workspace, port=port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(
        "Open "
        + url
        + " in your browser. Keep this terminal open; press Ctrl+C to stop.",
        flush=True,
    )
    if not no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nReview server stopped. Confirmed choices remain on disk.")
    finally:
        server.server_close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print(
            "\nStopped. Saved work remains on disk; reconcile an interrupted API submission before continuing.",
            file=sys.stderr,
        )
        return 130
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print("Photo Book: " + str(error), file=sys.stderr)
        return 2


def execute(args):
    command = args.command
    if command == "api":
        from .api_cli import execute_api

        return execute_api(args)
    if command in {"demo", "start"}:
        from .demo import create_demo

        root = args.directory.resolve()
        if command == "demo":
            return create_demo(root)
        if not root.exists():
            create_demo(root)
        marker = json_file(root / "demo-workspace.json")
        if marker.get("kind") != "wholly-invented-example":
            raise ValueError(
                "This folder is not the invented example. Use review with your own book path."
            )
        return serve(
            root / "book/book.json", root / "review", args.port, args.no_browser
        )
    if command == "review":
        return serve(args.book, args.workspace, args.port, args.no_browser)
    if command == "build":
        from .proof import build_book

        book = load_book(args.book)
        if args.choices:
            choices = json_file(args.choices)
            if choices.get("pending"):
                raise ValueError(
                    "This export contains unsaved drafts. Resolve or save them in the review app before building."
                )
            book = apply_choices(book, choices)
        report = build_book(book, args.book.resolve().parent, args.output)
        return {
            "output": str(args.output.resolve()),
            "pages": len(report["pdf"]["pages"]),
            "sourceMinPPI": report["pdf"]["source_min_ppi"],
            "embeddedProofMinPPI": report["pdf"]["embedded_min_ppi"],
        }
    if command == "capture":
        from .capture import capture_pages

        return capture_pages(args.proof, args.output, dpi=args.dpi)
    if command == "normalize":
        from .book import normalize_asset

        return normalize_asset(args.source, args.output, args.id)
    if command == "catalog":
        from . import photo_catalog as cat

        if args.action == "scan":
            return cat.scan_archives(args.zip, args.db)
        if args.action == "status":
            import sqlite3
            from contextlib import closing

            with closing(
                sqlite3.connect(
                    args.db.resolve(strict=True).as_uri() + "?mode=ro", uri=True
                )
            ) as conn:
                return dict(
                    conn.execute(
                        "SELECT status,COUNT(*) FROM photos GROUP BY status"
                    ).fetchall()
                )
        if args.action == "export":
            return {
                "photos": cat.export_jsonl(args.db, args.output),
                "containsPrivateMetadata": True,
            }
        if args.action == "prepare":
            manifest = cat.prepare_batch(
                args.db, args.work, limit=args.limit, metadata_fields=args.metadata
            )
            return {
                "batchId": manifest["batch_id"],
                "photos": len(manifest["items"]),
                "errors": len(manifest["errors"]),
                "manifest": str(
                    (args.work / manifest["batch_id"] / "manifest.json").resolve()
                ),
            }
        if args.action == "prompt":
            if args.output.exists():
                raise FileExistsError("Choose a new prompt file")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                cat.build_caption_prompt(json_file(args.manifest)), encoding="utf-8"
            )
            return {"prompt": str(args.output), "uploads": 0}
        if args.action == "ingest":
            manifest = json_file(args.manifest)
            return cat.ingest_caption_result(
                args.db,
                json_file(args.response),
                [item["url"] for item in manifest["items"]],
            )
        if args.action == "codex":
            if not args.allow_upload:
                raise ValueError(
                    "This sends the prepared preview images and selected context to your configured Codex service. Review the manifest, then pass --allow-upload explicitly."
                )
            return cat.run_caption_batch(
                args.db,
                args.manifest,
                Path(__file__).with_name("caption-schema.json"),
                model=args.model,
                allow_upload=True,
            )
    if command == "query":
        from .curation import catalog_records, rank_candidates

        query = json_file(args.query)
        candidates = rank_candidates(catalog_records(args.db), query)
        pool = {
            "schemaVersion": 1,
            "query": query,
            "candidates": candidates,
            "poolHash": digest(candidates),
        }
        new_json(args.output, pool)
        return {"candidates": len(candidates), "poolHash": pool["poolHash"]}
    if command in {"contact-sheets", "select"}:
        from .curation import contact_sheets, normalize_selection, resolve_photo

        pool = json_file(args.pool)
        resolver = lambda record: resolve_photo(args.db, record["url"])
        if command == "contact-sheets":
            return contact_sheets(pool, args.output, resolver)
        assets = normalize_selection(
            pool, json_file(args.selection), args.output, resolver
        )
        return {
            "selected": len(assets),
            "manifest": str(args.output / "selection.json"),
        }
    raise ValueError("Unknown command")

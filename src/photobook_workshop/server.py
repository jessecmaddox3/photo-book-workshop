"""Loopback-only caption review and explicit local proof generation."""

from __future__ import annotations
import hashlib
import json
import mimetypes
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from filelock import FileLock
from .book import load_book, options_hash, confined, atomic_json, apply_choices, digest
from .layout import plan_page
from .proof import build_book, colors
from .review_store import ReviewStore, Conflict

PACKAGE = Path(__file__).resolve().parent


def make_server(book_path, workspace, *, port=0):
    book_path = Path(book_path).resolve(strict=True)
    book = load_book(book_path)
    root = book_path.parent
    workspace = Path(workspace).resolve()
    marker = workspace / ".workshop.json"
    if (
        root.is_relative_to(workspace)
        or workspace.is_relative_to(root)
        or workspace.is_relative_to(PACKAGE)
    ):
        raise ValueError(
            "Choose a separate review workspace outside the source book and installed package"
        )
    identity = {
        "bookId": book["bookId"],
        "optionsHash": options_hash(book),
        "bookHash": digest(book),
    }
    if workspace.exists() and any(workspace.iterdir()):
        if (
            not marker.is_file()
            or json.loads(marker.read_text(encoding="utf-8")) != identity
        ):
            raise ValueError(
                "This folder belongs to another workflow or book version; choose a new review folder"
            )
    else:
        workspace.mkdir(parents=True, exist_ok=True)
        atomic_json(marker, identity)

    def guard_workspace():
        for name in [
            ".workshop.json",
            ".port.json",
            ".server-start.lock",
            "choices.json",
            "choices.json.binding.json",
            "choices.json.lock",
            "proofs",
        ]:
            target = workspace / name
            if target.is_symlink():
                raise ValueError(
                    "Review files and output folders cannot be symbolic links"
                )
            confined(workspace, name, exists=False)
        if json.loads(marker.read_text(encoding="utf-8")) != identity:
            raise ValueError("Review workspace binding changed")

    guard_workspace()
    store = ReviewStore(book, workspace / "choices.json")
    store.load()
    assets = {a["id"]: a for a in book["assets"]}
    layouts = {p["id"]: plan_page(p, assets, book["physical"]) for p in book["pages"]}
    public_book = {
        "bookId": book["bookId"],
        "title": book["title"],
        "optionsHash": store.key,
        "pages": book["pages"],
        "layouts": layouts,
        "assets": [
            {
                "id": a["id"],
                "alt": a.get("alt", ""),
                "width": a["width"],
                "height": a["height"],
            }
            for a in assets.values()
        ],
    }
    building = threading.Lock()
    proof_files = {}
    outputs = workspace / "proofs"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, ctype="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            hosts = self.headers.get_all("Host", [])
            return len(hosts) == 1 and hosts[0] in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def route_path(self):
            raw = urlsplit(self.path)
            if raw.scheme or raw.netloc:
                return None
            path = unquote(raw.path)
            if "\\" in path or "\x00" in path or ".." in Path(path).parts:
                return None
            return path

        def do_GET(self):
            if not self.host_ok():
                return self.send(
                    403, {"error": "Use this app through its local address"}
                )
            path = self.route_path()
            try:
                if path == "/api/book":
                    return self.send(200, public_book)
                if path == "/api/identity":
                    return self.send(200, identity)
                if path == "/api/state":
                    guard_workspace()
                    return self.send(200, store.load())
                if path == "/api/proofs":
                    return self.send(200, {"proofs": list(proof_files)})
                if path == "/ui/palette.css":
                    palette = colors()
                    css = (
                        ":root{"
                        + "".join(f"--{k}:{v};" for k, v in palette.items())
                        + "--white:#fff;--body-font:Fredoka,system-ui,sans-serif;}"
                    )
                    return self.send(200, css.encode(), "text/css; charset=utf-8")
                static = {
                    "/": PACKAGE / "ui/index.html",
                    "/ui/app.js": PACKAGE / "ui/app.js",
                    "/ui/save-queue.js": PACKAGE / "ui/save-queue.js",
                    "/ui/style.css": PACKAGE / "ui/style.css",
                    "/fonts/Baloo2.ttf": PACKAGE / "fonts/Baloo2.ttf",
                    "/fonts/Fredoka.ttf": PACKAGE / "fonts/Fredoka.ttf",
                }
                file = static.get(path)
                if path and path.startswith("/asset/") and path[7:] in assets:
                    file = confined(root, assets[path[7:]]["path"])
                if path and path.startswith("/proofs/"):
                    parts = path.removeprefix("/proofs/").split("/", 1)
                    if (
                        len(parts) == 2
                        and parts[0] in proof_files
                        and parts[1] in proof_files[parts[0]]
                    ):
                        guard_workspace()
                        if (outputs / parts[0]).is_symlink():
                            raise ValueError("Proof folders cannot be symbolic links")
                        file = confined(
                            workspace, "proofs/" + parts[0] + "/" + parts[1]
                        )
                if file and file.is_file():
                    return self.send(
                        200,
                        file.read_bytes(),
                        mimetypes.guess_type(file.name)[0]
                        or "application/octet-stream",
                    )
                return self.send(404, {"error": "Page or image not found"})
            except (ValueError, OSError, KeyError):
                return self.send(
                    422,
                    {
                        "error": "The saved workspace or source is unavailable or changed. Preserve your files and check the terminal."
                    },
                )

        def do_POST(self):
            if not self.host_ok():
                return self.send(403, {"error": "Invalid local address"})
            origins = self.headers.get_all("Origin", [])
            if origins != ["http://" + self.headers["Host"]]:
                return self.send(
                    403,
                    {"error": "Open the review app at its local address before saving"},
                )
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Expected JSON"})
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if (
                    len(lengths) != 1
                    or not lengths[0].isdigit()
                    or not 0 < int(lengths[0]) <= 32000
                    or self.headers.get("Transfer-Encoding")
                ):
                    return self.send(413, {"error": "Invalid request size"})
                guard_workspace()
                command = json.loads(self.rfile.read(int(lengths[0])))
                path = self.route_path()
                if path == "/api/choice":
                    return self.send(200, store.save(command))
                if path == "/api/build":
                    if command != {"bookId": book["bookId"], "optionsHash": store.key}:
                        raise ValueError("Book version changed")
                    if not building.acquire(blocking=False):
                        return self.send(
                            409, {"error": "A proof is already being built"}
                        )
                    try:
                        if load_book(book_path) != book:
                            raise ValueError(
                                "Source book changed; restart with a new review workspace"
                            )
                        state = store.load()
                        chosen = apply_choices(book, state)
                        name = (
                            datetime.now(timezone.utc).strftime("proof-%Y%m%d-%H%M%S-")
                            + uuid4().hex[:8]
                        )
                        target = confined(workspace, "proofs/" + name, exists=False)
                        build_book(chosen, root, target)
                        atomic_json(target / "choices-used.json", state)
                        proof_files[name] = {
                            p.relative_to(target).as_posix()
                            for p in target.rglob("*")
                            if p.is_file()
                        }
                        return self.send(
                            200,
                            {
                                "id": name,
                                "links": {
                                    kind: "/proofs/" + name + "/" + file
                                    for kind, file in {
                                        "book": "book-proof.pdf",
                                        "pages": "html/index.html",
                                        "checklist": "checklist.pdf",
                                        "captions": "captions.pdf",
                                        "picksheet": "picksheet.pdf",
                                        "choices": "choices-used.json",
                                        "ledger": "proof-ledger.json",
                                    }.items()
                                },
                            },
                        )
                    finally:
                        building.release()
                return self.send(404, {"error": "Action not found"})
            except Conflict as e:
                return self.send(409, {"error": str(e), "current": e.current})
            except (ValueError, TypeError, KeyError, FileNotFoundError) as e:
                return self.send(422, {"error": str(e)[:300]})
            except OSError:
                return self.send(
                    500,
                    {
                        "error": "Could not save. Keep this tab open and export your drafts before checking the review folder."
                    },
                )

    with FileLock(str(workspace / ".server-start.lock"), timeout=10):
        guard_workspace()
        port_file = workspace / ".port.json"
        if port == 0 and port_file.exists():
            saved = json.loads(port_file.read_text(encoding="utf-8"))
            if (
                not isinstance(saved, dict)
                or set(saved) != {"port"}
                or type(saved["port"]) is not int
                or not 1 <= saved["port"] <= 65535
            ):
                raise ValueError(
                    "Invalid saved review port; preserve this workspace and recover its original browser address"
                )
            port = saved["port"]
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as error:
            raise OSError(
                "The saved review address is unavailable. Close another instance using this workspace, or free its port, then retry. No new address was chosen; pending browser drafts stay at the original address."
            ) from error
        try:
            atomic_json(port_file, {"port": server.server_port})
        except BaseException:
            server.server_close()
            raise
    server.daemon_threads = True
    server.book = book
    server.workspace = workspace
    return server

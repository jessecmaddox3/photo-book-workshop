"""Durable per-page caption choices with idempotent saves and visible conflicts."""

from __future__ import annotations
import json
import re
from pathlib import Path
from uuid import UUID
from filelock import FileLock
from .book import atomic_json, digest, options_hash, text


class Conflict(Exception):
    def __init__(self, current):
        self.current = current
        super().__init__(
            "A newer choice was saved. Review both versions before continuing."
        )


class ReviewStore:
    def __init__(self, book, path):
        self.book = book
        self.path = Path(path).resolve()
        self.key = options_hash(book)
        self.binding = self.path.with_name(self.path.name + ".binding.json")
        self.pages = {
            p["id"]: {o["id"] for o in p.get("options", [])} for p in book["pages"]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock", timeout=10)

    def _identity(self):
        return {
            "schemaVersion": 1,
            "bookId": self.book["bookId"],
            "optionsHash": self.key,
        }

    def _load(self):
        if self.binding.exists():
            if (
                self.binding.stat().st_size > 10000
                or json.loads(self.binding.read_text(encoding="utf-8"))
                != self._identity()
            ):
                raise ValueError(
                    "Saved choices binding is invalid or belongs to another book"
                )
            if not self.path.exists():
                raise ValueError(
                    "An established choices file is missing. Restore it from your export or backup; it will not be reset."
                )
        if not self.path.exists():
            return {**self._identity(), "choices": {}, "receipts": {}}
        if self.path.stat().st_size > 16_000_000:
            raise ValueError(
                "Saved choices are too large; preserve the file for recovery"
            )
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or set(state)
            != {"schemaVersion", "bookId", "optionsHash", "choices", "receipts"}
            or any(state.get(k) != v for k, v in self._identity().items())
        ):
            raise ValueError(
                "Invalid choices or another book/option version. Preserve the file and use a new choices file."
            )
        if (
            not isinstance(state["choices"], dict)
            or not isinstance(state["receipts"], dict)
            or len(state["receipts"]) > 10000
        ):
            raise ValueError("Invalid saved choices; preserve the file")
        for page, choice in state["choices"].items():
            self._choice(page, choice)
            if (
                set(choice) != {"optionId", "note", "revision"}
                or type(choice["revision"]) is not int
                or choice["revision"] < 1
            ):
                raise ValueError("Invalid saved choice/revision")
        revisions = {page: set() for page in state["choices"]}
        for operation, receipt in state["receipts"].items():
            try:
                valid_uuid = str(UUID(operation)) == operation
            except (ValueError, TypeError, AttributeError):
                valid_uuid = False
            if (
                not valid_uuid
                or not isinstance(receipt, dict)
                or set(receipt) != {"fingerprint", "revision", "pageId"}
            ):
                raise ValueError("Invalid saved receipt")
            page = receipt["pageId"]
            revision = receipt["revision"]
            fingerprint = receipt["fingerprint"]
            if (
                not isinstance(page, str)
                or page not in state["choices"]
                or type(revision) is not int
                or not 1 <= revision <= state["choices"][page]["revision"]
            ):
                raise ValueError("Invalid receipt revision or page")
            if (
                not isinstance(fingerprint, str)
                or not re.fullmatch("[0-9a-f]{64}", fingerprint)
                or revision in revisions[page]
            ):
                raise ValueError("Invalid receipt fingerprint or repeated revision")
            revisions[page].add(revision)
        for page, choice in state["choices"].items():
            if len(revisions[page]) != choice["revision"]:
                raise ValueError("Incomplete saved revision history")
        return state

    def _choice(self, page, choice):
        if (
            not isinstance(page, str)
            or page not in self.pages
            or not isinstance(choice, dict)
            or not {"optionId", "note"} <= set(choice)
        ):
            raise ValueError("Unknown page or incomplete choice")
        option = choice["optionId"]
        if option is not None and (
            not isinstance(option, str) or option not in self.pages[page]
        ):
            raise ValueError("Unknown caption option")
        text(choice["note"], 3000)

    def load(self):
        with self.lock:
            state = self._load()
            return {k: v for k, v in state.items() if k != "receipts"}

    def save(self, command):
        if not isinstance(command, dict) or set(command) != {
            "bookId",
            "optionsHash",
            "pageId",
            "operationId",
            "baseRevision",
            "optionId",
            "note",
        }:
            raise ValueError("Invalid save command")
        if (
            command["bookId"] != self.book["bookId"]
            or command["optionsHash"] != self.key
        ):
            raise ValueError("Option version mismatch")
        page = command["pageId"]
        self._choice(page, command)
        if type(command["baseRevision"]) is not int or command["baseRevision"] < 0:
            raise ValueError("Invalid base revision")
        try:
            operation = str(UUID(command["operationId"]))
        except (ValueError, TypeError, AttributeError) as e:
            raise ValueError("Invalid save identity") from e
        if operation != command["operationId"]:
            raise ValueError("Use a canonical save identity")
        fingerprint = digest(command)
        with self.lock:
            state = self._load()
            receipt = state["receipts"].get(operation)
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise ValueError(
                        "A save identity was reused with different content"
                    )
                return {
                    "saved": True,
                    "acceptedRevision": receipt["revision"],
                    "current": state["choices"][page],
                    "pageId": page,
                }
            current = state["choices"].get(
                page, {"optionId": None, "note": "", "revision": 0}
            )
            if current["revision"] != command["baseRevision"]:
                raise Conflict(current)
            if len(state["receipts"]) >= 10000:
                raise ValueError(
                    "Save history is full. Export choices and prepare a new reviewed option version."
                )
            saved = {
                "optionId": command["optionId"],
                "note": command["note"],
                "revision": current["revision"] + 1,
            }
            state["choices"][page] = saved
            state["receipts"][operation] = {
                "fingerprint": fingerprint,
                "revision": saved["revision"],
                "pageId": page,
            }
            if not self.binding.exists():
                atomic_json(self.binding, self._identity())
            atomic_json(self.path, state)
            return {
                "saved": True,
                "acceptedRevision": saved["revision"],
                "current": saved,
                "pageId": page,
            }

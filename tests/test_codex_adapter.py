import json
import subprocess
from pathlib import Path
import pytest
from photobook_workshop import photo_catalog as cat
from test_catalog import archive, sidecar, caption, URL, row


def test_explicit_codex_uses_pseudonymous_keys_and_preserves_each_attempt(
    tmp_path, monkeypatch
):
    db = tmp_path / "catalog.sqlite3"
    cat.scan_archives([archive(tmp_path / "a.zip", metadata=sidecar(), media=True)], db)
    manifest = cat.prepare_batch(db, tmp_path / "work")
    manifest_path = tmp_path / "work" / manifest["batch_id"] / "manifest.json"
    calls = []

    def fake(command, **kwargs):
        calls.append((command, kwargs))
        assert URL not in command[-1] and kwargs["timeout"] == 600
        ident = Path(command[command.index("--image") + 1]).stem
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"captions": [{**caption(), "url": ident}]})
        )
        return subprocess.CompletedProcess(command, 0, "invented stdout", "")

    monkeypatch.setattr(cat.subprocess, "run", fake)
    schema = Path(cat.__file__).with_name("caption-schema.json")
    with pytest.raises(ValueError):
        cat.run_caption_batch(db, manifest_path, schema, model="invented-model")
    assert not calls
    cat.run_caption_batch(
        db, manifest_path, schema, model="invented-model", allow_upload=True
    )
    cat.run_caption_batch(
        db, manifest_path, schema, model="invented-model", allow_upload=True
    )
    assert (
        len(calls) == 2
        and calls[0][1]["cwd"] != calls[1][1]["cwd"]
        and row(db)["status"] == "captioned"
    )
    assert all((c[1]["cwd"] / "result.json").exists() for c in calls)


def test_codex_timeout_never_imports_or_retries(tmp_path, monkeypatch):
    db = tmp_path / "catalog.sqlite3"
    cat.scan_archives([archive(tmp_path / "a.zip", metadata=sidecar(), media=True)], db)
    manifest = cat.prepare_batch(db, tmp_path / "work")
    manifest_path = tmp_path / "work" / manifest["batch_id"] / "manifest.json"
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 10, output=b"invented partial output")

    monkeypatch.setattr(cat.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="may have processed"):
        cat.run_caption_batch(
            db,
            manifest_path,
            Path(cat.__file__).with_name("caption-schema.json"),
            model="invented-model",
            allow_upload=True,
            timeout=10,
        )
    assert len(calls) == 1 and row(db)["status"] == "pending"


def test_codex_reservation_preflight_and_late_result_conflict(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "catalog.sqlite3"
    cat.scan_archives([archive(tmp_path / "a.zip", metadata=sidecar(), media=True)], db)
    manifest = cat.prepare_batch(db, tmp_path / "work")
    manifest_path = tmp_path / "work" / manifest["batch_id"] / "manifest.json"
    calls = []

    def late(command, **kwargs):
        calls.append(command)
        cat.ingest_caption_result(
            db,
            {
                "captions": [
                    {**caption(), "caption": "A newer manually reviewed description."}
                ]
            },
            [URL],
        )
        ident = Path(command[command.index("--image") + 1]).stem
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"captions": [{**caption(), "url": ident}]})
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cat.subprocess, "run", late)
    schema = Path(cat.__file__).with_name("caption-schema.json")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE photos SET status='submitted'")
    with pytest.raises(ValueError, match="reserved"):
        cat.run_caption_batch(
            db, manifest_path, schema, model="invented-model", allow_upload=True
        )
    assert not calls
    with sqlite3.connect(db) as c:
        c.execute("UPDATE photos SET status='pending'")
    with pytest.raises(ValueError, match="Catalog changed"):
        cat.run_caption_batch(
            db, manifest_path, schema, model="invented-model", allow_upload=True
        )
    assert (
        len(calls) == 1
        and row(db)["attempts"] == 1
        and json.loads(row(db)["caption_json"])["caption"]
        == "A newer manually reviewed description."
    )

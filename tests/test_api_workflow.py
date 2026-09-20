import copy
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from PIL import Image
from photobook_workshop.api_workflow import initialize, BatchWorkspace
from photobook_workshop.photo_catalog import SCHEMA
from test_api_batch import config


def caption():
    return {
        "caption": "An invented blue balloon above a painted garden.",
        "keywords": ["balloon", "garden"],
        "unnamed_people": [],
        "pets": [],
        "quality": {
            "sharpness": "good",
            "exposure": "good",
            "eyes_open": "not_applicable",
            "faces_uncut": "not_applicable",
        },
        "print_recommendation": True,
        "print_reason": "Clear illustration.",
        "hero_candidate": False,
        "composition": {
            "subject_position": "center",
            "square_crop": "possible",
            "headroom": "Room above.",
        },
        "visible_text": [],
        "non_photo": True,
        "non_photo_type": "illustration",
    }


class FakeProvider:
    def __init__(self):
        self.files = {}
        self.batches = {}
        self.uploads = 0
        self.creates = 0
        self.fault = None
        self.reads = 0

    def upload(self, path, operation):
        self.uploads += 1
        ident = "file-" + str(self.uploads)
        self.files[ident] = Path(path).read_bytes()
        if self.fault == "upload":
            raise TimeoutError("Invented lost upload reply")
        return ident

    def create(self, file_id, metadata):
        self.creates += 1
        ident = "batch-" + str(self.creates)
        obj = {
            "id": ident,
            "input_file_id": file_id,
            "endpoint": "/v1/responses",
            "metadata": copy.deepcopy(metadata),
            "status": "in_progress",
            "output_file_id": None,
            "error_file_id": None,
        }
        self.batches[ident] = obj
        if self.fault == "create":
            raise TimeoutError("Invented lost create reply")
        return copy.deepcopy(obj)

    def get_batch(self, ident):
        self.reads += 1
        return copy.deepcopy(self.batches[ident])

    def download(self, ident, limit):
        value = self.files[ident]
        if len(value) > limit:
            raise ValueError("Too large")
        return value

    def complete(self, ident, *, mode="success", status="completed"):
        batch = self.batches[ident]
        requests = [
            json.loads(line) for line in self.files[batch["input_file_id"]].splitlines()
        ]
        records = []
        for index, request in enumerate(requests):
            if mode == "missing" and index == len(requests) - 1:
                continue
            usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
            if mode == "unknown-usage":
                usage = {"total_tokens": 123}
            body = {
                "status": "completed",
                "output_text": json.dumps(caption()),
                "usage": usage,
            }
            if mode == "invalid-caption":
                body["output_text"] = '{"wrong":"shape"}'
            record = {
                "custom_id": request["custom_id"],
                "response": {"status_code": 200, "body": body},
                "error": None,
            }
            if mode == "error":
                record["error"] = {"code": "invented_error"}
            records.append(record)
        if mode == "duplicate":
            records.append(copy.deepcopy(records[0]))
        if mode == "foreign":
            records.append({"custom_id": "foreign", "error": {"code": "invented"}})
        output = "output-" + ident
        self.files[output] = b"\n".join(json.dumps(r).encode() for r in records)
        batch.update(status=status, output_file_id=output)

    def find_operation(self, operation):
        return {
            "batches": [
                b["id"]
                for b in self.batches.values()
                if b["metadata"]["photobook_operation"] == operation
            ],
            "files": [],
            "completeSearch": False,
        }


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (80, 60), "blue").save(preview)
    urls = [f"https://example.invalid/photo/{i}" for i in range(4)]
    with sqlite3.connect(db) as c:
        c.executescript(SCHEMA)
        for url in urls:
            c.execute(
                "INSERT INTO photos(url,album,title,people_json,raw_metadata_json,media_ext,updated_at) VALUES(?,'Invented','Scene','[]','{}','.jpg','2030-01-01')",
                (url,),
            )
    work = tmp_path / "api"
    initialize(db, work)
    workspace = BatchWorkspace(db, work)
    manifest = {"items": [{"url": url, "preview": str(preview)} for url in urls]}
    return workspace, manifest, FakeProvider()


def prepared(ws, manifest, *, count=1, **changes):
    return ws.prepare(
        {"items": manifest["items"][:count]}, config(enabled=True, **changes)
    )


def submit(ws, job, provider):
    return ws.submit(job["id"], provider, allow_upload=True, smoke=True)


def test_complete_attempt_and_repeat_collection_are_idempotent(setup):
    ws, m, p = setup
    job = prepared(ws, m, count=2)
    submitted = submit(ws, job, p)
    p.complete(submitted["batchId"])
    result = ws.collect(job["id"], p)
    assert (
        result["captioned"] == 2
        and result["retry"] == 0
        and result["knownMicros"] == 280
        and result["heldMicros"] == 0
    )
    reads = p.reads
    assert ws.collect(job["id"], p) == result and p.reads == reads
    with sqlite3.connect(ws.db) as c:
        assert c.execute(
            "SELECT attempts FROM photos WHERE status='captioned'"
        ).fetchall() == [(1,), (1,)]


def test_default_disabled_or_missing_explicit_consent_never_uploads(setup):
    ws, m, p = setup
    job = ws.prepare({"items": m["items"][:1]}, config())
    with pytest.raises(ValueError):
        submit(ws, job, p)
    enabled = prepared(ws, m)
    with pytest.raises(ValueError):
        ws.submit(enabled["id"], p, smoke=True)
    assert p.uploads == p.creates == 0


def test_changed_sealed_request_never_reserves_or_uploads(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    path = ws.root / "jobs" / job["id"] / "requests.jsonl"
    request = json.loads(path.read_text())
    request["body"]["model"] = "changed-model"
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError):
        submit(ws, job, p)
    assert (
        p.uploads == 0
        and ws.job(job["id"])["status"] == "prepared"
        and ws.status()["reservedMicros"] == 0
    )


def test_upload_reply_loss_is_reconciled_without_duplicate_upload(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    p.fault = "upload"
    with pytest.raises(TimeoutError):
        submit(ws, job, p)
    assert ws.job(job["id"])["status"] == "upload_unknown"
    with pytest.raises(ValueError):
        submit(ws, job, p)
    p.files["wrong"] = b"wrong bytes"
    with pytest.raises(ValueError):
        ws.attach_upload(job["id"], "wrong", p)
    ws.attach_upload(job["id"], "file-1", p)
    p.fault = None
    ws.resume(job["id"], p, allow_upload=True)
    assert p.uploads == 1 and p.creates == 1


def test_create_reply_loss_requires_verified_remote_attachment(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    p.fault = "create"
    with pytest.raises(TimeoutError):
        submit(ws, job, p)
    reopened = BatchWorkspace(ws.db, ws.root)
    assert reopened.job(job["id"])["status"] == "submission_unknown"
    with pytest.raises(ValueError):
        reopened.resume(job["id"], p, allow_upload=True)
    with pytest.raises(ValueError):
        reopened.abandon_before_create(job["id"])
    bad = copy.deepcopy(p.batches["batch-1"])
    bad["id"] = "wrong"
    bad["metadata"]["photobook_operation"] = "different"
    p.batches["wrong"] = bad
    with pytest.raises(ValueError):
        reopened.attach_batch(job["id"], "wrong", p)
    reopened.attach_batch(job["id"], "batch-1", p)
    p.complete("batch-1")
    assert reopened.collect(job["id"], p)["captioned"] == 1
    assert p.uploads == 1 and p.creates == 1


def test_frozen_reservation_survives_restart_with_smaller_new_allowance(setup):
    ws, m, p = setup
    first = prepared(ws, m, ceiling_usd="0.007")
    submit(ws, first, p)
    reopened = BatchWorkspace(ws.db, ws.root)
    second = reopened.prepare(
        {"items": m["items"][1:2]},
        config(enabled=True, input_allowance=1, ceiling_usd="0.007"),
    )
    with pytest.raises(ValueError, match="ceiling"):
        submit(reopened, second, p)
    assert reopened.job(first["id"])["heldMicros"] == 6400 and p.uploads == 1


@pytest.mark.parametrize("mode", ["unknown-usage", "missing"])
def test_unknown_usage_keeps_reservation_until_explicit_settlement(setup, mode):
    ws, m, p = setup
    job = prepared(ws, m)
    submitted = submit(ws, job, p)
    p.complete(submitted["batchId"], mode=mode)
    result = ws.collect(job["id"], p)
    assert result["heldMicros"] == 6400 and not result["usageSettled"]
    result = ws.settle_usage(
        job["id"], "0.000140", "Checked an invented provider usage statement."
    )
    assert (
        result["knownMicros"] == 140
        and result["heldMicros"] == 0
        and result["usageSettled"]
    )


def test_invalid_caption_is_still_billed_when_usage_is_known(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    submitted = submit(ws, job, p)
    p.complete(submitted["batchId"], mode="invalid-caption")
    result = ws.collect(job["id"], p)
    assert (
        result["retry"] == 1 and result["knownMicros"] == 140 and result["usageSettled"]
    )


@pytest.mark.parametrize("mode", ["duplicate", "foreign"])
def test_result_integrity_error_preserves_photo_reservation(setup, mode):
    ws, m, p = setup
    job = prepared(ws, m)
    submitted = submit(ws, job, p)
    p.complete(submitted["batchId"], mode=mode)
    with pytest.raises(ValueError):
        ws.collect(job["id"], p)
    assert (
        ws.job(job["id"])["status"] == "review_required"
        and ws.status()["reservedMicros"] == 6400
    )
    with sqlite3.connect(ws.db) as c:
        assert (
            c.execute(
                "SELECT status FROM photos WHERE url=?", (m["items"][0]["url"],)
            ).fetchone()[0]
            == "submitted"
        )


def test_stale_attempt_never_changes_newer_owner_or_double_counts_old_results(setup):
    ws, m, p = setup
    old = prepared(ws, m)
    submit(ws, old, p)
    url = m["items"][0]["url"]
    # Simulate an independently authorized later attempt superseding the old owner.
    with sqlite3.connect(ws.db) as c:
        c.execute("DELETE FROM api_owners WHERE url=?", (url,))
        c.execute("UPDATE photos SET status='retry' WHERE url=?", (url,))
    new = prepared(ws, m)
    submit(ws, new, p)
    p.complete("batch-1", mode="error")
    old_result = ws.collect(old["id"], p)
    assert old_result["retry"] == 1 and ws.results(old["id"])[0]["applied"] is False
    with sqlite3.connect(ws.db) as c:
        assert (
            c.execute("SELECT job_id FROM api_owners WHERE url=?", (url,)).fetchone()[0]
            == new["id"]
        )
        assert (
            c.execute("SELECT status FROM photos WHERE url=?", (url,)).fetchone()[0]
            == "submitted"
        )
    p.complete("batch-2")
    ws.collect(new["id"], p)
    assert ws.collect(old["id"], p) == old_result


def test_smoke_review_is_bound_to_exact_quality_settings(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    submitted = submit(ws, job, p)
    p.complete(submitted["batchId"])
    ws.collect(job["id"], p)
    ids = [r["custom_id"] for r in ws.results(job["id"])]
    with pytest.raises(ValueError):
        ws.review_smoke(job["id"], [])
    ws.review_smoke(job["id"], ids)
    next_job = ws.prepare({"items": m["items"][1:2]}, config(enabled=True))
    ws.submit(next_job["id"], p, allow_upload=True)
    changed = ws.prepare(
        {"items": m["items"][2:3]},
        config(enabled=True, model="other-invented-model", max_active=3),
    )
    with pytest.raises(ValueError, match="smoke"):
        ws.submit(changed["id"], p, allow_upload=True)
    assert p.uploads == 2


def test_two_submitters_have_one_reservation_winner(setup):
    ws, m, p = setup
    a = prepared(ws, m)
    b = prepared(ws, m)
    other = BatchWorkspace(ws.db, ws.root)

    def attempt(w, job):
        try:
            return submit(w, job, p)["status"]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: attempt(*pair), [(ws, a), (other, b)]))
    assert sorted(results) == ["rejected", "submitted"] and p.uploads == p.creates == 1


def test_presubmit_validation_failure_does_not_strand_reservations(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    with sqlite3.connect(ws.db) as c:
        c.execute(
            "UPDATE photos SET status='captioned' WHERE url=?", (m["items"][0]["url"],)
        )
    with pytest.raises(ValueError):
        submit(ws, job, p)
    assert (
        ws.job(job["id"])["status"] == "prepared" and ws.status()["reservedMicros"] == 0
    )


def test_job_and_workspace_bindings_reject_copied_catalog(setup, tmp_path):
    import shutil

    ws, m, p = setup
    other = tmp_path / "copied.sqlite3"
    shutil.copyfile(ws.db, other)
    with pytest.raises(ValueError):
        BatchWorkspace(other, ws.root)


def test_attempt_limit_applies_to_direct_submissions(setup):
    ws, m, p = setup
    job = prepared(ws, m, max_attempts=1)
    submit(ws, job, p)
    p.complete("batch-1", mode="error")
    ws.collect(job["id"], p)
    ws.review_failures(job["id"], [r["custom_id"] for r in ws.results(job["id"])])
    retry = prepared(ws, m, max_attempts=1)
    with pytest.raises(ValueError, match="attempt limit"):
        submit(ws, retry, p)
    assert p.uploads == 1 and ws.job(retry["id"])["status"] == "prepared"


def test_unusable_terminal_results_have_audited_recovery_with_cost_hold(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    submit(ws, job, p)
    p.complete("batch-1", mode="duplicate")
    with pytest.raises(ValueError):
        ws.collect(job["id"], p)
    path = ws.root / "jobs" / job["id"] / "results/combined.jsonl"
    original = path.read_bytes()
    p.batches["batch-1"]["status"] = "in_progress"
    with pytest.raises(ValueError, match="terminal"):
        ws.resolve_failed_results(
            job["id"], p, "Confirmed unusable duplicate result identities."
        )
    p.batches["batch-1"]["status"] = "completed"
    resolved = ws.resolve_failed_results(
        job["id"], p, "Confirmed unusable duplicate result identities."
    )
    assert (
        resolved["status"] == "collected"
        and resolved["heldMicros"] == 6400
        and not resolved["usageSettled"]
        and path.read_bytes() == original
    )
    assert ws.results(job["id"])[0]["outcome"] == "retry"
    ws.review_failures(job["id"], [r["custom_id"] for r in ws.results(job["id"])])
    ws.settle_usage(
        job["id"], "0.00014", "Verified an invented final billing statement."
    )
    another = prepared(ws, m)
    submit(ws, another, p)
    assert p.creates == 2

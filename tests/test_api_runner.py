import json
import sqlite3
import pytest
from photobook_workshop.api_runner import run, watch
from test_api_batch import config
from test_api_workflow import setup, prepared, submit


def ready(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    submit(ws, job, p)
    p.complete("batch-1")
    ws.collect(job["id"], p)
    ws.review_smoke(job["id"], [r["custom_id"] for r in ws.results(job["id"])])
    with sqlite3.connect(ws.db) as c:
        c.execute(
            "UPDATE photos SET source_zip='invented.zip',media_member='scene.jpg'"
        )
    return ws, m, p


def test_runner_requires_review_and_never_uploads_implicitly(setup):
    ws, m, p = setup
    with pytest.raises(ValueError):
        run(ws, p, config(enabled=True))
    assert (
        run(ws, p, config(enabled=True), allow_upload=True)["reason"]
        == "smoke_review_required"
    )
    assert p.uploads == 0


def test_runner_caps_parallel_jobs_and_resumes_collection_without_duplicates(setup):
    ws, m, p = ready(setup)
    conf = config(enabled=True, batch_size=1, max_active=2)
    prepare = lambda urls: {"items": [i for i in m["items"] if i["url"] in urls]}
    result = run(ws, p, conf, allow_upload=True, prepare_previews=prepare)
    assert (
        len(result["started"]) == 2
        and result["status"]["activeJobs"] == 2
        and p.creates == 3
    )

    def complete_pending(seconds):
        for key in list(p.batches):
            p.complete(key)

    result = run(
        ws,
        p,
        conf,
        allow_upload=True,
        max_cycles=3,
        sleep=complete_pending,
        prepare_previews=prepare,
    )
    assert (
        result["reason"] == "complete"
        and p.creates == 4
        and result["status"]["photos"] == {"captioned": 4}
    )


def test_watch_does_not_submit_and_poll_failure_preserves_state(setup):
    ws, m, p = setup
    job = prepared(ws, m)
    submit(ws, job, p)
    result = watch(ws, p, max_cycles=1)
    assert result["reason"] == "cycle_limit" and p.creates == 1
    original = p.get_batch

    def unavailable(ident):
        raise TimeoutError("Invented polling outage")

    p.get_batch = unavailable
    with pytest.raises(TimeoutError):
        watch(ws, p, max_cycles=2)
    assert ws.job(job["id"])["status"] == "submitted" and p.creates == 1
    p.get_batch = original
    result = watch(ws, p, max_cycles=2, sleep=lambda seconds: p.complete("batch-1"))
    assert result["reason"] == "collected" and p.creates == 1


def test_runner_stops_after_failed_collection_before_retry(setup):
    ws, m, p = ready(setup)
    conf = config(enabled=True, batch_size=1)
    result = run(
        ws,
        p,
        conf,
        allow_upload=True,
        max_jobs=1,
        prepare_previews=lambda urls: {
            "items": [i for i in m["items"] if i["url"] in urls]
        },
    )
    p.complete("batch-2", mode="error")
    result = run(ws, p, conf, allow_upload=True)
    assert result["reason"] == "failure_review_required" and p.creates == 2


def test_unreadable_or_exhausted_photos_never_report_complete(setup):
    ws, m, p = ready(setup)
    with sqlite3.connect(ws.db) as c:
        c.execute("UPDATE photos SET status='preview_error' WHERE status='pending'")
    assert (
        run(ws, p, config(enabled=True), allow_upload=True)["reason"]
        == "needs_attention"
    )
    assert p.creates == 1

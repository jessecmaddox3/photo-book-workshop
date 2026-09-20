"""Bounded collect/watch/run loops; all spending still passes transactional guards."""

from __future__ import annotations
import json
import sqlite3
import time
from contextlib import closing
from .api_batch import validate_config, quality_hash, reserve_micros, micros
from .api_workflow import ACTIVE, UNCERTAIN


def local_jobs(workspace):
    return [
        workspace.job(j["id"])
        for j in workspace.status()["jobs"]
        if j["workspaceId"] == workspace.binding["workspaceId"]
    ]


def collect_all(workspace, provider):
    result = []
    for job in local_jobs(workspace):
        if job["status"] == "submitted":
            result.append(workspace.collect(job["id"], provider))
    return result


def eligible_urls(workspace, config, limit):
    with closing(sqlite3.connect(workspace.db)) as conn:
        rows = conn.execute(
            "SELECT url,attempts FROM photos WHERE status IN ('pending','retry') AND source_zip IS NOT NULL AND media_member IS NOT NULL AND url NOT IN (SELECT url FROM api_owners) ORDER BY COALESCE(taken_ts,0),url"
        )
        result = []
        for url, manual_attempts in rows:
            attempts = sum(
                json.loads(r[0])["reservedMicros"] > 0
                and json.loads(r[0])["status"] != "abandoned"
                for r in conn.execute(
                    "SELECT j.state_json FROM api_jobs j JOIN api_items i ON i.job_id=j.id WHERE i.url=?",
                    (url,),
                )
            )
            if max(attempts, manual_attempts) < config["max_attempts"]:
                result.append(url)
            if len(result) >= limit:
                break
    return result


def run(
    workspace,
    provider,
    config,
    *,
    allow_upload=False,
    max_cycles=1,
    max_jobs=10,
    sleep=time.sleep,
    prepare_previews=None,
):
    """Collect before filling free slots. Stops at every unresolved human decision."""
    config = validate_config(config)
    if allow_upload is not True or config["enabled"] is not True:
        raise ValueError("Run needs API enablement and --allow-upload")
    if (
        type(max_cycles) is not int
        or not 1 <= max_cycles <= 10000
        or type(max_jobs) is not int
        or not 1 <= max_jobs <= 10000
    ):
        raise ValueError("Use cycle and new-job limits from 1 to 10000")
    if prepare_previews is None:
        from .photo_catalog import prepare_batch

        prepare_previews = lambda urls: prepare_batch(
            workspace.db,
            workspace.root / "previews",
            urls=urls,
            limit=len(urls),
            preview_max=config["preview_max"],
            metadata_fields=config["metadata_fields"],
        )
    started = []
    reason = "cycle_limit"
    for cycle in range(max_cycles):
        collect_all(workspace, provider)
        status = workspace.status()
        if any(j["status"] in UNCERTAIN for j in status["jobs"]):
            reason = "reconciliation_required"
            break
        if any(
            j["retry"] and not workspace.job(j["id"])["failuresReviewed"]
            for j in status["jobs"]
            if j["workspaceId"] == workspace.binding["workspaceId"]
        ):
            reason = "failure_review_required"
            break
        with closing(sqlite3.connect(workspace.db)) as conn:
            if not conn.execute(
                "SELECT 1 FROM api_reviews WHERE quality_hash=?",
                (quality_hash(config),),
            ).fetchone():
                reason = "smoke_review_required"
                break
        while len(started) < max_jobs:
            status = workspace.status()
            if any(
                j["status"] in {"reserved", "uploaded"} for j in local_jobs(workspace)
            ):
                reason = "resume_required"
                break
            if status["activeJobs"] >= config["max_active"]:
                reason = "waiting"
                break
            available = (
                micros(config["ceiling_usd"])
                - status["estimatedSpentMicros"]
                - status["reservedMicros"]
            )
            capacity = min(config["batch_size"], available // reserve_micros(config))
            if capacity < 1:
                reason = "estimated_ceiling_reached"
                break
            prepared = [
                j
                for j in local_jobs(workspace)
                if j["status"] == "prepared" and j["config"] == config
            ]
            resumable = [
                j
                for j in local_jobs(workspace)
                if j["status"] in {"reserved", "uploaded"}
            ]
            if resumable:
                reason = "resume_required"
                break
            if prepared:
                job = prepared[0]
                if job["requestCount"] > capacity:
                    reason = "prepared_job_exceeds_remaining_estimate"
                    break
            else:
                urls = eligible_urls(workspace, config, capacity)
                if not urls:
                    unfinished = any(
                        n
                        for state, n in status["photos"].items()
                        if state != "captioned"
                    )
                    reason = (
                        "waiting"
                        if status["activeJobs"]
                        else ("needs_attention" if unfinished else "complete")
                    )
                    break
                manifest = prepare_previews(urls)
                if not manifest.get("items"):
                    reason = "preview_errors"
                    break
                job = workspace.prepare(manifest, config)
            workspace.submit(job["id"], provider, allow_upload=True)
            started.append(job["id"])
            reason = "waiting"
        if len(started) >= max_jobs:
            reason = "new_job_limit"
            break
        if reason != "waiting" or cycle + 1 == max_cycles:
            break
        sleep(config["poll_seconds"])
    return {"reason": reason, "started": started, "status": workspace.status()}


def watch(workspace, provider, *, poll_seconds=60, max_cycles=1440, sleep=time.sleep):
    if (
        type(poll_seconds) is not int
        or not 5 <= poll_seconds <= 3600
        or type(max_cycles) is not int
        or not 1 <= max_cycles <= 10000
    ):
        raise ValueError("Invalid bounded watch interval or cycle limit")
    for cycle in range(max_cycles):
        collect_all(workspace, provider)
        jobs = local_jobs(workspace)
        if any(j["status"] in ACTIVE - {"submitted"} for j in jobs):
            return {"reason": "reconciliation_required", "status": workspace.status()}
        if not any(j["status"] == "submitted" for j in jobs):
            return {"reason": "collected", "status": workspace.status()}
        if cycle + 1 < max_cycles:
            sleep(poll_seconds)
    return {"reason": "cycle_limit", "status": workspace.status()}

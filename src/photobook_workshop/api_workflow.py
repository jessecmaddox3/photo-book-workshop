"""Sealed local batches, attempt-owned results and recoverable optional API work.

The catalog is the transaction authority for reservations and cost accounting.
Every chargeable operation is preceded by a durable state transition. An
uncertain response is reconciled explicitly, never treated as permission to retry.
"""

from __future__ import annotations
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from filelock import FileLock
from .book import atomic_json, confined, digest, identifier, normalized_image
from .api_batch import (
    validate_config,
    quality_hash,
    reserve_micros,
    micros,
    build_batch_request,
    custom_id_for_url,
    parse_results,
)

TABLES = """
CREATE TABLE IF NOT EXISTS api_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_jobs (id TEXT PRIMARY KEY,state_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_items (job_id TEXT NOT NULL,url TEXT NOT NULL,custom_id TEXT NOT NULL,result_json TEXT,PRIMARY KEY(job_id,url),UNIQUE(job_id,custom_id));
CREATE TABLE IF NOT EXISTS api_owners (url TEXT PRIMARY KEY,job_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_reviews (quality_hash TEXT PRIMARY KEY,job_id TEXT NOT NULL,result_hash TEXT NOT NULL,reviewed_at TEXT NOT NULL);
"""
UNCERTAIN = {
    "uploading",
    "upload_unknown",
    "creating",
    "submission_unknown",
    "review_required",
}
ACTIVE = {
    "reserved",
    "uploading",
    "upload_unknown",
    "uploaded",
    "creating",
    "submission_unknown",
    "submitted",
    "review_required",
}
TERMINAL = {"completed", "failed", "expired", "cancelled"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dumps(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def initialize(db, workspace):
    db = Path(db).resolve(strict=True)
    workspace = Path(workspace).resolve()
    if db.is_relative_to(workspace):
        raise ValueError("Choose a separate empty batch workspace")
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError("Choose a new empty batch workspace")
    with closing(sqlite3.connect(db)) as c:
        if (
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='photos'"
            ).fetchone()
            is None
        ):
            raise ValueError("Index a photo catalog first")
        c.executescript(TABLES)
        c.execute(
            "INSERT OR IGNORE INTO api_meta VALUES(?,?)", ("catalog_id", uuid4().hex)
        )
        catalog_id = c.execute(
            "SELECT value FROM api_meta WHERE key='catalog_id'"
        ).fetchone()[0]
        c.commit()
    workspace.mkdir(parents=True, exist_ok=True)
    binding = {
        "schemaVersion": 1,
        "catalogId": catalog_id,
        "workspaceId": uuid4().hex,
        "database": str(db),
        "workspace": str(workspace),
    }
    atomic_json(workspace / "binding.json", binding)
    (workspace / "jobs").mkdir()
    return binding


class BatchWorkspace:
    def __init__(self, db, workspace):
        self.db = Path(db).resolve(strict=True)
        self.root = Path(workspace).resolve(strict=True)
        self.binding = self._read_binding()
        self.guard()
        self.lock = FileLock(str(self.root / ".batch.lock"), timeout=30)

    def _read_binding(self):
        path = self.root / "binding.json"
        if path.is_symlink() or path.stat().st_size > 10000:
            raise ValueError("Invalid batch workspace binding")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schemaVersion") != 1
            or value.get("database") != str(self.db)
            or value.get("workspace") != str(self.root)
        ):
            raise ValueError(
                "This batch workspace was moved or belongs to another catalog"
            )
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as c:
            row = c.execute(
                "SELECT value FROM api_meta WHERE key='catalog_id'"
            ).fetchone()
            if not row or row[0] != value.get("catalogId"):
                raise ValueError("Catalog identity changed")
        return value

    def guard(self):
        for name in ["binding.json", "jobs", ".batch.lock"]:
            if (self.root / name).is_symlink():
                raise ValueError("Batch workspace files cannot be symbolic links")
            confined(self.root, name, exists=False)
        if self._read_binding() != self.binding:
            raise ValueError("Batch workspace binding changed")

    @contextmanager
    def transaction(self):
        self.guard()
        with closing(sqlite3.connect(self.db, timeout=30)) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def _job(self, c, job_id):
        identifier(job_id)
        row = c.execute(
            "SELECT state_json FROM api_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError("Unknown local API job")
        state = json.loads(row[0])
        if state["workspaceId"] != self.binding["workspaceId"]:
            raise ValueError("This job belongs to another batch workspace")
        return state

    def _save(self, c, state):
        state["updatedAt"] = now()
        c.execute(
            "UPDATE api_jobs SET state_json=? WHERE id=?", (dumps(state), state["id"])
        )

    def job(self, job_id):
        self.guard()
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as c:
            return self._job(c, job_id)

    def _update(self, state, **changes):
        state.update(changes)
        with self.transaction() as c:
            self._save(c, state)
        return state

    def _path(self, job_id, relative="seal.json"):
        identifier(job_id)
        if (self.root / "jobs" / job_id).is_symlink():
            raise ValueError("Job folders cannot be symbolic links")
        return confined(self.root, "jobs/" + job_id + "/" + relative, exists=False)

    def verify(self, state):
        raw = self._path(state["id"]).read_bytes()
        if len(raw) > 16_000_000 or sha(raw) != state["sealHash"]:
            raise ValueError("Sealed job manifest changed")
        seal = json.loads(raw)
        if (
            seal["catalogId"] != self.binding["catalogId"]
            or seal["workspaceId"] != self.binding["workspaceId"]
            or seal["jobId"] != state["id"]
            or seal["config"] != state["config"]
        ):
            raise ValueError("Prepared artifacts belong to another job or policy")
        for name, expected in seal["files"].items():
            path = self._path(state["id"], name)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 190_000_000
                or sha(path.read_bytes()) != expected
            ):
                raise ValueError("A prepared request, mapping or preview changed")
        return seal

    def prepare(self, manifest, config):
        config = validate_config(config)
        items = manifest.get("items") if isinstance(manifest, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ValueError("Prepare 1-500 preview images")
        urls = [item.get("url") for item in items]
        if any(
            not isinstance(url, str) or not 1 <= len(url) <= 4096 for url in urls
        ) or len(set(urls)) != len(urls):
            raise ValueError("Missing or duplicate photo identity")
        ids = [custom_id_for_url(url) for url in urls]
        if len(set(ids)) != len(ids):
            raise ValueError("Photo identity hash collision")
        with self.lock:
            self.guard()
            job_id = "job-" + uuid4().hex
            temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=self.root))
            destination = self.root / "jobs" / job_id
            try:
                (temporary / "images").mkdir()
                mapping = []
                requests = []
                files = {}
                total = 0
                for item in items[: config["batch_size"]]:
                    image = normalized_image(Path(item["preview"]).read_bytes())
                    image.thumbnail((config["preview_max"], config["preview_max"]))
                    image_bytes = io.BytesIO()
                    image.save(image_bytes, "JPEG", quality=88, optimize=True)
                    image.close()
                    image_raw = image_bytes.getvalue()
                    ident = custom_id_for_url(item["url"])
                    relative = "images/" + ident + ".jpg"
                    preview = temporary / relative
                    preview.write_bytes(image_raw)
                    request = build_batch_request(
                        {**item, "preview": str(preview)}, config
                    )
                    line = (dumps(request) + "\n").encode("utf-8")
                    if total + len(line) > config["max_bytes"]:
                        preview.unlink()
                        if not mapping:
                            raise ValueError(
                                "One image request exceeds the configured batch byte limit"
                            )
                        break
                    requests.append(line)
                    total += len(line)
                    mapping.append(
                        {
                            "custom_id": ident,
                            "url": item["url"],
                            "preview": relative,
                            "source_sha256": item.get("source_sha256"),
                        }
                    )
                    files[relative] = sha(image_raw)
                request_raw = b"".join(requests)
                (temporary / "requests.jsonl").write_bytes(request_raw)
                files["requests.jsonl"] = sha(request_raw)
                atomic_json(temporary / "mapping.json", mapping)
                files["mapping.json"] = sha((temporary / "mapping.json").read_bytes())
                seal = {
                    "schemaVersion": 1,
                    "jobId": job_id,
                    "catalogId": self.binding["catalogId"],
                    "workspaceId": self.binding["workspaceId"],
                    "config": config,
                    "qualityHash": quality_hash(config),
                    "mapping": mapping,
                    "files": files,
                    "remainingUrls": urls[len(mapping) :],
                }
                atomic_json(temporary / "seal.json", seal)
                seal_hash = sha((temporary / "seal.json").read_bytes())
                temporary.rename(destination)
                state = {
                    "id": job_id,
                    "workspaceId": self.binding["workspaceId"],
                    "operationId": uuid4().hex,
                    "status": "prepared",
                    "sealHash": seal_hash,
                    "requestHash": files["requests.jsonl"],
                    "config": config,
                    "qualityHash": seal["qualityHash"],
                    "requestCount": len(mapping),
                    "reservePerItem": reserve_micros(config),
                    "reservedMicros": 0,
                    "knownMicros": 0,
                    "heldMicros": 0,
                    "usageSettled": False,
                    "inputFileId": None,
                    "batchId": None,
                    "remoteStatus": None,
                    "resultHash": None,
                    "captioned": 0,
                    "retry": 0,
                    "failuresReviewed": False,
                    "createdAt": now(),
                    "updatedAt": now(),
                }
                with self.transaction() as c:
                    for item in mapping:
                        if (
                            c.execute(
                                "SELECT 1 FROM photos WHERE url=?", (item["url"],)
                            ).fetchone()
                            is None
                        ):
                            raise ValueError(
                                "Prepared photo is absent from this catalog"
                            )
                    c.execute(
                        "INSERT INTO api_jobs VALUES(?,?)", (job_id, dumps(state))
                    )
                    c.executemany(
                        "INSERT INTO api_items(job_id,url,custom_id) VALUES(?,?,?)",
                        [(job_id, m["url"], m["custom_id"]) for m in mapping],
                    )
                return {**state, "remainingCount": len(seal["remainingUrls"])}
            except BaseException:
                if temporary.exists():
                    shutil.rmtree(temporary)
                # An unregistered complete folder is harmless and preserved for inspection.
                raise

    def _metadata(self, state):
        return {
            "photobook_operation": state["operationId"],
            "photobook_request": state["requestHash"],
            "photobook_quality": state["qualityHash"],
        }

    def _check_remote(self, state, remote):
        if (
            not isinstance(remote, dict)
            or remote.get("endpoint") != "/v1/responses"
            or remote.get("input_file_id") != state["inputFileId"]
            or any(
                (remote.get("metadata") or {}).get(k) != v
                for k, v in self._metadata(state).items()
            )
        ):
            raise ValueError(
                "Remote batch does not match the sealed operation and input file"
            )
        if not isinstance(remote.get("id"), str) or not remote["id"]:
            raise ValueError("Remote batch has no identity")

    def _upload_and_create(self, state, provider):
        if state["status"] == "reserved":
            self._update(state, status="uploading")
            try:
                file_id = provider.upload(
                    self._path(state["id"], "requests.jsonl"), state["operationId"]
                )
                if not isinstance(file_id, str) or not file_id:
                    raise ValueError("Upload response lacks file identity")
                self._update(state, status="uploaded", inputFileId=file_id)
            except Exception as error:
                self._update(
                    state, status="upload_unknown", errorType=type(error).__name__
                )
                raise
        self._update(state, status="creating")
        try:
            remote = provider.create(state["inputFileId"], self._metadata(state))
            self._check_remote(state, remote)
            self._update(
                state,
                status="submitted",
                batchId=remote["id"],
                remoteStatus=remote["status"],
            )
        except Exception as error:
            self._update(
                state, status="submission_unknown", errorType=type(error).__name__
            )
            raise
        return state

    def submit(self, job_id, provider, *, allow_upload=False, smoke=False):
        with self.lock:
            state = self.job(job_id)
            self.verify(state)
            config = state["config"]
            if allow_upload is not True or config["enabled"] is not True:
                raise ValueError(
                    "Explicit API enablement and --allow-upload are required"
                )
            if state["status"] != "prepared":
                raise ValueError(
                    "This job was already reserved or submitted; reconcile it instead of resubmitting"
                )
            with self.transaction() as c:
                all_jobs = [
                    json.loads(r[0])
                    for r in c.execute("SELECT state_json FROM api_jobs")
                ]
                if any(j["status"] in UNCERTAIN for j in all_jobs):
                    raise ValueError(
                        "An uncertain job needs reconciliation before further submissions"
                    )
                if sum(j["status"] in ACTIVE for j in all_jobs) >= config["max_active"]:
                    raise ValueError("The configured active-batch limit is reached")
                if smoke:
                    if state["requestCount"] > 5:
                        raise ValueError("A smoke batch has at most five images")
                else:
                    review = c.execute(
                        "SELECT job_id,result_hash FROM api_reviews WHERE quality_hash=?",
                        (state["qualityHash"],),
                    ).fetchone()
                    if not review:
                        raise ValueError(
                            "Review a completed smoke batch for these exact quality settings before bulk submission"
                        )
                    if any(j["retry"] and not j["failuresReviewed"] for j in all_jobs):
                        raise ValueError("Review failed photos before spending more")
                reserved = state["requestCount"] * state["reservePerItem"]
                committed = sum(j["knownMicros"] + j["heldMicros"] for j in all_jobs)
                if committed + reserved > micros(config["ceiling_usd"]):
                    raise ValueError(
                        "Estimated spending ceiling reached, including unresolved reservations"
                    )
                rows = c.execute(
                    "SELECT url FROM api_items WHERE job_id=?", (job_id,)
                ).fetchall()
                for (url,) in rows:
                    row = c.execute(
                        "SELECT status,attempts FROM photos WHERE url=?", (url,)
                    ).fetchone()
                    if (
                        not row
                        or row[0] not in {"pending", "retry", "preview_error"}
                        or c.execute(
                            "SELECT 1 FROM api_owners WHERE url=?", (url,)
                        ).fetchone()
                    ):
                        raise ValueError(
                            "A photo is already captioned or reserved by another attempt"
                        )
                    prior = [
                        json.loads(r[0])
                        for r in c.execute(
                            "SELECT j.state_json FROM api_jobs j JOIN api_items i ON i.job_id=j.id WHERE i.url=?",
                            (url,),
                        )
                    ]
                    attempts = max(
                        row[1],
                        sum(
                            j["reservedMicros"] > 0 and j["status"] != "abandoned"
                            for j in prior
                        ),
                    )
                    if attempts >= config["max_attempts"]:
                        raise ValueError("A photo reached the configured attempt limit")
                for (url,) in rows:
                    c.execute("INSERT INTO api_owners VALUES(?,?)", (url, job_id))
                    c.execute(
                        "UPDATE photos SET status='submitted',last_error=?,updated_at=? WHERE url=?",
                        ("api job " + job_id, now(), url),
                    )
                state.update(
                    status="reserved",
                    reservedMicros=reserved,
                    heldMicros=reserved,
                    smoke=bool(smoke),
                )
                self._save(c, state)
            return self._upload_and_create(state, provider)

    def resume(self, job_id, provider, *, allow_upload=False):
        with self.lock:
            state = self.job(job_id)
            self.verify(state)
            if allow_upload is not True or state["config"]["enabled"] is not True:
                raise ValueError("Explicit upload permission is required")
            if state["status"] not in {"reserved", "uploaded"}:
                raise ValueError(
                    "Only a proven pre-create interruption can resume; reconcile uncertain network operations"
                )
            return self._upload_and_create(state, provider)

    def attach_upload(self, job_id, file_id, provider):
        with self.lock:
            state = self.job(job_id)
            self.verify(state)
            if state["status"] not in {"uploading", "upload_unknown"}:
                raise ValueError("This job is not awaiting an upload identity")
            raw = provider.download(file_id, 190_000_000)
            if sha(raw) != state["requestHash"]:
                raise ValueError(
                    "Remote input file bytes do not match the sealed request"
                )
            return self._update(
                state, status="uploaded", inputFileId=file_id, reconciledAt=now()
            )

    def attach_batch(self, job_id, batch_id, provider):
        with self.lock:
            state = self.job(job_id)
            self.verify(state)
            if state["status"] not in {"creating", "submission_unknown", "submitted"}:
                raise ValueError("This job is not awaiting a batch identity")
            remote = provider.get_batch(batch_id)
            self._check_remote(state, remote)
            if remote["id"] != batch_id:
                raise ValueError("Remote batch identity mismatch")
            return self._update(
                state,
                status="submitted",
                batchId=batch_id,
                remoteStatus=remote["status"],
                reconciledAt=now(),
            )

    def _collection(self, state, provider):
        result_dir = self._path(state["id"], "results")
        if result_dir.exists():
            if result_dir.is_symlink():
                raise ValueError("Result directories cannot be symbolic links")
            receipt = json.loads(
                (result_dir / "receipt.json").read_text(encoding="utf-8")
            )
            if (
                receipt["jobId"] != state["id"]
                or receipt["batchId"] != state["batchId"]
            ):
                raise ValueError("Result artifacts belong to another attempt")
            self._check_remote(state, receipt["remote"])
            if receipt["remote"]["status"] not in TERMINAL:
                raise ValueError("Results are not from a terminal batch")
            for name, expected in receipt["files"].items():
                if name not in {"output.jsonl", "error.jsonl", "combined.jsonl"}:
                    raise ValueError("Unknown result artifact")
                path = self._path(state["id"], "results/" + name)
                if (
                    path.is_symlink()
                    or path.stat().st_size > 64_000_000
                    or sha(path.read_bytes()) != expected
                ):
                    raise ValueError("Collected result bytes changed")
            raw = (result_dir / "combined.jsonl").read_bytes()
            if state["resultHash"] and sha(raw) != state["resultHash"]:
                raise ValueError("Collected results differ from the database receipt")
            return raw, receipt["remote"]
        remote = provider.get_batch(state["batchId"])
        self._check_remote(state, remote)
        if remote["id"] != state["batchId"]:
            raise ValueError("Provider returned another batch identity")
        if remote["status"] not in TERMINAL:
            self._update(state, remoteStatus=remote["status"], checkedAt=now())
            return None, remote
        temporary = Path(tempfile.mkdtemp(prefix=".collect-", dir=result_dir.parent))
        try:
            chunks = []
            files = {}
            for kind in ["output", "error"]:
                remote_id = remote.get(kind + "_file_id")
                if remote_id:
                    raw = provider.download(remote_id, 64_000_000)
                    if not isinstance(raw, bytes) or len(raw) > 64_000_000:
                        raise ValueError("Result file exceeds the collection limit")
                    path = temporary / (kind + ".jsonl")
                    path.write_bytes(raw)
                    files[path.name] = sha(raw)
                    chunks.append(raw.rstrip() + b"\n")
            combined = b"".join(chunks)
            if len(combined) > 64_000_000:
                raise ValueError("Combined result files exceed the collection limit")
            (temporary / "combined.jsonl").write_bytes(combined)
            files["combined.jsonl"] = sha(combined)
            atomic_json(
                temporary / "receipt.json",
                {
                    "jobId": state["id"],
                    "batchId": state["batchId"],
                    "remote": remote,
                    "files": files,
                },
            )
            temporary.rename(result_dir)
            return combined, remote
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def collect(self, job_id, provider):
        with self.lock:
            state = self.job(job_id)
            seal = self.verify(state)
            if state["status"] == "collected":
                return state
            if not state["batchId"]:
                raise ValueError(
                    "This job needs submission reconciliation before collection"
                )
            try:
                raw, remote = self._collection(state, provider)
                if raw is None:
                    return state
                expected = {m["custom_id"]: m["url"] for m in seal["mapping"]}
                results = parse_results(raw, expected, state["config"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self._update(
                    state, status="review_required", errorType=type(error).__name__
                )
                raise
            with self.transaction() as c:
                current = self._job(c, job_id)
                if current["status"] == "collected":
                    return current
                for result in results:
                    owner = c.execute(
                        "SELECT job_id FROM api_owners WHERE url=?", (result["url"],)
                    ).fetchone()
                    photo = c.execute(
                        "SELECT status FROM photos WHERE url=?", (result["url"],)
                    ).fetchone()
                    result["applied"] = bool(
                        owner
                        and owner[0] == job_id
                        and photo
                        and photo[0] == "submitted"
                    )
                    if result["applied"]:
                        c.execute(
                            "UPDATE photos SET status=?,caption_json=?,attempts=attempts+1,last_error=?,updated_at=? WHERE url=?",
                            (
                                result["outcome"],
                                dumps(result["caption"]) if result["caption"] else None,
                                result["error"],
                                now(),
                                result["url"],
                            ),
                        )
                        c.execute(
                            "DELETE FROM api_owners WHERE url=? AND job_id=?",
                            (result["url"], job_id),
                        )
                    c.execute(
                        "UPDATE api_items SET result_json=? WHERE job_id=? AND url=?",
                        (dumps(result), job_id, result["url"]),
                    )
                unknown = sum(r["cost_micros"] is None for r in results)
                state.update(
                    status="collected",
                    remoteStatus=remote["status"],
                    resultHash=sha(raw),
                    captioned=sum(r["outcome"] == "captioned" for r in results),
                    retry=sum(r["outcome"] == "retry" for r in results),
                    knownMicros=sum(r["cost_micros"] or 0 for r in results),
                    heldMicros=unknown * state["reservePerItem"],
                    usageSettled=unknown == 0,
                    unknownUsageCount=unknown,
                    collectedAt=now(),
                )
                self._save(c, state)
            return state

    def results(self, job_id):
        self.job(job_id)
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as c:
            return [
                json.loads(row[0])
                for row in c.execute(
                    "SELECT result_json FROM api_items WHERE job_id=? AND result_json IS NOT NULL ORDER BY custom_id",
                    (job_id,),
                )
            ]

    def resolve_failed_results(self, job_id, provider, reason):
        """Preserve unusable terminal artifacts, release photos, retain the full cost hold."""
        if not isinstance(reason, str) or not 10 <= len(reason) <= 2000:
            raise ValueError("Record why these terminal results cannot be used")
        with self.lock:
            state = self.job(job_id)
            seal = self.verify(state)
            if state["status"] != "review_required" or not state["batchId"]:
                raise ValueError(
                    "Only an attached batch with unusable results can be resolved"
                )
            remote = provider.get_batch(state["batchId"])
            self._check_remote(state, remote)
            if remote["id"] != state["batchId"] or remote["status"] not in TERMINAL:
                raise ValueError(
                    "Verify the provider batch is terminal before releasing its photos"
                )
            with self.transaction() as c:
                for item in seal["mapping"]:
                    owner = c.execute(
                        "SELECT job_id FROM api_owners WHERE url=?", (item["url"],)
                    ).fetchone()
                    applied = bool(owner and owner[0] == job_id)
                    if applied:
                        c.execute(
                            "UPDATE photos SET status='retry',attempts=attempts+1,last_error='Unusable terminal API results; manual review required',updated_at=? WHERE url=? AND status='submitted'",
                            (now(), item["url"]),
                        )
                        c.execute(
                            "DELETE FROM api_owners WHERE url=? AND job_id=?",
                            (item["url"], job_id),
                        )
                    result = {
                        "custom_id": item["custom_id"],
                        "url": item["url"],
                        "outcome": "retry",
                        "caption": None,
                        "error": "Terminal results rejected by operator: " + reason,
                        "usage": None,
                        "cost_micros": None,
                        "applied": applied,
                    }
                    c.execute(
                        "UPDATE api_items SET result_json=? WHERE job_id=? AND url=?",
                        (dumps(result), job_id, item["url"]),
                    )
                state.update(
                    status="collected",
                    remoteStatus=remote["status"],
                    captioned=0,
                    retry=state["requestCount"],
                    heldMicros=state["reservedMicros"],
                    usageSettled=False,
                    unknownUsageCount=state["requestCount"],
                    collectedAt=now(),
                    resultResolution={
                        "at": now(),
                        "reason": reason,
                        "artifactsPreserved": True,
                    },
                )
                self._save(c, state)
            return state

    def review_smoke(self, job_id, reviewed_ids):
        with self.lock:
            state = self.job(job_id)
            self.verify(state)
            if (
                state["status"] != "collected"
                or not state.get("smoke")
                or state["retry"]
                or not state["usageSettled"]
            ):
                raise ValueError(
                    "Review a complete, successful smoke batch with settled usage"
                )
            rows = self.results(job_id)
            expected = {r["custom_id"] for r in rows}
            if (
                not isinstance(reviewed_ids, list)
                or len(reviewed_ids) != len(set(reviewed_ids))
                or set(reviewed_ids) != expected
                or len(expected) != state["requestCount"]
            ):
                raise ValueError(
                    "Confirm every smoke image and structured caption was reviewed"
                )
            raw, _ = self._collection(state, None)
            if sha(raw) != state["resultHash"]:
                raise ValueError("Smoke results changed")
            with self.transaction() as c:
                c.execute(
                    "INSERT OR REPLACE INTO api_reviews VALUES(?,?,?,?)",
                    (state["qualityHash"], job_id, state["resultHash"], now()),
                )
            return {
                "reviewed": True,
                "jobId": job_id,
                "qualityHash": state["qualityHash"],
                "count": len(expected),
            }

    def review_failures(self, job_id, reviewed_ids):
        with self.lock:
            state = self.job(job_id)
            if state["status"] != "collected":
                raise ValueError("Collect the batch before reviewing failures")
            failed = {
                r["custom_id"] for r in self.results(job_id) if r["outcome"] == "retry"
            }
            if (
                not isinstance(reviewed_ids, list)
                or len(reviewed_ids) != len(set(reviewed_ids))
                or set(reviewed_ids) != failed
            ):
                raise ValueError(
                    "Review every failed result explicitly before continuing"
                )
            return self._update(state, failuresReviewed=True, failuresReviewedAt=now())

    def settle_usage(self, job_id, actual_usd, reason):
        if not isinstance(reason, str) or not 10 <= len(reason) <= 2000:
            raise ValueError("Record how you verified the provider usage or bill")
        amount = micros(actual_usd)
        with self.lock:
            state = self.job(job_id)
            if state["status"] != "collected":
                raise ValueError(
                    "Usage can be settled after terminal results are collected"
                )
            history = state.get("manualSettlements", [])
            history.append(
                {
                    "at": now(),
                    "previousKnownMicros": state["knownMicros"],
                    "previousHeldMicros": state["heldMicros"],
                    "settledMicros": amount,
                    "reason": reason,
                }
            )
            return self._update(
                state,
                knownMicros=amount,
                heldMicros=0,
                usageSettled=True,
                manualSettlements=history,
            )

    def abandon_before_create(self, job_id):
        with self.lock:
            state = self.job(job_id)
            if (
                state["status"]
                not in {
                    "prepared",
                    "reserved",
                    "uploading",
                    "upload_unknown",
                    "uploaded",
                }
                or state["batchId"]
            ):
                raise ValueError(
                    "A create may have reached the provider. Reconcile that batch; do not release its reservation blindly."
                )
            with self.transaction() as c:
                for (url,) in c.execute(
                    "SELECT url FROM api_owners WHERE job_id=?", (job_id,)
                ).fetchall():
                    c.execute(
                        "UPDATE photos SET status='retry',last_error='API preparation abandoned before batch creation',updated_at=? WHERE url=? AND status='submitted'",
                        (now(), url),
                    )
                c.execute("DELETE FROM api_owners WHERE job_id=?", (job_id,))
                state.update(
                    status="abandoned",
                    heldMicros=0,
                    usageSettled=True,
                    abandonedAt=now(),
                )
                self._save(c, state)
            return state

    def reconciliation_candidates(self, job_id, provider):
        state = self.job(job_id)
        self.verify(state)
        return provider.find_operation(state["operationId"])

    def status(self):
        self.guard()
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as c:
            jobs = [
                json.loads(row[0])
                for row in c.execute("SELECT state_json FROM api_jobs ORDER BY id")
            ]
            photos = dict(
                c.execute("SELECT status,COUNT(*) FROM photos GROUP BY status")
            )
        return {
            "photos": photos,
            "estimatedSpentMicros": sum(j["knownMicros"] for j in jobs),
            "reservedMicros": sum(j["heldMicros"] for j in jobs),
            "activeJobs": sum(j["status"] in ACTIVE for j in jobs),
            "jobs": [
                {
                    k: j[k]
                    for k in [
                        "id",
                        "workspaceId",
                        "status",
                        "requestCount",
                        "captioned",
                        "retry",
                        "knownMicros",
                        "heldMicros",
                        "usageSettled",
                        "inputFileId",
                        "batchId",
                    ]
                }
                for j in jobs
            ],
            "note": "Estimates and reservations are local accounting, not a provider-enforced billing cap.",
        }

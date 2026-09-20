"""Explicit optional API commands, with no client for purely local operations."""

from pathlib import Path


def add_parsers(sub):
    api = sub.add_parser(
        "api", help="Optional paid caption batches; never used by the offline demo."
    ).add_subparsers(dest="action", required=True)
    for name in [
        "init",
        "status",
        "prepare",
        "submit",
        "resume",
        "collect",
        "watch",
        "run",
        "results",
        "find",
        "attach-upload",
        "attach-batch",
        "review-smoke",
        "review-failures",
        "settle",
        "resolve-results",
        "abandon",
    ]:
        c = api.add_parser(name)
        c.add_argument("--db", type=Path, required=True)
        c.add_argument("--workspace", type=Path, required=True)
        if name in {"prepare", "run"}:
            c.add_argument("--config", type=Path, required=True)
        if name == "prepare":
            c.add_argument("--manifest", type=Path, required=True)
        if name not in {"init", "status", "prepare", "collect", "watch", "run"}:
            c.add_argument("--job", required=True)
        if name == "collect":
            c.add_argument(
                "--job", help="Omit to collect all submitted jobs in this workspace."
            )
        if name in {"submit", "resume", "run"}:
            c.add_argument("--allow-upload", action="store_true")
        if name == "submit":
            c.add_argument("--smoke", action="store_true")
        if name in {"watch", "run"}:
            c.add_argument(
                "--max-cycles", type=int, default=1 if name == "run" else 1440
            )
        if name == "run":
            c.add_argument("--max-jobs", type=int, default=10)
        if name == "watch":
            c.add_argument("--poll-seconds", type=int, default=60)
        if name in {"review-smoke", "review-failures"}:
            c.add_argument(
                "--reviewed-id",
                action="append",
                default=[],
                help="Repeat for each image and structured result you inspected.",
            )
        if name in {"settle", "resolve-results"}:
            c.add_argument("--reason", required=True)
        if name == "settle":
            c.add_argument("--actual-usd", required=True)
        if name == "attach-upload":
            c.add_argument("--file-id", required=True)
        if name == "attach-batch":
            c.add_argument("--batch-id", required=True)


def execute_api(args):
    from .api_workflow import initialize, BatchWorkspace
    from .cli import json_file

    if args.action == "init":
        return initialize(args.db, args.workspace)
    ws = BatchWorkspace(args.db, args.workspace)
    action = args.action
    if action == "status":
        return ws.status()
    if action == "prepare":
        return ws.prepare(json_file(args.manifest), json_file(args.config))
    if action == "results":
        return ws.results(args.job)
    if action == "review-smoke":
        return ws.review_smoke(args.job, args.reviewed_id)
    if action == "review-failures":
        return ws.review_failures(args.job, args.reviewed_id)
    if action == "settle":
        return ws.settle_usage(args.job, args.actual_usd, args.reason)
    if action == "abandon":
        return ws.abandon_before_create(args.job)
    if action in {"submit", "resume", "run"} and not args.allow_upload:
        raise ValueError(
            "Review the sealed request and selected metadata first, then explicitly pass --allow-upload"
        )
    if action in {"submit", "resume"} and not ws.job(args.job)["config"]["enabled"]:
        raise ValueError("This sealed job has API access disabled")
    if action == "run":
        from .api_batch import validate_config

        config = validate_config(json_file(args.config))
        if not config["enabled"]:
            raise ValueError("This API configuration is disabled")
    from .api_provider import OpenAIBatchProvider
    from .api_runner import run, watch, collect_all

    provider = None
    try:
        provider = OpenAIBatchProvider()
        if action == "submit":
            return ws.submit(args.job, provider, allow_upload=True, smoke=args.smoke)
        if action == "resume":
            return ws.resume(args.job, provider, allow_upload=True)
        if action == "collect":
            return (
                ws.collect(args.job, provider)
                if args.job
                else collect_all(ws, provider)
            )
        if action == "watch":
            return watch(
                ws, provider, poll_seconds=args.poll_seconds, max_cycles=args.max_cycles
            )
        if action == "run":
            return run(
                ws,
                provider,
                config,
                allow_upload=True,
                max_cycles=args.max_cycles,
                max_jobs=args.max_jobs,
            )
        if action == "find":
            return ws.reconciliation_candidates(args.job, provider)
        if action == "attach-upload":
            return ws.attach_upload(args.job, args.file_id, provider)
        if action == "attach-batch":
            return ws.attach_batch(args.job, args.batch_id, provider)
        if action == "resolve-results":
            return ws.resolve_failed_results(args.job, provider, args.reason)
        raise ValueError("Unknown API command")
    except Exception as error:
        # Provider exceptions can contain request or response content. Keep that local.
        if error.__class__.__module__.startswith(("openai", "httpx")):
            raise RuntimeError(
                "Provider request failed ("
                + type(error).__name__
                + "). Inspect api status. Reconcile uncertain uploads/submissions before retrying."
            ) from None
        raise
    finally:
        if provider is not None:
            provider.client.close()

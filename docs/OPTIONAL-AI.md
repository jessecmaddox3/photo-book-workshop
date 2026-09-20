# Optional captions with AI

> **Quick take:** the demo, catalog, layout, review and proof tools need no AI account. These optional commands send selected images to a service. Start with a few images, inspect every result, and keep working files private.

## Choose a route deliberately

Manual captions use `catalog ingest` and never start a model. `catalog prompt` writes a local prompt for you to inspect; copying it or attaching images to another service is your decision. That manual prompt includes catalog identities, so inspect it before sharing.

The Codex route uses a separately installed and configured Codex CLI. It needs an explicit model and `--allow-upload`, and sends normalized copies with pseudonymous join keys rather than source URLs or original filenames. The prompt contains only context already selected into the manifest. Your normal Codex account, configuration and service terms still apply. The adapter does not bypass approval settings or install/login to Codex for you.

```sh
photo-book catalog codex --db ../my-book/catalog.sqlite3 --manifest PATH-TO-MANIFEST.json --model YOUR-MODEL --allow-upload
```

Each attempt has a separate result/log folder and a ten-minute timeout. There is no automatic retry. After a timeout the service may already have processed the images; inspect the preserved attempt before starting another. If catalog state changes while the model works, the old reply is preserved but not imported over the newer caption. Public issues should never contain these logs or real captions.

## Paid OpenAI Batch route

Install the optional SDK with `python -m pip install '.[api]'` using the workshop environment’s Python. Supply `OPENAI_API_KEY` through your terminal’s environment or your own secret manager. The project never searches local credential files, automatically loads `.env`, or needs your key for ordinary commands.

Copy [api-config.example.json](../examples/api-config.example.json) to your private workspace. It is deliberately disabled and invalid until you fill in real settings. Choose a model that supports images, structured JSON output and the Responses endpoint in Batch. Verify its current supported detail/reasoning settings and **Batch** input, cached-input and output prices. Prices are USD per million tokens. Record their source URL and verification date; set `enabled` to true only when ready to prepare an uploadable job.

See the official [Batch guide](https://developers.openai.com/api/docs/guides/batch), [Batch API reference](https://developers.openai.com/api/reference/resources/batches), and [pricing page](https://openai.com/api/pricing/). This adapter uses `/v1/responses`, files uploaded for `batch`, a 24-hour completion window and at most 190 MB of request data per local job. Support and pricing can change; no model or rate is silently chosen for you.

The uploaded request contains a normalized JPEG preview, the generic caption prompt, a pseudonymous image key and **only** the metadata fields explicitly listed in `metadata_fields`. Those can include album/title/date, coordinates or people tags. The default list is empty. Tags are context, not evidence identifying a face. Images themselves can reveal personal information. `store: false` is part of the Responses request; it is not a promise about Batch file deletion or the provider’s complete retention policy. Provider files are not automatically deleted by this tool.

## Prepare a small trial locally

First scan a catalog, then prepare up to five previews. Keep the `api` workspace separate from the database file and start with an empty folder:

```sh
photo-book api init --db ../my-book/catalog.sqlite3 --workspace ../my-book-api
photo-book catalog prepare --db ../my-book/catalog.sqlite3 --work ../my-book/previews --limit 5
photo-book api prepare --db ../my-book/catalog.sqlite3 --workspace ../my-book-api --config ../my-book/api-config.json --manifest PATH-TO-MANIFEST.json
```

These commands do not contact OpenAI. The last prints a local `job-...` ID. Its directory contains the exact sealed request JSONL, normalized images, local identity mapping and policy. Inspect them before upload. The mapping contains private catalog identities; it is not uploaded. If preparation reaches the configured request-size/count bound, `remainingCount` tells you how many manifest items were not included. A later preparation can select those still-pending photos.

```sh
photo-book api submit --db ../my-book/catalog.sqlite3 --workspace ../my-book-api --job JOB-ID --smoke --allow-upload
photo-book api watch --db ../my-book/catalog.sqlite3 --workspace ../my-book-api --max-cycles 1440
photo-book api results --db ../my-book/catalog.sqlite3 --workspace ../my-book-api --job JOB-ID
```

`watch` polls and collects only. It creates no new work and can be interrupted with Ctrl+C. Inspect each trial image beside its complete structured caption, including quality, visible text, crop advice and non-photo classification. Record approval using each result’s `custom_id`:

```sh
photo-book api review-smoke --db ../my-book/catalog.sqlite3 --workspace ../my-book-api --job JOB-ID --reviewed-id PHOTO-ID-1 --reviewed-id PHOTO-ID-2
```

Repeat `--reviewed-id` for every trial item. A successful, settled trial is required. This is a human review record, not an automatic quality score. Changing model, prompt/schema, image processing or quality settings requires a new trial.

## Run more work within limits

```sh
photo-book api run --db ../my-book/catalog.sqlite3 --workspace ../my-book-api --config ../my-book/api-config.json --allow-upload --max-jobs 10 --max-cycles 1
photo-book api status --db ../my-book/catalog.sqlite3 --workspace ../my-book-api
photo-book api collect --db ../my-book/catalog.sqlite3 --workspace ../my-book-api
```

`run` collects existing submitted jobs first, then fills available slots with eligible photos. The default single cycle submits available work and returns. Increase `--max-cycles` for a bounded polling run. Active-job, per-photo attempt and new-job limits still apply. It stops for unresolved submissions, failed-result review, exhausted estimates or a new quality trial. `needs_attention` means photos remain but require intervention, such as restoring missing originals, retrying preview preparation deliberately or reviewing an exhausted attempt limit. It does not mean the entire catalog is captioned.

The estimate holds `input_allowance × input rate + max_output_tokens × output rate` per image before any upload. Pick a conservative input allowance for your image size/detail, model and context. Known usage replaces estimates after collection. Missing or malformed usage retains the original hold; changing a later configuration cannot shrink an existing reservation.

**This is local accounting, not a provider-enforced cap.** Real input usage or changed rates can exceed the allowance. Configure account/project billing controls separately. Actual charges, taxes and account adjustments can differ from these token estimates.

## Recover without duplicate submissions

Every chargeable transition is recorded before its request. The SDK has automatic retries disabled, including injected clients. A lost reply is uncertain, not a failed request that can safely be sent again.

| Status or event | Next action |
| --- | --- |
| `prepared` | Inspect, submit once, or `abandon`. No reservation or upload yet. |
| `reserved` or `uploaded` | A known pre-create interruption. Use `resume --job JOB-ID --allow-upload`. |
| `uploading` / `upload_unknown` | Use `find --job JOB-ID`, then `attach-upload --job JOB-ID --file-id FILE-ID`. Downloaded input must exactly match the sealed request. Resume afterward. |
| `creating` / `submission_unknown` | Use `find`, then `attach-batch --job JOB-ID --batch-id BATCH-ID`. Endpoint, input file and operation metadata must match. Never resubmit because a listing is empty. |
| `submitted` | `collect --job JOB-ID` or `watch`. A polling failure preserves the submitted job. |
| `review_required` | Inspect preserved terminal artifacts. If unusable, use `resolve-results --job JOB-ID --reason "..."`; it verifies the remote batch is terminal, marks the images retryable and retains the full cost hold. |
| Failed individual results | Inspect `results`, then `review-failures --job JOB-ID --reviewed-id PHOTO-ID`, repeated for every failed image. Failures can still have billed tokens. |
| Unknown usage | Verify the provider’s usage/bill, then `settle --job JOB-ID --actual-usd AMOUNT --reason "How I verified it"`. This records an audited local adjustment. |

Every table command also takes `photo-book api`, `--db` and `--workspace` as in the examples above. `find` searches bounded provider listings and returns candidates, not permission to start another job. `abandon` is allowed only when creation could not have started; uploaded provider files may remain. There is no blind “reset everything” command.

Back up the catalog database and its matching workspace together while no runner is active. The binding rejects moved/mismatched workspaces, and the request seal rejects changed artifacts. Preserve an old workspace for recovery instead of hand-editing identities or deleting reservations. Collecting an old attempt cannot overwrite a newer attempt’s ownership, and repeat collection cannot count the same usage twice.

Provider tests use fake clients and the actual SDK over a synthetic HTTP transport. No live-model accuracy, live service compatibility for every model, or real billing outcome is claimed.

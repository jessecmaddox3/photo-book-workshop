# Contributing

> **Quick take:** useful improvements are welcome. Reproduce problems with invented images and data, run the relevant tests, and explain the observable change.

This began as a personal-use toolkit. There is no guaranteed support schedule. Small focused pull requests are easiest to review. Good starting points include accessible review controls, more decoder fixtures, better page-plan authoring, font coverage and printer-specific export adapters.

Use Python 3.12+ and Node 22+ for the JavaScript queue tests. With [uv](https://docs.astral.sh/uv/), run:

```sh
uv sync --extra dev --extra api --extra browser
uv run pytest tests -q
node --test tests/save-queue.test.mjs
uv run python -m build
```

Or create a venv and install `.[dev,api,browser]` with pip. The normal Python suite blocks external sockets. API tests use invented responses and the actual SDK over an in-memory HTTP transport, never a real key. Optional decoder tests should remain synthetic and skip cleanly when a decoder is absent.

For the browser workflow, install Playwright Chromium and run:

```sh
uv run playwright install chromium
uv run python scripts/browser-check.py --output ../photo-book-browser-check
```

The check creates a temporary local server, exercises saved choices, conflicts/recovery and proof generation, checks responsive widths and blocks outside browser requests. Choose a new output folder. Inspect generated captures after changing geometry or typography, not only test exit codes.

Keep the source release allowlist current in `release-files.txt`. `scripts/package-release.py` builds a source ZIP only from that explicit list and verifies asset provenance hashes. Build/install the wheel outside the source checkout to catch missing package assets. GitHub CI also runs the downloaded-folder launcher on Windows, macOS and Linux.

Preserve full selected-image inclusion, immutable originals, explicit caption selection, lossless pending-draft recovery and attempt-bound billing/ownership. Changes to those boundaries need meaningful regression tests. Do not submit real catalogs, media, people tags, credentials, logs or generated private books. Sanitizing only a filename is not sufficient.

Submit contributions under the project's MIT license. Preserve font and other third-party notices. For a vulnerability, use [private reporting](SECURITY.md).

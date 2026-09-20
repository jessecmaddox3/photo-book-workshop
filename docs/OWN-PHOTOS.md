# Make the workshop yours

> **Quick take:** keep a separate private working folder. Gather and choose images, normalize selected originals, describe your pages in `book.json`, then review captions and build proofs. The example is a complete working reference.

This guide uses `photo-book` after installation. With the starter’s environment, use `.venv/bin/photo-book` on Mac/Linux or `.venv\Scripts\photo-book` on Windows. Run commands from the downloaded project folder. Quoted paths can contain spaces.

## Keep your working material separate

Create a folder outside the downloaded source, such as `../my-book`. Keep original media and backups elsewhere too. The application never searches your Downloads, home folder or cloud account for images. You select every input explicitly.

The catalog can contain source URLs, album names, people tags, dates, coordinates and original archive paths. Candidate pools, contact sheets, notes and proofs can also be personal. A normalized image loses embedded metadata, but the picture itself can still identify people or places. Review what is visible before sharing.

## Option A: a few pictures already on your computer

You do not need a catalog. Normalize each chosen original into a new book assets folder:

```sh
photo-book normalize --source "../originals/garden.jpg" --output "../my-book/assets" --id garden
```

The command prints an asset record with its dimensions, relative derivative filename and hashes. Put that record into your page plan’s `assets` list, prefixing its `path` with `assets/` because the book plan will live one folder above the image. Retain the source original. Repeat with stable IDs for the remaining images.

## Option B: a larger Google Photos Takeout collection

Download your Google Photos export yourself. Keep all ZIP parts and index them together. Do not extract personal archives into the source checkout.

```sh
photo-book catalog scan --db ../my-book/catalog.sqlite3 --zip ../originals/part-1.zip ../originals/part-2.zip
photo-book catalog status --db ../my-book/catalog.sqlite3
photo-book catalog prepare --db ../my-book/catalog.sqlite3 --work ../my-book/previews --limit 5
```

The scanner joins sidecar metadata to available originals across parts and preserves multiple album memberships and alternate source files. Some exports differ; missing or undecodable media appears in status instead of being treated as a successfully captioned image.

Preparation makes local normalized previews and prints the exact `manifest.json` path. It uploads nothing. Metadata context is empty by default; `--metadata album` or another supported field deliberately includes that field in the manifest. The private catalog itself still retains indexed metadata.

You can rank photos from dates, tags and albums without AI. Rich captions make content search and quality ranking more useful. For manual captions, inspect the [caption schema](../src/photobook_workshop/caption-schema.json), create a response shaped like the example’s `captions.json`, and import it:

```sh
photo-book catalog ingest --db ../my-book/catalog.sqlite3 --manifest PATH-TO-MANIFEST.json --response ../my-book/captions.json
```

The full schema checks quality fields and identities. Missing results stay retryable. [Optional AI](OPTIONAL-AI.md) is a separate, explicit choice.

## Search, inspect and pick

Copy [query.example.json](../examples/query.example.json) to your private folder and edit its literal search rules. Empty rules include everything eligible. `includeNonPhotos` defaults false; set it true for illustrations such as the example. Dates use an inclusive `since` and exclusive `before`.

```sh
photo-book query --db ../my-book/catalog.sqlite3 --query ../my-book/query.json --output ../my-book/candidates.json
photo-book contact-sheets --db ../my-book/catalog.sqlite3 --pool ../my-book/candidates.json --output ../my-book/contacts
```

Open the numbered contact sheets. Create `selection.json` with the exact `poolHash` from the candidates file and your chosen one-based positions:

```json
{"poolHash": "COPY-THE-EXACT-HASH-HERE", "positions": [1, 3, 5]}
```

```sh
photo-book select --db ../my-book/catalog.sqlite3 --pool ../my-book/candidates.json --selection ../my-book/selection.json --output ../my-book/assets
```

The output includes normalized image files and a selection manifest. Copy its asset records into your book plan, prefixing each image path with `assets/`. The saved book uses stable IDs, so a later contact-sheet reorder cannot silently change the photos.

## Describe the book

Use the generated example’s `book/book.json` as your starting structure. Replace all example assets and pages with your own records. Give the new book its own `bookId`; keep page and option IDs stable while reviewing.

Each page has a subject, caption, layout kind and explicit images. An image placement names an asset. `mosaic` searches arrangements; `rows` preserves your authored rows; `cover` uses one full-bleed image; `text` is for an introduction or dedication. Caption options are editable plain text. [The reference](REFERENCE.md#book-format) describes the units and available fields.

If you use an AI assistant, give it the included skill and your intended book outline. It can help write the page plan and propose caption alternatives. Ask it to use only facts you supply, retain your originals, and build a proof for review before changing your established plan. It does not need to upload images to help with local layout code.

## Review and rebuild

```sh
photo-book build --book ../my-book/book.json --output ../my-book/proof-001
photo-book review --book ../my-book/book.json --workspace ../my-book-review
```

The review workspace must be separate from the book folder, with neither nested inside the other. After choosing captions, use **Make proof** in the app. Alternatively, download confirmed choices and run:

```sh
photo-book build --book ../my-book/book.json --choices ../my-book-choices.json --output ../my-book/proof-002
```

Notes never replace caption text. Apply intended wording changes to the page plan deliberately, then use a fresh review workspace if the book/options identity changed. Inspect the PDF, every browser page, crop and resolution report before sharing or printing.

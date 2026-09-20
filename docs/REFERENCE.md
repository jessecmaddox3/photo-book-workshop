# Commands and book format

> **Quick take:** every input and output location is explicit. `--help` is local and side-effect free. Work in a private folder outside the source checkout; build into a new output folder each time.

## Command map

Run `photo-book COMMAND --help` for exact required arguments. The [own-photos guide](OWN-PHOTOS.md) provides a complete sequence.

| Command | Purpose |
| --- | --- |
| `demo --directory DIR` | Create a fresh invented catalog, contacts, normalized book and proof. |
| `start --directory DIR` | Create or reopen that invented example and launch its review app. |
| `normalize --source FILE --output DIR --id ID` | Preserve the original and create a metadata-free JPEG derivative and asset record. |
| `catalog scan --db DB --zip PART...` | Scan explicitly chosen Takeout ZIPs; preserve source alternatives and albums. |
| `catalog status --db DB` | Show counts by caption/media status. |
| `catalog export --db DB --output FILE` | Write private catalog JSONL, including metadata. Never publish it by default. |
| `catalog prepare --db DB --work DIR` | Make a local preview manifest. `--limit` is 1–500; `--metadata` deliberately adds selected context. |
| `catalog prompt`, `catalog ingest`, `catalog codex` | Write a prompt, import schema-checked manual results or explicitly invoke Codex. |
| `query --db DB --query FILE --output FILE` | Apply literal filters and quality ranking, saving a stable candidate pool. |
| `contact-sheets --db DB --pool FILE --output DIR` | Make numbered, uncropped candidate sheets and indexes. |
| `select --db DB --pool FILE --selection FILE --output DIR` | Bind picks to the pool hash, resolve originals and normalize chosen assets. |
| `build --book FILE --output DIR [--choices FILE]` | Create a complete new PDF/HTML/paper proof folder. Pending draft exports are rejected. |
| `review --book FILE --workspace DIR` | Open a separate local review workspace. `--port 0` reuses its saved port, choosing a free one only on first use; `--no-browser` prints the URL. |
| `capture --proof DIR --output DIR --dpi 180` | Capture every built browser page at explicit resolution. Requires the browser extra and Playwright Chromium. |
| `api ...` | Explicit optional paid batch lifecycle. See [the AI guide](OPTIONAL-AI.md). |

For captures, install `.[browser]`, then run `python -m playwright install chromium` using the workshop environment. On Linux, browser system dependencies may also be needed. Captures wait for local fonts and images, reject overflow and outside requests, and preserve previous output folders.

## Book format

The generated demo’s `book/book.json` is the easiest editable reference. The [bundled template](../src/photobook_workshop/demo/book.json) has the same structure, with paths relative to its own folder. Save JSON as UTF-8.

| Top-level field | Meaning |
| --- | --- |
| `schemaVersion` | `1`. |
| `bookId`, `title` | Stable project identity and human-readable title. |
| `physical` | Trim `width`/`height`, `bleed` and `safe` margins in **inches**; embedded proof target `dpi`. |
| `assets` | Image records with unique `id`, confined relative `path`, verified `sha256`, dimensions and optional `alt`. Normalization also records `source_sha256`. |
| `pages` | Ordered list of 1–256 page definitions. |
| `covers` | Up to 32 additional cover ideas, rendered as separate HTML pages for comparison. They are not automatically added to the book PDF. |

IDs use letters, digits, hyphens and underscores, starting with a letter or digit. Do not use people’s names as an ID if you intend to share a plan. Asset paths must stay inside the book folder; absolute and escaping paths are rejected. An image’s actual dimensions and hash are checked when loading.

Page geometry uses **points** (72 points per inch) with a top-left origin. The physical canvas is trim plus twice the bleed. The ordinary safe inset is bleed plus the chosen safe margin. DPI affects proof pixels, not page dimensions.

| Page field | Meaning |
| --- | --- |
| `id`, `subject`, `caption` | Stable page identity, heading and original wording. |
| `kind` | `mosaic`, `rows`, `cover` or `text`. |
| `images` | Up to 24 placements with unique per-page `id`, `asset` and optional `focus: [x,y]`. Focus fractions run 0–1 from top-left to bottom-right. |
| `options` | Up to 12 `{id,text}` caption alternatives. Zero options is allowed; the original caption remains. |
| `cropLimit` | Maximum removed fraction, 0–0.95, default 0.18. Failure asks for a deliberate layout/grouping change. |
| `gutter` | Spacing in **inches**, default 0.08. Authored rows use half on each side of a cell. |
| `fontSize`, `titleSize` | Caption and heading sizes in points, default 22 and 42. |
| `band`, `bandHeight` | `top`, `bottom` or `overlay`; optional band height in points. Default height is 23% of the page. |
| `rows` | For authored rows: each has `height` in points, placement `images` and optional explicit `y` in points. Every placement must occur exactly once. |
| `gridY` | Initial row offset in points when a row has no explicit `y`. |
| `pattern` | `plain` or `dots`. |
| `scrim` | Cover gradient direction: `top`, `bottom` or `none`. |
| `textRect` | Explicit heading/caption region `[x,y,width,height]` in points. |
| `textBlocks` | Additional `{id,text,rect,size,font,align,ink,panel}` panels. Fonts: `Fredoka`/`Baloo2`; alignment: `left`/`center`; ink: `dark`/`white`; panel: `white`/`none`. |
| `exclusions` | Explicit `{asset,reason}` decisions retained in the layout report. |

A placeholder placement uses `placeholder: {width,height,label}` instead of `asset`. It appears in the layout and ledger without pretending to have photographic resolution. Text pages cannot silently hide selected images; they must have an empty image list. Cover pages use exactly one image.

Mosaics search exact recursive slicing arrangements through six photos and use a bounded deterministic search for 7–24. Larger groups need more pages. Authored rows preserve the supplied height, placement order and offsets; cumulative edge rounding closes the row without seams. Neither method silently drops photos to make the page fit.

## Output files

The complete proof folder contains `book-proof.pdf`, `html/`, `checklist.pdf`, `captions.pdf`, `picksheet.pdf`, and `proof-ledger.json`. The ledger records page identities, image placement/crops, text measurement, source and embedded-proof resolution, cover variants and paper pagination. Read its actual keys rather than assuming PDF page numbers match page-list offsets.

`source_ppi` describes the original normalized image after cropping. Embedded proof pixels can be fewer because the PDF downsamples to its target DPI. A high source PPI is not a claim that the embedded proof has that same resolution. Browser captures have their own configured pixel size.

HTML proofs are local pages with bundled fonts and selected image copies. Sharing them also shares those images and all visible wording. The entire private catalog is not needed to view a proof.

## Review and recovery

Review state is bound to the book and exact caption options. The server stores per-page revisions and durable operation receipts under a process lock. It acknowledges a save only after writing it atomically. The browser keeps pending drafts separate from in-flight saves and retries the same operation ID after a lost reply.

If a save conflicts, choose explicitly between the pending and saved versions. A focused text box keeps the revision of the words actually displayed, so a background poll cannot silently authorize overwriting another tab’s changes. If initial state cannot load, editing stays disabled and pending drafts remain exportable.

The browser can recover interrupted tab journals. The server saves and reuses the workspace port because browser storage belongs to the exact address. It refuses to silently fall back to another port when that address is busy. An explicit `--port` change, another browser profile, or switching between `localhost` and `127.0.0.1` changes where browser drafts live; export pending work before doing that. If tab identity or local storage is blocked, read the visible warning and export pending work. Browser drafts are not a backup of confirmed server state. Back up the separate review folder while stopped. Never “fix” corrupt state by deleting its binding or choices file; preserve it for recovery, and start a genuinely new workspace if needed.

Proof files persist under the review folder’s `proofs/` directory. The app lists builds created in the current server session; after a restart, older complete builds remain on disk and can be opened from that folder. Caption review is intended for one local operator, including multiple tabs. It is not an authenticated shared internet service.

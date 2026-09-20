# How the workshop fits together

> **Quick take:** private originals feed an explicit page plan. Shared geometry drives two renderers. Caption decisions and optional AI attempts have their own durable state so they can be reviewed and recovered independently.

## Keep decisions visible

The project grew from making personal books, where selecting six good images, choosing one sentence and checking a crop are separate decisions. The public extraction retains the catalog, selection tools, page builders, cover variants, browser review and paper proofs, while replacing all original content with independent examples.

The pipeline is: explicit archives or local images → private catalog → candidate pool → numbered contact sheets → selected normalized assets → book plan → proofs → caption choices → new proof. Each step is usable independently. Nothing in the renderer starts an AI job or searches a personal folder.

Source URLs join Takeout records privately. Public-facing book assets use stable IDs and hashes. A placement ID distinguishes reusing a photo on different pages from accidentally repeating it within a page. A pool hash binds numbered picks to the exact ordering the person saw.

Normalization bakes in EXIF orientation, composites transparency onto white and writes a full-resolution JPEG derivative. It leaves source bytes unchanged. A content-derived filename and exact derivative-byte verification prevent an old cached picture from masquerading as a new one.

## Two layout tools

Recursive mosaics combine images horizontally or vertically, score crop severity, cell size and area balance, then choose an acceptable arrangement. The exact search grows rapidly: six photos already produce 30,240 top-level trees. Above six, a deterministic bounded search keeps a spread of shapes and retains every image. It can fail when no examined layout meets the crop ceiling; it does not silently raise that ceiling.

Authored rows solve a different problem: preserve a deliberate composition. Row height, ordering and offsets remain explicit. Widths follow image aspect ratios, with rounded cumulative edges and an exact last edge. Crop calculations use the image interior after borders, not the surrounding cell. Additional panels and overlays are typed fields, not executable CSS strings.

Both methods produce the same point-based, top-left geometry. PDF converts that geometry once into ReportLab coordinates; HTML uses it directly. Fonts are local. Text is measured, wrapped and checked. Long paper checklists paginate with repeated headings and complete wording instead of shrinking or dropping text to meet an arbitrary page count.

## Review without losing words

Previewing a caption and selecting it are distinct actions. Notes are review instructions, never automatically final prose. A confirmed choice is valid only for the original book/options identity.

Each save carries a UUID and the page revision the editor actually saw. The store locks, validates and atomically writes state plus the receipt before replying. A repeated UUID returns the original accepted revision. A competing edit becomes a visible conflict. Browser storage is a recovery journal for unsent work, not a substitute for the server’s confirmed state.

The local server binds only to loopback, checks Host on all requests and Origin on writes, and serves explicit assets rather than the whole filesystem. It validates book/workspace bindings and refuses escaping or substituted output paths. This supports a local personal workflow; it does not turn the server into a multi-user hosted application.

## Optional models are a separate workflow

Manual, Codex and Batch results share the same caption schema. The Codex adapter uses pseudonymous keys, per-attempt folders and a finite timeout; catalog snapshots prevent a delayed response overwriting newer work.

Batch requests are immutable prepared artifacts. The SQLite catalog is the authority for ownership, reservations, reviews and estimated costs. Reservations are committed before a mutation; unknown replies require reconciliation. Results belong to the attempt that produced them, so collecting old output cannot mutate a newer owner. Unusable terminal artifacts can be explicitly rejected without erasing billing uncertainty.

Costs are estimates from recorded rates and returned token usage. Unknown usage retains the original hold. The automatic runner is bounded by active jobs, attempts, new jobs and cycles, and stops at human review boundaries. These choices favor recoverable personal use over unattended throughput.

## Deliberate limits

The toolkit does not invent a photo-book printer integration, order books, infer face identities or promise that an AI model understands a family story. A photo catalog is not an access-controlled photo vault. Proofs are inspectable outputs, not a certification that a printer will accept them. Broader font coverage, a page-plan editor and printer-specific adapters are useful places to extend it.

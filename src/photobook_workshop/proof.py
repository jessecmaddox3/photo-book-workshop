"""Crop-aware PDF, justified HTML, cover comparisons and paper caption proofs."""

from __future__ import annotations
import io
import json
import shutil
from html import escape
from pathlib import Path
from PIL import Image
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph
from .book import confined, digest, atomic_json
from .layout import plan_page

PACKAGE = Path(__file__).resolve().parent


def colors():
    return json.loads((PACKAGE / "palette.json").read_text(encoding="utf-8"))


def fonts():
    for name in ["Baloo2", "Fredoka"]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                TTFont(name, str(PACKAGE / "fonts" / (name + ".ttf")))
            )


def paragraph(text, width, size, font="Fredoka", center=False, color=None):
    style = ParagraphStyle(
        "book",
        fontName=font,
        fontSize=size,
        leading=size * 1.24,
        textColor=HexColor(color or colors()["dark_teal"]),
        alignment=1 if center else 0,
        splitLongWords=True,
    )
    p = Paragraph(escape(text).replace("\n", "<br/>"), style)
    _, height = p.wrap(width, 100000)
    return p, height


def background(canvas, page, layout):
    palette = colors()
    canvas.setFillColor(HexColor(palette["content_box_bg"]))
    canvas.rect(0, 0, layout["width"], layout["height"], fill=1, stroke=0)
    if page.get("pattern") == "dots":
        canvas.setFillColor(HexColor(palette["separator"]))
        for x in range(3, int(layout["width"]) + 1, 22):
            for y in range(3, int(layout["height"]) + 1, 22):
                canvas.circle(x, layout["height"] - y, 1.5, fill=1, stroke=0)


def scrim(canvas, page, layout):
    direction = page.get("scrim", "bottom")
    if layout["kind"] != "cover" or direction == "none":
        return
    # A small stack of translucent strips matches the HTML's directional gradient.
    height = layout["height"] * 0.48
    step = height / 96
    canvas.saveState()
    for i in range(96):
        alpha = 0.75 * (1 - i / 95)
        y = i * step if direction == "bottom" else layout["height"] - (i + 1) * step
        canvas.setFillColor(Color(0, 0, 0, alpha=alpha))
        canvas.rect(0, y, layout["width"], step + 0.05, fill=1, stroke=0)
    canvas.restoreState()


def text_block(canvas, page, layout):
    x, y, w, h = layout["textRect"]
    title = page.get("subject", "")
    caption = page.get("caption", "")
    cover = layout["kind"] == "cover"
    ink = "#ffffff" if cover else colors()["dark_teal"]
    titlep, titleh = paragraph(title, w, page["titleSize"], "Baloo2", True, ink)
    captionp, captionh = paragraph(caption, w, page["fontSize"], "Fredoka", True, ink)
    gap = 8 if title and caption else 0
    if titleh + captionh + gap > h:
        raise ValueError(
            "Text does not fit page "
            + page["id"]
            + ". Shorten it, lower font size or enlarge the caption area."
        )
    if not cover:
        canvas.setFillColor(Color(1, 1, 1, alpha=0.94))
        canvas.roundRect(
            x,
            layout["height"] - y - titleh - gap - captionh,
            w,
            titleh + gap + captionh,
            8,
            fill=1,
            stroke=0,
        )
    if title:
        titlep.drawOn(canvas, x, layout["height"] - y - titleh)
    if caption:
        captionp.drawOn(canvas, x, layout["height"] - y - titleh - gap - captionh)
    panels = []
    for block in layout["textBlocks"]:
        bx, by, bw, bh = block["rect"]
        p, ph = paragraph(
            block["text"],
            bw,
            block["size"],
            block["font"],
            block["align"] == "center",
            "#ffffff" if block["ink"] == "white" else None,
        )
        if ph > bh:
            raise ValueError(
                "Text panel " + block["id"] + " does not fit page " + page["id"]
            )
        if block["panel"] == "white":
            canvas.setFillColor(Color(1, 1, 1, alpha=0.94))
            canvas.roundRect(
                bx, layout["height"] - by - ph, bw, ph, 8, fill=1, stroke=0
            )
        p.drawOn(canvas, bx, layout["height"] - by - ph)
        panels.append(
            {"id": block["id"], "rect": [bx, by, bw, ph], "text": block["text"]}
        )
    return {
        "rect": [x, y, w, titleh + gap + captionh],
        "title": title,
        "caption": caption,
        "panels": panels,
    }


def placeholder(canvas, placement, height):
    x, y, w, h = placement["rect"]
    canvas.setFillColor(HexColor(colors()["dri_box_bg"]))
    canvas.rect(x, height - y - h, w, h, fill=1, stroke=0)
    p, ph = paragraph(
        placement["placeholder"]["label"],
        max(1, w - 16),
        min(22, max(8, w / 12)),
        "Fredoka",
        True,
    )
    if ph > h - 16:
        raise ValueError("Placeholder label does not fit its photo slot")
    p.drawOn(canvas, x + 8, height - y - (h + ph) / 2)


def render_pdf(book, root, output):
    fonts()
    assets = {a["id"]: a for a in book["assets"]}
    layouts = []
    image_cache = {}
    ledger = []
    pages = book["pages"]
    first = plan_page(pages[0], assets, book["physical"])
    canvas = Canvas(
        str(output),
        pagesize=(first["width"], first["height"]),
        pageCompression=1,
        invariant=1,
    )
    canvas.setTitle(book["title"])
    canvas.setAuthor("Photo Book Workshop")
    canvas.setSubject("Local review proof")
    for page_number, page in enumerate(pages, 1):
        layout = plan_page(page, assets, book["physical"])
        layouts.append(layout)
        background(canvas, page, layout)
        canvas.bookmarkPage(page["id"])
        canvas.addOutlineEntry(page.get("subject") or page["id"], page["id"])
        for placement in layout["placements"]:
            if "placeholder" in placement:
                placeholder(canvas, placement, layout["height"])
                continue
            asset = assets[placement["asset"]]
            path = confined(root, asset["path"])
            x, y, w, h = placement["rect"]
            dx, dy, dw, dh = placement["draw"]
            target = (
                max(1, round(dw * book["physical"]["dpi"] / 72)),
                max(1, round(dh * book["physical"]["dpi"] / 72)),
            )
            cache_key = (asset["sha256"], target)
            if cache_key not in image_cache:
                with Image.open(path) as source:
                    image = source.convert("RGB")
                    image.thumbnail(target, Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    image.save(buf, "JPEG", quality=92, subsampling=0)
                    image_cache[cache_key] = (
                        ImageReader(io.BytesIO(buf.getvalue())),
                        image.size,
                    )
            reader, size = image_cache[cache_key]
            canvas.saveState()
            clip = canvas.beginPath()
            clip.rect(x, layout["height"] - y - h, w, h)
            canvas.clipPath(clip, stroke=0, fill=0)
            canvas.drawImage(reader, dx, layout["height"] - dy - dh, dw, dh)
            canvas.restoreState()
            ledger.append(
                {
                    "pageId": page["id"],
                    "pageNumber": page_number,
                    **placement,
                    "embedded_pixels": list(size),
                    "embedded_ppi": min(size[0] * 72 / dw, size[1] * 72 / dh),
                }
            )
        scrim(canvas, page, layout)
        layout["text"] = text_block(canvas, page, layout)
        canvas.showPage()
    canvas.save()
    return {
        "kind": "review-proof",
        "pages": layouts,
        "images": ledger,
        "source_min_ppi": min((p["source_ppi"] for p in ledger), default=None),
        "embedded_min_ppi": min((p["embedded_ppi"] for p in ledger), default=None),
        "note": "Source PPI and embedded proof PPI differ. Confirm print dimensions, bleed, safe areas and image quality with your chosen printer.",
    }


def render_html_page(page, layout, assets):
    palette = colors()
    parts = []
    width, height = layout["width"], layout["height"]
    for p in layout["placements"]:
        x, y, w, h = p["rect"]
        focus = p["focus"]
        style = f"left:{x}px;top:{y}px;width:{w}px;height:{h}px"
        if "placeholder" in p:
            parts.append(
                f'<div class="cell placeholder" style="{style};font-size:{min(22, max(8, w / 12))}px"><span>{escape(p["placeholder"]["label"])}</span></div>'
            )
            continue
        asset = assets[p["asset"]]
        parts.append(
            f'<div class="cell" data-placement="{p["id"]}" style="{style}"><img src="../assets/{asset["sha256"]}{Path(asset["path"]).suffix.lower()}" alt="{escape(asset.get("alt", asset["id"]), quote=True)}" style="object-position:{focus[0] * 100}% {focus[1] * 100}%"></div>'
        )
    x, y, w, h = layout["textRect"]
    cover = layout["kind"] == "cover"
    if cover and page.get("scrim", "bottom") != "none":
        parts.append('<div class="scrim ' + page.get("scrim", "bottom") + '"></div>')
    style = "cover-caption" if cover else "caption"
    title = (
        f'<h1 style="font-size:{page["titleSize"]}px">{escape(page["subject"])}</h1>'
        if page.get("subject")
        else ""
    )
    caption = (
        f'<p style="font-size:{page["fontSize"]}px; margin-top:{8 if title else 0}px">{escape(page["caption"])}</p>'
        if page.get("caption")
        else ""
    )
    parts.append(
        f'<section class="{style}" style="left:{x}px;top:{y}px;width:{w}px;max-height:{h}px">{title}{caption}</section>'
    )
    for block in layout["textBlocks"]:
        bx, by, bw, bh = block["rect"]
        ink = "white" if block["ink"] == "white" else palette["dark_teal"]
        panel = "rgba(255,255,255,.94)" if block["panel"] == "white" else "transparent"
        parts.append(
            f'<section class="text-panel" data-panel="{block["id"]}" style="left:{bx}px;top:{by}px;width:{bw}px;max-height:{bh}px;font-family:{block["font"]};font-size:{block["size"]}px;text-align:{block["align"]};color:{ink};background:{panel}">{escape(block["text"])}</section>'
        )
    pattern = (
        f"background-image:radial-gradient(circle at 3px 3px,{palette['separator']} 1.5px,transparent 1.5px);background-size:22px 22px;"
        if page.get("pattern") == "dots"
        else ""
    )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(page.get("subject", page["id"]))}</title><style>
@font-face{{font-family:Baloo2;src:url('../fonts/Baloo2.ttf')}}@font-face{{font-family:Fredoka;src:url('../fonts/Fredoka.ttf')}}
*{{box-sizing:border-box;margin:0;padding:0}}html,body{{width:{width}px;height:{height}px;overflow:hidden}}body{{position:relative;background-color:{palette["content_box_bg"]};color:{palette["dark_teal"]};{pattern}}}.cell{{position:absolute;overflow:hidden}}.cell img{{width:100%;height:100%;object-fit:cover;display:block}}.placeholder{{display:flex;align-items:center;justify-content:center;text-align:center;padding:8px;font-family:Fredoka;background:{palette["dri_box_bg"]};overflow-wrap:anywhere}}section{{position:absolute;text-align:center;z-index:3;overflow:visible;overflow-wrap:anywhere;line-height:1.24;white-space:pre-wrap}}h1{{font-family:Baloo2;line-height:1.24;font-weight:400}}p{{font-family:Fredoka;line-height:1.24;white-space:pre-wrap}}.caption{{background:rgba(255,255,255,.94);border-radius:8px}}.cover-caption{{color:white}}.scrim{{position:absolute;left:0;right:0;height:48%;z-index:2;pointer-events:none}}.scrim.bottom{{bottom:0;background:linear-gradient(transparent,rgba(0,0,0,.75))}}.scrim.top{{top:0;background:linear-gradient(rgba(0,0,0,.75),transparent)}}
</style></head><body data-page="{page["id"]}">{"".join(parts)}</body></html>'''


def render_html(book, root, output):
    output = Path(output)
    output.mkdir()
    (output / "pages").mkdir()
    (output / "assets").mkdir()
    shutil.copytree(PACKAGE / "fonts", output / "fonts")
    assets = {a["id"]: a for a in book["assets"]}
    all_pages = book["pages"] + book.get("covers", [])
    used = {p["asset"] for page in all_pages for p in page["images"] if "asset" in p}
    for ident in sorted(used):
        asset = assets[ident]
        shutil.copyfile(
            confined(root, asset["path"]),
            output / "assets" / (asset["sha256"] + Path(asset["path"]).suffix.lower()),
        )
    layouts = []
    cards = []
    for page in all_pages:
        layout = plan_page(page, assets, book["physical"])
        layouts.append(layout)
        (output / "pages" / (page["id"] + ".html")).write_text(
            render_html_page(page, layout, assets), encoding="utf-8"
        )
        image = next((p for p in page["images"] if "asset" in p), None)
        thumb = ""
        if image:
            asset = assets[image["asset"]]
            thumb = f'<img alt="" src="assets/{asset["sha256"]}{Path(asset["path"]).suffix.lower()}">'
        cards.append(
            f'<a href="pages/{page["id"]}.html">{thumb}<strong>{escape(page.get("subject") or page["id"])}</strong><span>{"Alternate cover" if page in book.get("covers", []) else "Book page"}</span></a>'
        )
    palette = colors()
    (output / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(book["title"])}</title><style>@font-face{{font-family:Fredoka;src:url('fonts/Fredoka.ttf')}}*{{box-sizing:border-box}}body{{max-width:1100px;margin:auto;padding:32px 20px;font:18px/1.5 Fredoka,system-ui;color:{palette["dark_teal"]};background:{palette["content_box_bg"]}}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:20px}}a{{display:flex;flex-direction:column;gap:8px;color:inherit;text-decoration:none;background:white;padding:12px;border:1px solid {palette["separator"]};border-radius:10px}}img{{width:100%;aspect-ratio:4/3;object-fit:cover}}a:hover,a:focus{{outline:3px solid {palette["section_bar"]}}}span{{font-size:14px}}</style></head><body><p>PHOTO BOOK WORKSHOP · LOCAL PROOFS</p><h1>{escape(book["title"])}</h1><p>Open a page to inspect its layout. These pages keep their physical proportions. Use the capture command for image exports at your chosen dimensions.</p><main>{"".join(cards)}</main></body></html>""",
        encoding="utf-8",
    )
    return {
        "pages": layouts,
        "units": "CSS pixels matching PDF points; capture scales by DPI/72",
        "covers": [p["id"] for p in book.get("covers", [])],
    }


def paper_proofs(book, output):
    fonts()
    output = Path(output)
    reports = {}
    for kind in ["captions", "checklist", "picksheet"]:
        c = Canvas(str(output / (kind + ".pdf")), pagesize=(612, 792), invariant=1)
        c.setTitle(book["title"] + ": " + kind)
        c.setAuthor("Photo Book Workshop")
        y = 748
        number = 1
        rows = []

        def newpage():
            nonlocal y, number
            if y != 748:
                c.showPage()
                number += 1
                y = 748

        def paint(p, h, page_id, oid, role, *, checkbox=False, continued=False):
            nonlocal y
            x = 74 if oid is not None else 54
            width = 484 if oid is not None else 504
            if checkbox:
                c.setStrokeColor(HexColor(colors()["dark_teal"]))
                c.rect(54, y - 10, 9, 9, fill=0, stroke=1)
            rendered = p.getPlainText()
            if not rendered and hasattr(p, "blPara"):
                rendered = " ".join(
                    " ".join(line[1])
                    if p.blPara.kind == 0
                    else "".join(getattr(word, "text", "") for word in line.words)
                    for line in p.blPara.lines
                )
            p.drawOn(c, x, y - h)
            rows.append(
                {
                    "pageId": page_id,
                    "optionId": oid,
                    "sheet": number,
                    "text": rendered,
                    "rect": [x, 792 - y, width, h],
                    "role": role,
                    "continued": continued,
                }
            )
            y -= h + 9

        for page in book["pages"]:
            options = (
                page.get("options", [])
                if kind != "captions"
                else [{"id": None, "text": page.get("caption", "")}]
            )
            if kind == "picksheet":
                options = [
                    *options,
                    {
                        "id": "write-in",
                        "text": "Write-in: _________________________________________",
                    },
                ]
            heading = page.get("subject") or page["id"]
            hp, hh = paragraph(heading, 504, 16, "Baloo2")
            entries = []
            for option in options:
                oid = option["id"]
                p, h = paragraph(
                    option["text"], 484 if oid is not None else 504, 11, "Fredoka"
                )
                entries.append((oid, p, h))
            total = hh + 9 + sum(h + 9 for _, _, h in entries) + 10
            if total <= 704 and y - total < 44:
                newpage()
            elif total > 704 and y - hh - 9 - 28 < 44:
                newpage()
            if hh > 620:
                raise ValueError(
                    "Paper heading is too long; use a shorter page subject"
                )
            paint(hp, hh, page["id"], None, "heading")
            for oid, p, h in entries:
                first = True
                parts = [p]
                width = 484 if oid is not None else 504
                while parts:
                    part = parts.pop(0)
                    _, height = part.wrap(width, 100000)
                    available = y - 44
                    if height <= available:
                        paint(
                            part,
                            height,
                            page["id"],
                            oid,
                            "wording",
                            checkbox=first and oid is not None,
                            continued=not first,
                        )
                        first = False
                        continue
                    chunks = part.split(width, available) if available >= 28 else []
                    if chunks:
                        chunk = chunks[0]
                        _, ch = chunk.wrap(width, available)
                        paint(
                            chunk,
                            ch,
                            page["id"],
                            oid,
                            "wording",
                            checkbox=first and oid is not None,
                            continued=not first,
                        )
                        first = False
                        parts = chunks[1:] + parts
                    else:
                        parts.insert(0, part)
                    newpage()
                    continued, continued_h = paragraph(
                        heading + " (continued)", 504, 16, "Baloo2"
                    )
                    if continued_h + 28 > 704:
                        raise ValueError(
                            "Paper heading leaves no room for caption wording"
                        )
                    paint(
                        continued,
                        continued_h,
                        page["id"],
                        None,
                        "heading",
                        continued=True,
                    )
            y -= 10
        c.save()
        reports[kind] = {"sheets": number, "rows": rows}
    return reports


def build_book(book, root, output):
    """New complete output folder only. A failed build leaves prior proofs intact."""
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(
            "Choose a new output folder; existing proofs are preserved"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    try:
        pdf = render_pdf(book, root, output / "book-proof.pdf")
        html = render_html(book, root, output / "html")
        paper = paper_proofs(book, output)
        result = {
            "bookId": book["bookId"],
            "bookHash": digest(book),
            "pdf": pdf,
            "html": html,
            "paper": paper,
        }
        atomic_json(output / "proof-ledger.json", result)
        return result
    except Exception:
        shutil.rmtree(output)
        raise

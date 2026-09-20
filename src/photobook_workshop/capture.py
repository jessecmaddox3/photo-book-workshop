"""Portable, offline browser proof capture at explicit physical dimensions."""

from __future__ import annotations
import json
import math
import shutil
from pathlib import Path
from .book import atomic_json, identifier, number, confined


def capture_pages(proof, output, *, dpi=180):
    if type(dpi) not in (int, float) or not math.isfinite(dpi) or not 72 <= dpi <= 600:
        raise ValueError("Choose 72-600 DPI")
    from playwright.sync_api import sync_playwright

    proof = Path(proof).resolve(strict=True)
    output = Path(output).resolve()
    ledger = json.loads((proof / "proof-ledger.json").read_text(encoding="utf-8"))
    html = (proof / "html").resolve(strict=True)
    layouts = ledger["html"]["pages"]
    if not isinstance(layouts, list) or not 1 <= len(layouts) <= 288:
        raise ValueError("Invalid proof page list")
    seen = set()
    for layout in layouts:
        ident = identifier(layout["pageId"])
        if ident in seen:
            raise ValueError("Duplicate capture page")
        seen.add(ident)
        number(layout["width"], 72, 1296, "page width")
        number(layout["height"], 72, 1296, "page height")
        confined(html, "pages/" + ident + ".html")
    if output.exists():
        raise FileExistsError("Choose a new capture folder")
    output.mkdir(parents=True)
    results = []
    requests = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                for layout in ledger["html"]["pages"]:
                    width, height = layout["width"], layout["height"]
                    page = browser.new_page(
                        viewport={
                            "width": math.ceil(width),
                            "height": math.ceil(height),
                        },
                        device_scale_factor=dpi / 72,
                    )
                    outside = []
                    errors = []

                    def route(request):
                        url = request.request.url
                        if url.startswith(html.as_uri() + "/"):
                            requests.append(url.removeprefix(html.as_uri() + "/"))
                            request.continue_()
                        else:
                            outside.append(url)
                            request.abort()

                    page.route("**/*", route)
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto((html / "pages" / (layout["pageId"] + ".html")).as_uri())
                    page.evaluate(
                        "async () => { await document.fonts.ready; await Promise.all([...document.images].map(i => i.decode())); }"
                    )
                    check = page.evaluate(
                        r"""() => ({images:[...document.images].map(i=>({ok:i.complete&&i.naturalWidth>0})),text:[...document.querySelectorAll('section,.placeholder')].map(s=>{
                          const box=s.getBoundingClientRect(),rects=[],walk=document.createTreeWalker(s,NodeFilter.SHOW_TEXT);
                          // Collapsible spaces can hang past a line box without visible ink.
                          // Check every visible run rather than weakening the clipping tolerance.
                          for(let node=walk.nextNode();node;node=walk.nextNode())for(const match of node.textContent.matchAll(/\S+/gu)){
                            const range=document.createRange();range.setStart(node,match.index);range.setEnd(node,match.index+match[0].length);
                            rects.push(...[...range.getClientRects()].map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom})));
                          }
                          return {height:s.scrollHeight,max:parseFloat(s.style.maxHeight)||box.height,bottom:box.bottom,left:box.left,right:box.right,overflow:s.scrollWidth>s.clientWidth+1,rects};
                        }),width:document.body.getBoundingClientRect().width,height:document.body.getBoundingClientRect().height})"""
                    )
                    if outside or errors or any(not i["ok"] for i in check["images"]):
                        raise ValueError(
                            "Page failed offline image/browser validation: "
                            + layout["pageId"]
                        )
                    if any(
                        t["height"] > t["max"] + 1
                        or t["bottom"] > height + 1
                        or t["left"] < -0.6
                        or t["right"] > width + 0.6
                        or t["overflow"]
                        or any(
                            r["left"] < t["left"] - 0.6
                            or r["right"] > t["right"] + 0.6
                            or r["top"] < -0.6
                            or r["bottom"] > height + 0.6
                            for r in t["rects"]
                        )
                        for t in check["text"]
                    ):
                        raise ValueError(
                            "Browser text overflows page " + layout["pageId"]
                        )
                    target = output / (layout["pageId"] + ".png")
                    page.screenshot(
                        path=str(target),
                        clip={"x": 0, "y": 0, "width": width, "height": height},
                    )
                    from PIL import Image

                    with Image.open(target) as im:
                        pixels = list(im.size)
                    expected = [round(width * dpi / 72), round(height * dpi / 72)]
                    if any(abs(a - b) > 1 for a, b in zip(pixels, expected)):
                        raise ValueError(
                            "Browser pixel dimensions do not match physical specification"
                        )
                    results.append(
                        {
                            "pageId": layout["pageId"],
                            "file": target.name,
                            "pixels": pixels,
                            "dpi": dpi,
                            "images": len(check["images"]),
                        }
                    )
                    page.close()
            finally:
                browser.close()
        result = {
            "bookId": ledger["bookId"],
            "bookHash": ledger["bookHash"],
            "pages": results,
            "outsideRequests": 0,
            "localRequests": sorted(set(requests)),
            "note": "Review captures, not a printer certification. Verify your chosen printer specification.",
        }
        atomic_json(output / "capture-ledger.json", result)
        return result
    except Exception:
        shutil.rmtree(output)
        raise

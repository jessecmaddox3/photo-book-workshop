"""Exercise the invented local review app, including save faults, at real viewports."""

from __future__ import annotations
import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from threading import Thread
from playwright.async_api import async_playwright, expect
from photobook_workshop.server import make_server
from photobook_workshop.book import atomic_json


async def run(output):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    outside = []
    with tempfile.TemporaryDirectory(prefix="photo-book-browser-") as tmp:
        book = (
            Path(__file__).resolve().parents[1]
            / "src/photobook_workshop/demo/book.json"
        )
        server = make_server(book, Path(tmp) / "review", port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                try:
                    context = await browser.new_context(
                        viewport={"width": 1440, "height": 1000}, accept_downloads=True
                    )

                    async def guard(route):
                        if route.request.url.startswith(url + "/"):
                            await route.continue_()
                        else:
                            outside.append(route.request.url)
                            await route.abort()

                    await context.route("**/*", guard)
                    context.on(
                        "page",
                        lambda page: page.on(
                            "pageerror", lambda e: errors.append(str(e))
                        ),
                    )
                    page = await context.new_page()
                    await page.goto(url)
                    await expect(page.locator("main")).to_be_visible()
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    state = await (await context.request.get(url + "/api/state")).json()
                    assert not state["choices"]
                    assert await page.locator("#options input:checked").count() == 0
                    results.append("Initial option preview is not a saved choice")
                    for width in [320, 390, 768, 1440]:
                        await page.set_viewport_size({"width": width, "height": 1000})
                        await page.evaluate("document.fonts.ready")
                        await page.evaluate(
                            "Promise.all([...document.images].map(i=>i.decode()))"
                        )
                        assert await page.evaluate(
                            "document.documentElement.scrollWidth<=innerWidth+1"
                        ), f"Overflow at {width}"
                        await page.screenshot(
                            path=str(output / f"review-{width}.png"), full_page=True
                        )
                    results.append(
                        "320/390/768/1440 layouts, fonts and images load without horizontal overflow"
                    )
                    await page.locator("#options input").nth(1).check()
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    await page.locator("#note").fill("Café by the imaginary shore.")
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    await page.reload()
                    await expect(page.locator("#note")).to_have_value(
                        "Café by the imaginary shore."
                    )
                    await page.locator("#summary-open").click()
                    await expect(page.locator("#summary")).to_be_visible()
                    assert await page.locator(".summary-row").count() == 5
                    await page.locator("#summary-close").click()
                    async with page.expect_download() as download_info:
                        await page.locator("#download").click()
                    downloaded = await download_info.value
                    download_path = await downloaded.path()
                    export = json.loads(Path(download_path).read_text())
                    assert export["pending"] is False and export["choices"]["cover"][
                        "note"
                    ].startswith("Café")
                    await page.locator("#clear").click()
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    assert await page.locator("#options input:checked").count() == 0
                    results.append(
                        "Choose, Unicode note, reload, summary, export and clear"
                    )
                    # Hold the first acknowledgement while a newer note is entered.
                    arrived = asyncio.Event()
                    release = asyncio.Event()
                    held = False

                    async def delay(route):
                        nonlocal held
                        response = await route.fetch()
                        if not held:
                            held = True
                            arrived.set()
                            await release.wait()
                        await route.fulfill(response=response)

                    await page.route("**/api/choice", delay)
                    await page.locator("#options input").first.check()
                    await asyncio.wait_for(arrived.wait(), 10)
                    await page.locator("#note").fill(
                        "This newer draft survives a delayed reply."
                    )
                    release.set()
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    state = await (await context.request.get(url + "/api/state")).json()
                    assert (
                        state["choices"]["cover"]["note"]
                        == "This newer draft survives a delayed reply."
                    )
                    await page.unroute("**/api/choice", delay)
                    results.append(
                        "Newer typing survives a delayed save acknowledgement"
                    )
                    # Save on the server, then lose the response. Retrying cannot create another revision.
                    lost = False

                    async def lose(route):
                        nonlocal lost
                        response = await route.fetch()
                        if not lost:
                            lost = True
                            await route.abort("failed")
                        else:
                            await route.fulfill(response=response)

                    await page.route("**/api/choice", lose)
                    await page.locator("#note").fill(
                        "One saved operation, even when the reply disappears."
                    )
                    await expect(page.locator("#retry")).to_be_visible()
                    before = await (
                        await context.request.get(url + "/api/state")
                    ).json()
                    await page.locator("#retry").click()
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    after = await (await context.request.get(url + "/api/state")).json()
                    assert before == after
                    await page.unroute("**/api/choice", lose)
                    results.append(
                        "Lost response retries the same save without another revision"
                    )
                    # Focus old text, let another tab save and background refresh complete, then type.
                    await page.locator("#note").focus()
                    other = await context.new_page()
                    await other.goto(url)
                    await expect(other.locator("main")).to_be_visible()
                    await other.locator("#note").fill("A different tab wrote this.")
                    await expect(other.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    await page.wait_for_timeout(5400)
                    await page.locator("#note").fill(
                        "My draft from the older visible editor."
                    )
                    await expect(page.locator("#conflict")).to_be_visible()
                    assert (
                        await page.locator("#note").input_value()
                        == "My draft from the older visible editor."
                    )
                    await page.locator("#keep-mine").click()
                    await expect(page.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    await other.close()
                    results.append(
                        "Focused stale editor surfaces conflict and explicit keep-mine saves it"
                    )
                    await page.locator("#next").click()
                    assert (
                        await page.locator("#page-position").inner_text()
                        == "Page 2 of 5"
                    )
                    await page.locator("#previous").click()
                    await page.locator("#build").click()
                    await expect(page.locator("#proofs")).to_be_visible(timeout=30000)
                    for link in await page.locator("#proof-links a").all():
                        href = await link.get_attribute("href")
                        response = await context.request.get(url + href)
                        assert response.status == 200
                        if href.endswith(".pdf"):
                            assert (await response.body()).startswith(b"%PDF-")
                    results.append(
                        "Explicit proof build returns book PDF, HTML pages, checklists, choices and ledger"
                    )
                    blocked = await context.new_page()
                    await blocked.add_init_script(
                        "Storage.prototype.setItem = function(){throw new Error('storage disabled')}"
                    )
                    await blocked.goto(url)
                    await expect(blocked.locator("main")).to_be_visible()
                    await expect(blocked.locator("#notice")).to_contain_text(
                        "Browser draft storage is unavailable"
                    )
                    await blocked.close()
                    failed = await context.new_page()
                    await failed.route(
                        "**/api/state",
                        lambda r: r.fulfill(
                            status=422, json={"error": "Saved choices need recovery."}
                        ),
                    )
                    await failed.goto(url)
                    await expect(failed.locator("#fatal")).to_be_visible()
                    await expect(failed.locator("main")).to_be_hidden()
                    await expect(failed.locator("#build")).to_be_disabled()
                    await failed.close()
                    results.append(
                        "Blocked local storage is visible; failed initial state prevents editing"
                    )
                    recovery = await context.new_page()
                    await recovery.add_init_script(
                        "Object.defineProperty(window,'sessionStorage',{get(){throw new Error('blocked')}})"
                    )
                    await recovery.goto(url)
                    await expect(recovery.locator("main")).to_be_visible()
                    await expect(recovery.locator("#durability")).to_be_visible()

                    async def fail_save(route):
                        await route.abort("failed")

                    await recovery.route("**/api/choice", fail_save)
                    await recovery.locator("#note").fill(
                        "Recover this unsent note after blocked tab storage."
                    )
                    await expect(recovery.locator("#retry")).to_be_visible()
                    await recovery.reload()
                    await expect(recovery.locator("#recovery")).to_be_visible()
                    await recovery.unroute("**/api/choice", fail_save)
                    await recovery.locator("#recovery summary").click()
                    await recovery.get_by_role(
                        "button", name="Recover in this tab"
                    ).last.click()
                    await expect(recovery.locator("#note")).to_have_value(
                        "Recover this unsent note after blocked tab storage."
                    )
                    await expect(recovery.locator("#save-status")).to_have_text(
                        "Saved on this computer."
                    )
                    await recovery.route("**/api/choice", fail_save)
                    await recovery.locator("#note").fill(
                        "A recovery export must contain this pending wording."
                    )
                    await expect(recovery.locator("#retry")).to_be_visible()
                    await recovery.route(
                        "**/api/state",
                        lambda r: r.fulfill(
                            status=422, json={"error": "Saved choices need recovery."}
                        ),
                    )
                    await recovery.reload()
                    await expect(recovery.locator("#fatal")).to_be_visible()
                    async with recovery.expect_download() as recovery_download:
                        await recovery.locator("#download").click()
                    recovered_file = await recovery_download.value
                    payload = json.loads(
                        Path(await recovered_file.path()).read_text(encoding="utf-8")
                    )
                    assert (
                        "A recovery export must contain this pending wording."
                        in json.dumps(payload)
                    )
                    await recovery.close()
                    results.append(
                        "Blocked tab identity retains recoverable drafts; fatal-state export includes pending wording"
                    )

                    assert not errors and not outside, {
                        "errors": errors,
                        "outside": outside,
                    }
                    await context.close()
                finally:
                    await browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
    result = {"checks": results, "pageErrors": errors, "outsideRequests": outside}
    atomic_json(output / "results.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output))

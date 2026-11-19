"""Pocket on an emulated phone in airplane mode: search, key in a price, shop, export.

Optional, for developers:  pip install playwright && playwright install chromium
    python tools/pocket_e2e.py path/to/pricewise-pocket.html output_folder
Prints timings, any network requests (there should be none) and what the export contained."""
import json, sys, time
from playwright.sync_api import sync_playwright
html, out = sys.argv[1], sys.argv[2]
net = []
with sync_playwright() as p:
    import os
    exe = os.environ.get("CHROME")
    b = p.chromium.launch(**({"executable_path": exe} if exe else {}), args=["--no-sandbox"])
    dev = p.devices["iPhone 13"]
    ctx = b.new_context(**{k: v for k, v in dev.items() if k != "default_browser_type"}, accept_downloads=True)
    ctx.set_offline(True)                                   # airplane mode
    pg = ctx.new_page()
    pg.on("request", lambda r: net.append(r.url) if not r.url.startswith(("file:", "data:", "blob:")) else None)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(__import__("pathlib").Path(html).resolve().as_uri())
    pg.wait_for_selector("#q")
    pg.screenshot(path=f"{out}/1-start.png")
    # store: Kroger
    pg.select_option("#store", "Kroger")
    t0 = time.perf_counter()
    pg.tap("#q"); pg.fill("#q", "spaghetti")
    pg.screenshot(path=f"{out}/2-search.png")
    pg.locator("#results .row").filter(has_text="Kroger Spaghetti").first.tap()
    for k in "100":
        pg.tap(f'[data-k="{k}"]')
    pg.screenshot(path=f"{out}/3-keypad.png")
    pg.tap("#save")
    scripted = time.perf_counter() - t0
    pg.wait_for_timeout(300)
    # a product with no history: record a shelf price
    pg.fill("#q", "")
    # multi-buy on yogurt: 2 for $9
    pg.tap("#q"); pg.fill("#q", "greek yogurt")
    pg.locator("#results .row").first.tap()
    pg.tap("#mb\\+")
    for k in "900":
        pg.tap(f'[data-k="{k}"]')
    pg.locator("details summary").tap(); pg.fill("#terms", "buy 2; Kroger card; ends Sat")
    pg.screenshot(path=f"{out}/4-multibuy.png")
    pg.tap("#save")
    # shopping mode
    pg.tap('#tabs button[data-v="shop"]')
    for i in range(3):
        pg.locator(".check").nth(i).tap()
    pg.locator('[data-m="3"]').tap()
    pg.screenshot(path=f"{out}/5-shop.png")
    subs = pg.locator("[data-sub]")
    if subs.count():
        subs.first.tap()
    pg.locator('[data-m="4"]').tap(); pg.locator('[data-rm="4"]').tap()
    pg.screenshot(path=f"{out}/6-shop-after.png")
    # sync
    pg.tap('#tabs button[data-v="sync"]')
    with pg.expect_download() as dl:
        pg.tap("#export")
    d = dl.value
    path = f"{out}/{d.suggested_filename}"
    d.save_as(path)
    pg.screenshot(path=f"{out}/7-sync.png")
    # reload offline: everything still there
    pg.reload(); pg.wait_for_selector("#q")
    kept = pg.evaluate("JSON.parse(localStorage.getItem('pricewise-pocket')).observations.length")
    data = json.load(open(path))
    print(json.dumps({"scripted_seconds": round(scripted, 2), "recorded_seconds": [o["seconds"] for o in data["observations"]],
                      "network_requests": net, "errors": errors, "export": path,
                      "observations": len(data["observations"]), "list_changes": len(data["list_changes"]),
                      "kept_after_reload": kept}))
    b.close()

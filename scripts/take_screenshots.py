"""
Capture website screenshots into screenshots/.

Starts the app in-process with a throwaway data folder, drives a browser with
Playwright, runs a few real requests, and saves PNG files.

    pip install -e ".[screenshots]"
    python scripts/take_screenshots.py            # uses installed Chrome or Edge
    python scripts/take_screenshots.py --browser chromium   # Playwright's own browser

Uses Groq for the "approved" example if GROQ_API_KEY is set (in .env), else
the local model or the rules.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "screenshots"

# Fresh, empty runtime data so the screenshots show only this run's records
os.environ["DPI_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="dpi_screens_")
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from dpi_agent.web.api import providers  # noqa: E402
from dpi_agent.web.server import create_server  # noqa: E402

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


def launch(playwright, browser: str):
    if browser == "chromium":
        return playwright.chromium.launch()
    for channel in [browser] if browser != "auto" else ["chrome", "msedge"]:
        try:
            return playwright.chromium.launch(channel=channel)
        except Exception as exc:  # noqa: BLE001 - try the next installed browser
            print(f"  {channel} not available: {exc.__class__.__name__}")
    return playwright.chromium.launch()


def ask(page: Page, text: str, provider: str) -> None:
    option = page.locator(f'input[name="provider"][value="{provider}"]')
    if option.is_enabled():
        option.check(force=True)
    page.fill("#query", text)
    page.click("#send-btn")
    page.wait_for_selector("#outcome:not([hidden])", timeout=90_000)
    page.wait_for_timeout(600)


def shot(page: Page, name: str, full_page: bool = False) -> None:
    # Back to the top first (clicking scrolls the page); a wheel event works
    # under the site's Content-Security-Policy, which blocks script evaluation.
    page.mouse.move(10, 400)
    page.mouse.wheel(0, -50_000)
    page.wait_for_timeout(700)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    print(f"  saved {path.relative_to(ROOT)}")


def best_provider() -> str:
    health = providers.health()
    for name in ("groq", "ollama"):
        if health[name].get("available"):
            return name
    return "deterministic"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--browser", default="auto", help="auto, chrome, msedge or chromium")
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True)
    server = create_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    model = best_provider()
    print(f"  serving {base}; AI used for examples: {model}")

    with sync_playwright() as playwright:
        browser = launch(playwright, args.browser)
        page = browser.new_page(viewport=DESKTOP, device_scale_factor=1)
        # Surface any script or Content-Security-Policy error in the page
        page.on("console", lambda m: m.type == "error" and print(f"  browser error: {m.text}"))
        page.on("pageerror", lambda e: print(f"  page error: {e}"))

        page.goto(f"{base}/#home")
        expect(page.locator("#stat-records")).not_to_have_text("–")
        shot(page, "01-home")
        shot(page, "01-home-full", full_page=True)

        page.goto(f"{base}/#ask")
        expect(page.locator("#header-status-text")).to_have_text(re.compile("^Reading"))
        ask(page, "Mera ID 765-741-434 hai, mujhe solar pump ke liye subsidy chahiye", model)
        shot(page, "02-ask-approved", full_page=True)

        ask(
            page,
            "I am Suresh Mishra, ID 715-454-455. I want to apply for a solar pump subsidy.",
            model,
        )
        shot(page, "03-ask-officer-review", full_page=True)

        ask(
            page,
            "I am Anjali Iyer, ID 213-163-577. I want to assess my credit for an agricultural loan.",
            "deterministic",
        )

        for name, view in [("04-services", "services"), ("05-how-it-works", "how")]:
            page.goto(f"{base}/#{view}")
            page.wait_for_timeout(300)
            shot(page, name)

        page.goto(f"{base}/#transparency")
        page.wait_for_selector("#audit-table tbody tr")
        page.click("#verify-btn")
        page.wait_for_selector("#integrity-text.is-ok, #integrity-text.is-bad")
        shot(page, "06-transparency")

        page.goto(f"{base}/#data")
        page.wait_for_selector("#registry-table tbody tr td")
        shot(page, "07-open-data")

        mobile = browser.new_page(viewport=MOBILE, device_scale_factor=2)
        mobile.goto(f"{base}/#home")
        expect(mobile.locator("#stat-records")).not_to_have_text("–")
        shot(mobile, "08-mobile-home")

        page.goto(f"{base}/#ask")
        page.click("#contrast-toggle")
        ask(page, "I want to apply for a solar pump subsidy.", "deterministic")
        shot(page, "09-high-contrast", full_page=True)
        page.click("#contrast-toggle")

        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()

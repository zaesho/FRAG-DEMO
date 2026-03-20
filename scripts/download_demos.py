"""Download CS2 demos from HLTV using Playwright to bypass Cloudflare."""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

DEMOS_DIR = Path(__file__).resolve().parents[1] / "demos"
DEMOS_DIR.mkdir(exist_ok=True)


def get_hltv_match_links(page, max_links: int = 5) -> list[str]:
    """Extract match page URLs from HLTV results page."""
    links = page.eval_on_selector_all(
        "a",
        r"""els => els.map(e => e.href)
            .filter(h => /\/matches\/\d+\//.test(h))
            .filter((v, i, a) => a.indexOf(v) === i)""",
    )
    return links[:max_links]


def get_demo_download_url(page, match_url: str) -> str | None:
    """Navigate to a match page and find the GOTV demo download link."""
    page.goto(match_url, timeout=30000)
    page.wait_for_timeout(3000)

    # HLTV demo link is typically at /download/demo/{id}
    links = page.eval_on_selector_all(
        "a",
        "els => els.map(e => e.href).filter(h => h.includes('/download/demo/'))",
    )
    return links[0] if links else None


def download_demo(page, download_url: str, dest_dir: Path) -> Path | None:
    """Download a demo file using the browser context."""
    with page.expect_download(timeout=120000) as download_info:
        page.goto(download_url)
    download = download_info.value
    filename = download.suggested_filename or "demo.rar"
    dest = dest_dir / filename
    download.save_as(str(dest))
    print(f"  Saved: {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
    return dest


def main():
    num_demos = int(sys.argv[1]) if len(sys.argv) > 1 else 2

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            accept_downloads=True,
        )
        page = context.new_page()
        Stealth().apply(page)

        print("Loading HLTV results...")
        page.goto("https://www.hltv.org/results", timeout=60000)

        # Wait for CF challenge
        for attempt in range(6):
            time.sleep(5)
            title = page.title()
            print(f"  [{attempt+1}] Title: {title}")
            if "moment" not in title.lower() and "checking" not in title.lower():
                break

        match_links = get_hltv_match_links(page, max_links=num_demos + 5)
        print(f"\nFound {len(match_links)} match links")

        downloaded = 0
        for i, match_url in enumerate(match_links):
            if downloaded >= num_demos:
                break

            print(f"\n[{i+1}] Checking: {match_url}")
            try:
                demo_url = get_demo_download_url(page, match_url)
                if not demo_url:
                    print("  No demo link found, skipping")
                    continue

                print(f"  Demo URL: {demo_url}")
                download_demo(page, demo_url, DEMOS_DIR)
                downloaded += 1
            except Exception as e:
                print(f"  Error: {e}")

        browser.close()

    print(f"\nDone! Downloaded {downloaded} demos to {DEMOS_DIR}")


if __name__ == "__main__":
    main()

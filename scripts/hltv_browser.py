"""Open HLTV in a stealth browser for manual demo downloading.

The browser stays open so you can navigate to matches and click 
'GOTV Demo' download links. Downloaded files go to demos/.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

DEMOS_DIR = Path(__file__).resolve().parents[1] / "demos"
DEMOS_DIR.mkdir(exist_ok=True)

TARGET_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.hltv.org/results"


def main():
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

        # Auto-save any downloads to demos/
        def handle_download(download):
            fname = download.suggested_filename or "demo.rar"
            dest = DEMOS_DIR / fname
            print(f"Downloading: {fname} -> {dest}")
            download.save_as(str(dest))
            size_mb = dest.stat().st_size / 1024 / 1024
            print(f"Saved: {dest} ({size_mb:.1f} MB)")

        page.on("download", handle_download)

        print(f"Opening {TARGET_URL}")
        print(f"Downloads will be saved to: {DEMOS_DIR}")
        print("Navigate to match pages and click 'GOTV Demo' to download.")
        print("Close the browser window when done.
")

        page.goto(TARGET_URL, timeout=60000)

        # Keep alive until browser is closed
        try:
            while True:
                if not browser.is_connected():
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    print("
Browser closed. Check demos/ for downloaded files.")
    for f in DEMOS_DIR.iterdir():
        if f.suffix in (".dem", ".rar", ".gz", ".zip"):
            print(f"  {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()

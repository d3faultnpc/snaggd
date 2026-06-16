"""
One-shot: open a chatik URL with saved cookies, dump iframe DOM to file.
Usage: python scripts/dump_chatik.py <chatik_url>
"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

COOKIES_PATH = Path("data/profiles/pm/applied_log.json").parent / ".." / ".." / "data/hh_cookies.json"
# fallback: root data/
if not COOKIES_PATH.exists():
    COOKIES_PATH = Path("data/hh_cookies.json")

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/dump_chatik.py <chatik_url>")
        print("Example: python scripts/dump_chatik.py https://chatik.hh.ru/chat/YOUR_CHAT_ID")
        sys.exit(1)
    url = sys.argv[1]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        with open(COOKIES_PATH) as f:
            ctx.add_cookies(json.load(f))

        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # Dump main page DOM
        out = Path("tmp/chatik_dump.html")
        out.parent.mkdir(exist_ok=True)
        out.write_text(page.content(), encoding="utf-8")
        print(f"✅ Main page DOM → {out}")

        # Dump all frames
        for i, frame in enumerate(page.frames):
            frame_out = Path(f"tmp/chatik_frame_{i}.html")
            try:
                frame_out.write_text(frame.content(), encoding="utf-8")
                print(f"✅ Frame {i} ({frame.url[:80]}) → {frame_out}")
            except Exception as e:
                print(f"⚠️ Frame {i} error: {e}")

        # Print visible text from each frame
        print("\n=== FRAME TEXTS ===")
        for i, frame in enumerate(page.frames):
            try:
                text = frame.inner_text("body")[:2000]
                if text.strip():
                    print(f"\n--- Frame {i} ({frame.url[:60]}) ---\n{text}")
            except Exception:
                pass

        page.wait_for_timeout(2000)
        browser.close()

if __name__ == "__main__":
    main()

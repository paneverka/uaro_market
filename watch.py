#!/usr/bin/env python3
"""
watch_uaro_playwright.py

Fixes added:
- Correct login detection (no false positives from "module=account&action=logout/view")
- Only requests page 2 when page 1 actually has a page 2 link
- Treats "logged_in_no_table" as NON-fatal (means logged in but 0 results/table missing)
- Keeps dynamic URL-per-item searching
- Prints lowest offer on the market (even if above limit)
- Telegram alert when lowest <= limit AND new low

Deps:
  pip install playwright requests beautifulsoup4
  python3 -m playwright install firefox
"""

import os
import re
import time
import html as htmllib
from urllib.parse import urlencode

import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

print("WATCHER VERSION: dynamic-url-per-item-2 (login-detect-fix + page2-detect)")

STATE_FILE = "uaro_storage_state.json"
POLL_SECONDS = 300  # 5 minutes

# Telegram (optional)
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""


def tg_send(text: str) -> None:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print(text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def build_url_page(item_name: str, page_no: int = 1) -> str:
    params = {
        "module": "merchant",
        "action": "vendors",
        "item_id": "",
        "name": item_name,          # dynamic query
        "type": "-1",
        "merchant_name": "",
        "vend_price_op": "eq",
        "vend_price": "",
    }
    base = "https://uaro.net/cp/?" + urlencode(params)
    if page_no <= 1:
        return base
    return base + f"&p={page_no}"


def classify(page_html: str) -> str:
    """
    Returns:
      ok                 -> logged in and vendors table exists
      logged_in_no_table -> logged in but table missing (often 0 results or markup change)
      cloudflare         -> CF interstitial
      login              -> actual login page
      recaptcha          -> recaptcha present
      unknown            -> none of the above
    """
    h = page_html.lower()

    # Strong logged-in markers from your page
    logged_in = ("you are currently logged in as" in h) or ("module=account&action=logout" in h)

    if logged_in:
        if "horizontal-table" in h:
            return "ok"
        return "logged_in_no_table"

    # Cloudflare / bot checks
    if "just a moment" in h or "cf-browser-verification" in h:
        return "cloudflare"

    # Real login page detection (specific!)
    if "module=account&action=login" in h:
        return "login"

    if "recaptcha" in h:
        return "recaptcha"

    return "unknown"


def get_title(page_html: str) -> str:
    m = re.search(r"<title>\s*(.*?)\s*</title>", page_html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "<no title>"


def has_page2(page_html: str) -> bool:
    """
    Detects whether pagination includes a link to page 2.
    Based on your markup:
      <a href="...&p=2" title="Page #2" class="page-num">2</a>
    """
    return ('title="Page #2"' in page_html) or ("&p=2" in page_html and "page-num" in page_html)


def parse_int_limit(s: str) -> int:
    cleaned = s.strip().replace(",", "").replace(" ", "")
    if not cleaned.isdigit():
        raise ValueError(f"Not a number: {s!r}")
    return int(cleaned)


def prompt_items_and_limits() -> dict[str, int]:
    print("\nEnter items and limits as pairs. Blank item name to finish.")
    print("Example:\n  Survivor's Manteau\n  150000\n  Vali's Manteau\n  400000\n")

    items: dict[str, int] = {}
    while True:
        name = input("Item name (blank to finish): ").strip()
        if not name:
            break
        limit_raw = input("Limit (z): ").strip()
        limit_val = parse_int_limit(limit_raw)
        items[name] = limit_val
        print(f"Added: {name!r} <= {limit_val:,} z\n")

    if not items:
        raise SystemExit("No items provided. Exiting.")
    return items


def normalize_text(s: str) -> str:
    s = htmllib.unescape(s or "")
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_price_to_int(price_text: str) -> int | None:
    m = re.search(r"([\d,]+)\s*z", price_text or "")
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def extract_offers(page_html: str) -> list[dict]:
    """
    Extract offers from the vendors table.
    Returns list of dicts with keys: item_text, price, merchant, shop, position
    """
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.select_one("table.horizontal-table")
    if not table:
        return []

    offers = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue

        merchant = normalize_text(tds[0].get_text(" ", strip=True))
        shop = normalize_text(tds[1].get_text(" ", strip=True))
        position = normalize_text(tds[2].get_text(" ", strip=True))
        item_text = normalize_text(tds[4].get_text(" ", strip=True))
        price_text = normalize_text(tds[6].get_text(" ", strip=True))

        price_val = parse_price_to_int(price_text)
        if price_val is None:
            continue

        offers.append(
            {
                "item_text": item_text,
                "price": price_val,
                "merchant": merchant,
                "shop": shop,
                "position": position,
            }
        )
    return offers


def auth_fail(page_html: str, url: str, status: str) -> None:
    title = get_title(page_html)
    print("\n--- AUTH DEBUG ---")
    print("URL:", url)
    print("Status:", status)
    print("Title:", title)
    print("HTML snippet:", page_html[:500].replace("\n", " "))
    print("--- /AUTH DEBUG ---\n")

    if status == "cloudflare":
        tg_send("⚠ UARO: Cloudflare challenge/interstitial detected. Re-login/solve challenge.")
    elif status == "recaptcha":
        tg_send("⚠ UARO: reCAPTCHA detected. Re-login/solve challenge.")
    elif status == "login":
        tg_send("⚠ UARO: Actual login page detected / session expired. Re-login to refresh state.")
    else:
        tg_send(f"⚠ UARO: Unexpected page (status={status}, title={title}).")


def main():
    watch = prompt_items_and_limits()  # item -> limit
    last_notified: dict[str, int] = {}  # item -> last alerted price

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        while True:
            try:
                best_offer: dict[str, dict | None] = {item: None for item in watch.keys()}

                for item in watch.keys():
                    # Page 1 always
                    url1 = build_url_page(item, 1)
                    page.goto(url1, wait_until="domcontentloaded")
                    html1 = page.content()

                    status1 = classify(html1)
                    if status1 in ("cloudflare", "recaptcha", "login", "unknown"):
                        auth_fail(html1, url1, status1)
                        raise SystemExit(2)

                    # status1 is ok or logged_in_no_table
                    for off in extract_offers(html1):
                        cur = best_offer[item]
                        if cur is None or off["price"] < cur["price"]:
                            best_offer[item] = off

                    # Only fetch page 2 if it exists
                    if has_page2(html1):
                        url2 = build_url_page(item, 2)
                        page.goto(url2, wait_until="domcontentloaded")
                        html2 = page.content()

                        status2 = classify(html2)
                        if status2 in ("cloudflare", "recaptcha", "login", "unknown"):
                            auth_fail(html2, url2, status2)
                            raise SystemExit(2)

                        for off in extract_offers(html2):
                            cur = best_offer[item]
                            if cur is None or off["price"] < cur["price"]:
                                best_offer[item] = off

                # Report + alert
                for item, limit in watch.items():
                    off = best_offer[item]
                    if off is None:
                        print(f"[MISS] {item} -> no offers found on the market (0 results)")
                        continue

                    price = off["price"]
                    print(
                        f"[LOWEST] {item}: {price:,} z (limit {limit:,} z) | "
                        f"{off['merchant']} | {off['shop']} | {off['position']}"
                    )

                    under = price <= limit
                    new_low = (item not in last_notified) or (price < last_notified[item])

                    if under and new_low:
                        tg_send(
                            f"✅ Price alert: {item}\n"
                            f"Lowest on market: {price:,} z (limit {limit:,} z)\n"
                            f"{off['merchant']} | {off['shop']} | {off['position']}\n"
                            f"{build_url_page(item, 1)}"
                        )
                        last_notified[item] = price

            except SystemExit:
                context.close()
                browser.close()
                return
            except Exception as e:
                print("Error:", repr(e))

            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

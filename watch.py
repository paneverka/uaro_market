#!/usr/bin/env python3
"""
watch_uaro_playwright.py

- Prompts user for item name or item ID, plus price limit
- If input is numeric -> search by item_id
- If input is not numeric -> search by name
- Reads Telegram bot token and chat id from telegram_bot.txt
- Parses UARO vendors page with BeautifulSoup
- Prints lowest market ad for each watched item
- Sends Telegram alert when lowest price is below the set limit
"""

import os
import re
import time
import html as htmllib
from urllib.parse import urlencode

import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

print("WATCHER VERSION: dynamic-search-id-or-name-1")

STATE_FILE = "uaro_storage_state.json"
POLL_SECONDS = 300  # 5 minutes
TELEGRAM_FILE = "telegram_bot.txt"


def load_telegram_config(filepath: str) -> tuple[str, str]:
    """
    Reads telegram_bot.txt:
      line 1 -> bot token
      line 2 -> chat id
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return "", ""

    if len(lines) < 2:
        print(f"Warning: {filepath} must contain 2 lines: bot token and chat id")
        return "", ""

    return lines[0], lines[1]


TG_BOT_TOKEN, TG_CHAT_ID = load_telegram_config(TELEGRAM_FILE)


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


def classify(page_html: str) -> str:
    h = page_html.lower()

    logged_in = ("you are currently logged in as" in h) or ("module=account&action=logout" in h)
    if logged_in:
        if "horizontal-table" in h:
            return "ok"
        return "logged_in_no_table"

    if "just a moment" in h or "cf-browser-verification" in h:
        return "cloudflare"

    if "module=account&action=login" in h:
        return "login"

    if "recaptcha" in h:
        return "recaptcha"

    return "unknown"


def get_title(page_html: str) -> str:
    m = re.search(r"<title>\s*(.*?)\s*</title>", page_html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "<no title>"


def has_page2(page_html: str) -> bool:
    return ('title="Page #2"' in page_html) or ("&p=2" in page_html and "page-num" in page_html)


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


def parse_int_limit(s: str) -> int:
    cleaned = s.strip().replace(",", "").replace(" ", "")
    if not cleaned.isdigit():
        raise ValueError(f"Not a number: {s!r}")
    return int(cleaned)


def is_numeric_search(value: str) -> bool:
    return value.strip().isdigit()


def build_url_page(search_value: str, page_no: int = 1) -> str:
    """
    If search_value is numeric -> use item_id
    Else -> use name
    """
    search_value = search_value.strip()

    if is_numeric_search(search_value):
        item_id = search_value
        name = ""
    else:
        item_id = ""
        name = search_value

    params = {
        "module": "merchant",
        "action": "vendors",
        "item_id": item_id,
        "name": name,
        "type": "-1",
        "merchant_name": "",
        "vend_price_op": "eq",
        "vend_price": "",
    }

    base = "https://uaro.net/cp/?" + urlencode(params)
    if page_no <= 1:
        return base
    return base + f"&p={page_no}"


def prompt_items_and_limits() -> dict[str, dict]:
    """
    Returns:
      {
        "Goibne's Armor": {"search": "Goibne's Armor", "limit": 500000},
        "5124": {"search": "5124", "limit": 120000}
      }
    """
    print("\nEnter item name OR item ID, then limit. Blank input to finish.")
    print("Examples:")
    print("  Survivor's Manteau")
    print("  150000")
    print("  5124")
    print("  400000\n")

    items: dict[str, dict] = {}

    while True:
        search_value = input("Item name or item ID (blank to finish): ").strip()
        if not search_value:
            break

        limit_raw = input("Limit (z): ").strip()
        limit_val = parse_int_limit(limit_raw)

        items[search_value] = {
            "search": search_value,
            "limit": limit_val,
        }

        mode = "ID" if is_numeric_search(search_value) else "name"
        print(f"Added: {search_value!r} ({mode} search) <= {limit_val:,} z\n")

    if not items:
        raise SystemExit("No items provided. Exiting.")

    return items


def extract_offers(page_html: str) -> list[dict]:
    """
    Extract offers from vendors table.
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
    watch = prompt_items_and_limits()
    last_notified: dict[str, int] = {}

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        while True:
            try:
                best_offer: dict[str, dict | None] = {item: None for item in watch.keys()}

                for label, cfg in watch.items():
                    search_value = cfg["search"]

                    url1 = build_url_page(search_value, 1)
                    page.goto(url1, wait_until="domcontentloaded")
                    html1 = page.content()

                    status1 = classify(html1)
                    if status1 in ("cloudflare", "recaptcha", "login", "unknown"):
                        auth_fail(html1, url1, status1)
                        raise SystemExit(2)

                    for off in extract_offers(html1):
                        cur = best_offer[label]
                        if cur is None or off["price"] < cur["price"]:
                            best_offer[label] = off

                    if has_page2(html1):
                        url2 = build_url_page(search_value, 2)
                        page.goto(url2, wait_until="domcontentloaded")
                        html2 = page.content()

                        status2 = classify(html2)
                        if status2 in ("cloudflare", "recaptcha", "login", "unknown"):
                            auth_fail(html2, url2, status2)
                            raise SystemExit(2)

                        for off in extract_offers(html2):
                            cur = best_offer[label]
                            if cur is None or off["price"] < cur["price"]:
                                best_offer[label] = off

                for label, cfg in watch.items():
                    limit = cfg["limit"]
                    off = best_offer[label]

                    if off is None:
                        print(f"[MISS] {label} -> no offers found on the market (0 results)")
                        continue

                    price = off["price"]
                    print(
                        f"[LOWEST] {label}: {price:,} z (limit {limit:,} z) | "
                        f"{off['merchant']} | {off['shop']} | {off['position']}"
                    )

                    under = price <= limit
                    new_low = (label not in last_notified) or (price < last_notified[label])

                    if under and new_low:
                        tg_send(
                            f"✅ Price alert: {label}\n"
                            f"Lowest on market: {price:,} z (limit {limit:,} z)\n"
                            f"{off['merchant']} | {off['shop']} | {off['position']}\n"
                            f"{build_url_page(cfg['search'], 1)}"
                        )
                        last_notified[label] = price

            except SystemExit:
                context.close()
                browser.close()
                return
            except Exception as e:
                print("Error:", repr(e))

            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

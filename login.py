from playwright.sync_api import sync_playwright

STATE_FILE = "uaro_storage_state.json"
VENDORS_URL = "https://uaro.net/cp/?module=merchant&action=vendors&name=surv&type=-1&vend_price_op=eq&vend_price="

def main():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(VENDORS_URL, wait_until="domcontentloaded")

        print("Log in and solve the captcha/challenge if prompted.")
        print("Waiting until the page shows you're logged in...")

        # Wait up to 10 minutes for the logged-in marker
        page.wait_for_function(
            "() => document.body && document.body.innerText.includes('You are currently logged in as')",
            timeout=10 * 60 * 1000
        )

        context.storage_state(path=STATE_FILE)
        print(f"Saved session state to {STATE_FILE}")

        browser.close()

if __name__ == "__main__":
    main()

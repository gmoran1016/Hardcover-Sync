"""One-time setup: opens a visible browser so you can log in manually,
then saves session cookies for the sync app to reuse.

Run this once on your local machine before starting the sync:

    python setup_cookies.py

A Chrome window will open. Log in to each site, then come back here
and press Enter. Cookies are saved to the cookies/ folder.

In Docker, mount that folder as a volume so the container can read them:
    volumes:
      - ./cookies:/app/cookies
"""

import json
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

COOKIES_DIR = os.path.join(os.path.dirname(__file__), "cookies")
os.makedirs(COOKIES_DIR, exist_ok=True)


def create_visible_driver() -> webdriver.Chrome:
    options = Options()
    # NOT headless — user needs to see and interact with the browser
    options.add_argument("--window-size=1200,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    if chromedriver_path and os.path.exists(chromedriver_path):
        driver = webdriver.Chrome(service=Service(chromedriver_path), options=options)
    else:
        driver = webdriver.Chrome(options=options)

    return driver


def save_cookies(driver: webdriver.Chrome, filename: str) -> None:
    path = os.path.join(COOKIES_DIR, filename)
    cookies = driver.get_cookies()
    with open(path, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"  Saved {len(cookies)} cookies → {path}")


def setup_goodreads(driver: webdriver.Chrome) -> None:
    print("\n--- Goodreads ---")
    driver.get("https://www.goodreads.com/user/sign_in")
    print("Log in to Goodreads in the browser window that just opened.")
    input("Press Enter here once you are fully logged in and can see your home feed: ")

    # Confirm login succeeded
    if "goodreads.com" in driver.current_url and "sign_in" not in driver.current_url:
        save_cookies(driver, "goodreads.json")
        print("  Goodreads cookies saved successfully.")
    else:
        print(f"  WARNING: doesn't look like login succeeded (URL: {driver.current_url})")
        choice = input("  Save anyway? [y/N]: ")
        if choice.strip().lower() == "y":
            save_cookies(driver, "goodreads.json")


def setup_storygraph(driver: webdriver.Chrome) -> None:
    print("\n--- StoryGraph ---")
    driver.get("https://app.thestorygraph.com/users/sign_in")
    print("Log in to StoryGraph in the browser window.")
    input("Press Enter here once you are fully logged in: ")

    if "thestorygraph.com" in driver.current_url and "sign_in" not in driver.current_url:
        save_cookies(driver, "storygraph.json")
        print("  StoryGraph cookies saved successfully.")
    else:
        print(f"  WARNING: URL is {driver.current_url}")
        choice = input("  Save anyway? [y/N]: ")
        if choice.strip().lower() == "y":
            save_cookies(driver, "storygraph.json")


def main() -> None:
    print("Hardcover Sync — Cookie Setup")
    print("=" * 40)
    print("This script opens a visible browser so you can log in manually.")
    print("It saves session cookies that the sync app will reuse.")
    print()

    do_goodreads = input("Set up Goodreads cookies? [Y/n]: ").strip().lower() != "n"
    do_storygraph = input("Set up StoryGraph cookies? [y/N]: ").strip().lower() == "y"

    driver = create_visible_driver()
    try:
        if do_goodreads:
            setup_goodreads(driver)
        if do_storygraph:
            setup_storygraph(driver)
    finally:
        driver.quit()

    print()
    print("Done! Run 'python main.py' (or 'docker compose up -d') to start syncing.")


if __name__ == "__main__":
    main()

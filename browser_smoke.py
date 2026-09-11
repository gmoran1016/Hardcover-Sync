"""Offline Chrome smoke test used by CI inside the built image."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib protocol name
        body = b"<!doctype html><title>smoke</title><button id='ready'>ready</button>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, *_):
        # Chrome may close its favicon/socket request while the smoke server
        # is shutting down; that is not a failed render.
        pass


def main() -> None:
    server = _QuietThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = None
    try:
        options = Options()
        options.add_argument("--window-size=1200,800")
        options.add_argument("--disable-dev-shm-usage")
        if os.getenv("CHROME_NO_SANDBOX", "0").casefold() in {"1", "true", "yes"}:
            options.add_argument("--no-sandbox")
        binary = os.getenv("CHROME_BIN")
        if binary:
            options.binary_location = binary
        driver_path = os.getenv("CHROMEDRIVER_PATH")
        service = Service(driver_path) if driver_path else Service()
        browser = webdriver.Chrome(service=service, options=options)
        browser.set_page_load_timeout(10)
        browser.get(f"http://127.0.0.1:{server.server_port}/")
        if browser.find_element("id", "ready").text != "ready":
            raise RuntimeError("local Chrome page did not render")
        print("browser smoke: PASS", flush=True)
    finally:
        if browser is not None:
            browser.quit()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

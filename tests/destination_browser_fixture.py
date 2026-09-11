"""Offline destination contracts using real Chrome and an isolated HTTP store.

Enable with HARDCOVER_BROWSER_TESTS=1. No login or provider requests are made;
the wrapper maps supported provider navigations onto this local fixture server.
"""

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

import goodreads
import storygraph


BOOK = {
    "id": "7",
    "title": "Dune",
    "author": "Frank Herbert",
    "progress_pages": 100,
    "progress_percent": 20.0,
    "total_pages": 500,
}
GR_URL = "https://www.goodreads.com/book/show/1"
SG_URL = "https://app.thestorygraph.com/books/dune"


class DestinationStore:
    def __init__(self):
        self.books = {
            "1": {
                "title": "Dune",
                "author": "Frank Herbert",
                "pages": 75,
                "percentage": 15,
                "status": "currently reading",
            },
            "2": {
                "title": "Dune Messiah",
                "author": "Frank Herbert",
                "pages": 30,
                "percentage": 6,
                "status": "currently reading",
            },
            "3": {
                "title": "Dune",
                "author": "Jane Austen",
                "pages": 10,
                "percentage": 2,
                "status": "currently reading",
            },
        }
        self.widgets = ["2", "3", "1"]
        self.failed_save = False
        self.failed_finish = False
        self.optimistic_save = False
        self.mutations = []
        self.opens = []

    def mutate(self, data):
        self.mutations.append(data)
        success = not (
            self.failed_finish if data["action"] == "finish" else self.failed_save
        )
        if success:
            book = self.books[data["id"]]
            if data["action"] == "finish":
                book["status"] = "read"
            else:
                book[data["unit"]] = int(data["value"])
        return {"success": success}

    def page(self, path):
        common = """<!doctype html><meta charset='utf-8'><style>body{font:16px Arial;padding:20px} .card{padding:12px} button,input,select{padding:8px} [hidden]{display:none!important}</style>
        <script>async function save(data){let response=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return (await response.json()).success}</script>"""
        if path.startswith("/gr/book/"):
            item = self.books[path.rsplit("/", 1)[1]]
            return (
                common
                + f"<h1>{html.escape(item['title'])}</h1><p>{html.escape(item['author'])}</p><button class='Button--block'>{html.escape(item['status'].title())}</button>"
            )
        if path.startswith("/gr/home"):
            cards = []
            for identity in self.widgets:
                book = self.books[identity]
                if book["status"] != "currently reading":
                    continue
                cards.append(
                    f"<div class='card'><a href='https://www.goodreads.com/book/show/{identity}.Edition'>{html.escape(book['title'])}</a><p>by {html.escape(book['author'])}</p><button onclick=\"openProgress('{identity}')\">Update progress</button></div>"
                )
            return (
                common
                + "".join(cards)
                + """
            <div id='popup' hidden><button onclick="unit='pages';loadValue()">#</button><button onclick="unit='percentage';loadValue()">%</button>
            <input class='updateReadingProgress__headerInput' type='number'>
            <button class='longTextPopupForm__submitButton' onclick='submitProgress()'>Save</button>
            <button onclick='finishBook()'>I'm finished!</button></div><p id='error' hidden>Save failed</p>
            <script>
            const books=BOOKS; let selected=null,unit='pages';
            function loadValue(){document.querySelector('input').value=books[selected][unit]}
            function openProgress(id){selected=id;document.querySelector('#popup').hidden=false;loadValue()}
            async function submitProgress(){await save({id:selected,action:'progress',unit,value:document.querySelector('input').value});document.querySelector('#popup').hidden=true;document.querySelector('#error').hidden=false}
            async function finishBook(){await save({id:selected,action:'finish'});document.querySelector('#popup').replaceChildren();document.querySelector('#error').hidden=false}
            </script>""".replace("BOOKS", json.dumps(self.books))
            )
        if path.startswith("/sg/book"):
            book = self.books["1"]
            return (
                common
                + f"<h1>Dune</h1><p class='read-status-label'>{book['status']}</p>"
                + """
            <button class='expand-dropdown-button'>Currently reading</button>
            <button class='track-progress-button' onclick="document.querySelector('#progress').hidden=false">Track progress</button>
            <button class='mark-as-finished-btn' onclick='finishBook(this)'>Mark as finished</button>
            <form id='progress' hidden onsubmit='return false'>
              <select id='read_status_progress_type' onchange="document.querySelector('#read_status_progress_number').max=this.value==='pages'?'624':'100'">
                <option value='percentage'>Percentage</option><option value='pages'>Pages</option>
              </select>
              <input type='number' id='read_status_progress_number' value='PAGES' min='0' max='100'>
              <input type='hidden' class='read-status-book-num-of-pages' value='624'>
              <input type='hidden' class='read-status-last-reached-pages' value='PAGES'>
              <input type='hidden' class='read-status-last-reached-percent' value='PERCENT'>
              <input type='button' class='progress-tracker-update-button' value='Save' onclick='submitProgress()'>
            </form><p id='error' hidden>Save failed</p><script>
            async function submitProgress(){let unit=document.querySelector('select').value,value=document.querySelector('#read_status_progress_number').value;let ok=await save({id:'1',action:'progress',unit,value});if(ok||OPTIMISTIC){document.querySelector(unit==='pages'?'.read-status-last-reached-pages':'.read-status-last-reached-percent').value=value}document.querySelector('#progress').hidden=true;document.querySelector('#error').hidden=!ok}
            async function finishBook(button){let ok=await save({id:'1',action:'finish'});if(ok){document.querySelector('.read-status-label').textContent='read'}button.replaceWith(button.cloneNode(true));document.querySelector('#error').hidden=!ok}
            </script>""".replace("PAGES", str(book["pages"]))
                .replace("PERCENT", str(book["percentage"]))
                .replace("OPTIMISTIC", json.dumps(self.optimistic_save))
            )
        raise AssertionError(f"Unexpected local route {path}")


class LocalDestinationDriver:
    def __init__(self, browser, server, store):
        self.browser = browser
        self.server = server
        self.store = store
        self.current_url = ""

    def get(self, url):
        parsed = urlsplit(url)
        if parsed.hostname in (
            "goodreads.com",
            "www.goodreads.com",
        ) and parsed.path in ("", "/"):
            path = "/gr/home"
        elif parsed.hostname in (
            "goodreads.com",
            "www.goodreads.com",
        ) and parsed.path.startswith("/book/show/"):
            identity = parsed.path.rsplit("/", 1)[1].split(".")[0].split("-")[0]
            path = f"/gr/book/{identity}"
        elif parsed.hostname == "app.thestorygraph.com" and parsed.path.startswith(
            "/books/"
        ):
            path = "/sg/book"
        else:
            raise AssertionError(f"Provider navigation blocked by offline test: {url}")
        self.store.opens.append(url)
        self.browser.get(f"http://127.0.0.1:{self.server.server_port}{path}")
        self.current_url = url

    def refresh(self):
        self.get(self.current_url)

    def __getattr__(self, name):
        return getattr(self.browser, name)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, *_):
        # Chrome can close an in-flight request during teardown; it is
        # irrelevant to the destination contract.
        pass


@unittest.skipUnless(
    os.environ.get("HARDCOVER_BROWSER_TESTS") == "1",
    "set HARDCOVER_BROWSER_TESTS=1 for local Chrome contracts",
)
class DestinationBrowserCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = webdriver.ChromeOptions()
        for argument in (
            "--headless=new",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--no-proxy-server",
        ):
            options.add_argument(argument)
        if os.environ.get("CHROME_NO_SANDBOX") == "1":
            options.add_argument("--no-sandbox")
        if os.environ.get("CHROME_BIN"):
            options.binary_location = os.environ["CHROME_BIN"]
        cls.browser = webdriver.Chrome(
            service=Service(executable_path=os.environ.get("CHROMEDRIVER_PATH")),
            options=options,
        )
        cls.browser.execute_cdp_cmd("Network.enable", {})
        cls.browser.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["https://*"]})

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()

    def setUp(self):
        self.store = DestinationStore()
        store = self.store

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/favicon.ico":
                    self.send_error(404)
                    return
                body = store.page(urlsplit(self.path).path).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/save":
                    self.send_error(404)
                    return
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps(store.mutate(data)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = QuietThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.driver = LocalDestinationDriver(self.browser, self.server, self.store)

        def bounded_wait(driver, timeout):
            return WebDriverWait(driver, min(timeout, 0.6), poll_frequency=0.02)

        for module in (goodreads, storygraph):
            self.enterContext(patch.object(module.time, "sleep"))
            self.enterContext(patch.object(module, "WebDriverWait", bounded_wait))

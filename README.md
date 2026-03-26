# Hardcover Sync

Automatically syncs your reading progress from [Hardcover](https://hardcover.app) to **Goodreads** (required) and **StoryGraph** (optional) on a configurable schedule.

Hardcover is always the source of truth. Progress is never written back.

---

## How it works

1. Every N minutes the app queries the Hardcover GraphQL API for your **Currently Reading** books and their page progress.
2. For each book it opens a headless Chrome session and logs in to Goodreads (and optionally StoryGraph), searches for the book, and updates the progress.

> Goodreads shut down their public API in 2020, so browser automation is the only available method.

---

## Quick start (Docker — recommended)

### 1. Clone the repo

```bash
git clone https://github.com/gmoran1016/Hardcover-Sync.git
cd Hardcover-Sync
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials:

| Variable | Required | Description |
|---|---|---|
| `HARDCOVER_API_KEY` | Yes | From [hardcover.app/settings](https://hardcover.app/settings) |
| `GOODREADS_EMAIL` | Yes | Your Goodreads login email |
| `GOODREADS_PASSWORD` | Yes | Your Goodreads password |
| `STORYGRAPH_EMAIL` | No | Your StoryGraph login email |
| `STORYGRAPH_PASSWORD` | No | Your StoryGraph password |
| `SYNC_INTERVAL_MINUTES` | No | How often to sync (default: `30`) |

### 3. Build and run

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f
```

Stop:

```bash
docker compose down
```

---

## Running locally (without Docker)

Requires Python 3.11+ and Google Chrome (or Chromium) installed.

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your credentials
python main.py
```

---

## Project structure

```
main.py          - Entry point and sync loop
hardcover.py     - Hardcover GraphQL API client
goodreads.py     - Goodreads Selenium automation
storygraph.py    - StoryGraph Selenium automation
driver.py        - Shared Chrome WebDriver factory
requirements.txt
Dockerfile
docker-compose.yml
.env.example
```

---

## Troubleshooting

### Login fails
- Double-check credentials in `.env`.
- Goodreads/StoryGraph may require solving a CAPTCHA on first login from a new IP. Log in once manually from the same machine/network, then retry.

### "Could not find book on Goodreads"
- The book title on Hardcover must be similar enough to the Goodreads title for the search to match. Very long subtitles or different editions can cause mismatches.

### "Could not locate a progress input field"
- Goodreads and StoryGraph occasionally redesign their HTML. If selectors stop working, inspect the page with browser DevTools and update the selectors in `goodreads.py` / `storygraph.py`.

### Docker: Chrome crashes
- Make sure `shm_size: "256mb"` is present in `docker-compose.yml` (it is by default).

---

## Notes

- Progress is synced but **shelf status is only changed** when a book needs to be moved to Currently Reading.
- StoryGraph sync is best-effort; if it fails, the Goodreads sync is unaffected.
- Credentials are stored only in your local `.env` file and are never logged.

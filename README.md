# Hardcover Sync

Automatically syncs your reading progress from [Hardcover](https://hardcover.app) to **Goodreads** (required) and **StoryGraph** (optional) on a configurable schedule.

Hardcover is always the source of truth — progress is never written back.

---

## How it works

1. Every N minutes the app queries the Hardcover GraphQL API for your **Currently Reading** books and their page progress.
2. For each book it opens a headless Chrome session, logs in to Goodreads (and optionally StoryGraph), and updates the progress.

> Goodreads and StoryGraph have no public API, so browser automation is the only available method.

> **Note on percentages:** Hardcover, Goodreads, and StoryGraph may show different completion percentages for the same page number if they have different editions of the book with different page counts. This is expected — the sync always pushes the absolute page number.

---

## Prerequisites — save cookies first

Because Goodreads uses Amazon's login infrastructure (which blocks headless browsers with CAPTCHAs), the app authenticates using saved browser cookies instead of a username/password login.

**Run this once on any machine that has a display (your PC, laptop, etc.):**

```bash
git clone https://github.com/gmoran1016/Hardcover-Sync.git
cd Hardcover-Sync
pip install -r requirements.txt
python setup_cookies.py
```

A Chrome window will open. Log in to Goodreads (and StoryGraph if you use it), then press Enter in the terminal. Cookies are saved to the `cookies/` folder.

You will need to re-run `setup_cookies.py` if you are ever logged out (typically every few weeks/months).

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
| `HARDCOVER_API_KEY` | Yes | From [hardcover.app/settings](https://hardcover.app/account) → API |
| `GOODREADS_EMAIL` | Yes | Your Goodreads login email |
| `GOODREADS_PASSWORD` | Yes | Your Goodreads password |
| `STORYGRAPH_EMAIL` | No | Your StoryGraph login email |
| `STORYGRAPH_PASSWORD` | No | Your StoryGraph password |
| `SYNC_INTERVAL_MINUTES` | No | How often to sync (default: `30`) |

### 3. Run setup_cookies.py (if you haven't already)

```bash
pip install -r requirements.txt
python setup_cookies.py
```

### 4. Build and run

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f
```

---

## Unraid installation

### Requirements
- Unraid 6.12+ with the **Compose Manager** plugin (Community Applications → search "Compose Manager")
- The `cookies/` folder saved from `setup_cookies.py` (run on another machine first — see Prerequisites above)

### Steps

**1. SSH into your Unraid server and create a folder for the app:**

```bash
mkdir -p /mnt/user/appdata/hardcover-sync/cookies
```

**2. Copy your cookies from the machine where you ran `setup_cookies.py`:**

```bash
# Run this on your local machine (not Unraid):
scp cookies/goodreads.json root@UNRAID_IP:/mnt/user/appdata/hardcover-sync/cookies/
scp cookies/storygraph.json root@UNRAID_IP:/mnt/user/appdata/hardcover-sync/cookies/
```

**3. In Unraid, go to the Docker tab → Compose Manager → Add New Stack**

Name it `hardcover-sync`, then paste this compose file:

```yaml
services:
  hardcover-sync:
    build:
      context: https://github.com/gmoran1016/Hardcover-Sync.git
    restart: unless-stopped
    shm_size: "256mb"
    environment:
      - HARDCOVER_API_KEY=your_hardcover_api_key_here
      - GOODREADS_EMAIL=you@example.com
      - GOODREADS_PASSWORD=your_password
      - STORYGRAPH_EMAIL=
      - STORYGRAPH_PASSWORD=
      - SYNC_INTERVAL_MINUTES=30
    volumes:
      - /mnt/user/appdata/hardcover-sync/cookies:/app/cookies
```

Replace the environment variable values with your actual credentials.

**4. Click Compose Up.**

Unraid will pull the source from GitHub, build the image, and start the container. This takes a few minutes on first run.

**5. View logs** in Unraid → Docker tab → click the container icon → Logs.

### Updating

To update to the latest version, SSH in and run:

```bash
cd /mnt/user/appdata/hardcover-sync   # or wherever Compose Manager stores it
docker compose build --no-cache
docker compose up -d
```

Or use Compose Manager's "Pull and Rebuild" button.

---

## Running locally (without Docker)

Requires Python 3.11+ and Google Chrome or Chromium installed.

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your credentials
python setup_cookies.py
python main.py
```

---

## Project structure

```
main.py           Entry point and sync loop
hardcover.py      Hardcover GraphQL API client
goodreads.py      Goodreads Selenium automation
storygraph.py     StoryGraph Selenium automation
driver.py         Shared Chrome WebDriver factory
setup_cookies.py  One-time interactive cookie setup
requirements.txt
Dockerfile
docker-compose.yml
.env.example
```

---

## Troubleshooting

### Cookies expired / login fails
Re-run `python setup_cookies.py` on a machine with a display and re-copy the `cookies/` folder to your server.

### Book not found
The book title on Hardcover must be close enough to the title on Goodreads/StoryGraph for the search to match. Very long subtitles or different editions can cause mismatches.

### Docker: Chrome crashes
Make sure `shm_size: "256mb"` is present in your compose file. Chrome needs more shared memory than Docker's 64 MB default.

### Percentages differ between platforms
Different platforms may have your book in a different edition with a different page count. This is expected — the sync pushes the absolute page number, and each platform calculates its own percentage.

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

Goodreads uses Amazon's login infrastructure which blocks headless browsers with a CAPTCHA, so the app authenticates via saved browser cookies rather than form login. StoryGraph does not have this restriction and will fall back to form login automatically if no cookies are found, but saving cookies for it is still recommended for reliability.

**Run this once on any machine that has a display (your PC, laptop, etc.):**

```bash
git clone https://github.com/gmoran1016/Hardcover-Sync.git
cd Hardcover-Sync
pip install -r requirements.txt
python setup_cookies.py
```

The script will ask which platforms to set up. A Chrome window opens — log in to each site, then press Enter in the terminal. Cookies are saved to the `cookies/` folder.

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

The Docker image is automatically built and published to GitHub Container Registry on every push to `main`. No plugins required — use Unraid's built-in Docker tab.

### Requirements
- The `cookies/` folder saved from `setup_cookies.py` (run on your PC first — see Prerequisites above)

### Steps

**1. SSH into your Unraid server and create the appdata folder:**

```bash
mkdir -p /mnt/user/appdata/hardcover-sync/cookies
```

**2. Copy your cookies from your PC to Unraid:**

```bash
# Run on your local PC:
scp cookies/goodreads.json root@UNRAID_IP:/mnt/user/appdata/hardcover-sync/cookies/
scp cookies/storygraph.json root@UNRAID_IP:/mnt/user/appdata/hardcover-sync/cookies/
```

**3. In Unraid → Docker tab → Add Container**, fill in:

| Field | Value |
|---|---|
| Name | `hardcover-sync` |
| Repository | `ghcr.io/gmoran1016/hardcover-sync:latest` |
| Network type | `bridge` |
| Extra Parameters | `--shm-size=256m --cap-add=SYS_ADMIN --restart=unless-stopped` |

Add these **Environment Variables** (click "+ Add another Path, Port, Variable, Label or Device"):

| Key | Value |
|---|---|
| `HARDCOVER_API_KEY` | Your Hardcover API key |
| `GOODREADS_EMAIL` | Your Goodreads email |
| `GOODREADS_PASSWORD` | Your Goodreads password |
| `STORYGRAPH_EMAIL` | Your StoryGraph email (or leave blank) |
| `STORYGRAPH_PASSWORD` | Your StoryGraph password (or leave blank) |
| `SYNC_INTERVAL_MINUTES` | `30` |

Add these **Volume mappings**:

| Container path | Host path |
|---|---|
| `/app/cookies` | `/mnt/user/appdata/hardcover-sync/cookies` |
| `/app/state` | `/mnt/user/appdata/hardcover-sync/state` |

> The `/app/state` mapping is optional but recommended — it stores sync state so the app can skip unchanged books between restarts. Without it, a harmless warning appears in the logs and every sync will push progress regardless of whether it changed.

**4. Click Apply.**

**5. View logs:** Docker tab → click the container icon → Logs.

### Updating

In the Unraid Docker tab, click the container icon → **Update** (or force re-pull with `docker pull ghcr.io/gmoran1016/hardcover-sync:latest` via SSH).

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
Two things are required:
- `shm_size: "256mb"` in your compose file (or `--shm-size=256m` in Unraid). Chrome needs more shared memory than Docker's 64 MB default.
- `cap_add: SYS_ADMIN` (or `--cap-add=SYS_ADMIN` in Unraid). Chrome needs this capability as an alternative to `--no-sandbox` for its process isolation.

### Percentages differ between platforms
Different platforms may have your book in a different edition with a different page count. This is expected — the sync pushes the absolute page number, and each platform calculates its own percentage.

### "Could not save sync state: Permission denied"
The `/app/state` directory isn't writable. If using Docker Compose, make sure the `sync-state` named volume is defined (see `docker-compose.yml`). If using Unraid's Docker UI, add a path mapping for `/app/state` → `/mnt/user/appdata/hardcover-sync/state` and create that folder first with `mkdir -p /mnt/user/appdata/hardcover-sync/state`.

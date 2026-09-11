# Hardcover Sync

Automatically syncs your reading progress from [Hardcover](https://hardcover.app) to **Goodreads** and **StoryGraph** on a configurable schedule.

Hardcover is always the source of truth — progress is never written back.

---

## How it works

1. Every N minutes the app queries the Hardcover GraphQL API for your **Currently Reading** books and their page progress.
2. It compares that data with durable per-destination state, retaining failed updates for retry.
3. For each changed book it opens an automated Chrome session, logs in to each configured destination, and updates the progress.
4. Ambiguous search results are skipped instead of risking an update to the wrong book.

> Goodreads and StoryGraph have no public API, so browser automation is the only available method.

> **Note on percentages:** Hardcover, Goodreads, and StoryGraph may show different completion percentages for the same page number if they have different editions of the book with different page counts. The sync prefers the absolute page number, but StoryGraph falls back to the Hardcover percentage when that page number is outside the matched progress field's allowed range.

---

## Prerequisites — save cookies first

Goodreads uses Amazon's login infrastructure which blocks headless browsers with a CAPTCHA, so the app authenticates via saved browser cookies rather than form login. StoryGraph does not have this restriction and will fall back to form login automatically if no cookies are found, but saving cookies for it is still recommended for reliability.

**Run this once on any machine that has a display (your PC, laptop, etc.):**

```bash
git clone https://github.com/gmoran1016/Hardcover-Sync.git
cd Hardcover-Sync
pip install -r requirements.lock
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
| `GOODREADS_EMAIL` | No | Goodreads login email; only needed for form-login fallback |
| `GOODREADS_PASSWORD` | No | Goodreads password; only needed for form-login fallback |
| `STORYGRAPH_EMAIL` | No | Your StoryGraph login email |
| `STORYGRAPH_PASSWORD` | No | Your StoryGraph password |
| `SYNC_INTERVAL_MINUTES` | No | How often to sync (default: `30`) |

A destination is enabled when its explicit `*_ENABLED` switch is true (the
default) and its cookie file exists or both credential variables are configured.
Cookie-only operation is recommended.

### 3. Run setup_cookies.py (if you haven't already)

```bash
pip install -r requirements.lock
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
mkdir -p /mnt/user/appdata/hardcover-sync/cookies \
  /mnt/user/appdata/hardcover-sync/state
# The image runs as UID/GID 1000 (appuser), so make the bind mount writable.
chown -R 1000:1000 /mnt/user/appdata/hardcover-sync/state
touch /mnt/user/appdata/hardcover-sync/state/.write-test \
  && rm /mnt/user/appdata/hardcover-sync/state/.write-test
```

**2. Copy your cookies from your PC to Unraid:**

```bash
# Run on your local PC:
scp cookies/goodreads.json root@UNRAID_IP:/mnt/user/appdata/hardcover-sync/cookies/
scp cookies/storygraph.json root@UNRAID_IP:/mnt/user/appdata/hardcover-sync/cookies/

# Run on Unraid after copying; the container reads as UID/GID 1000.
chown -R 1000:1000 /mnt/user/appdata/hardcover-sync/cookies
chmod 700 /mnt/user/appdata/hardcover-sync/cookies
find /mnt/user/appdata/hardcover-sync/cookies -maxdepth 1 -type f \
  -name '*.json' -exec chmod 600 {} +
```

**3. In Unraid → Docker tab → Add Container**, fill in:

| Field | Value |
|---|---|
| Name | `hardcover-sync` |
| Repository | `ghcr.io/gmoran1016/hardcover-sync:latest` |
| Network type | `bridge` |
| Extra Parameters | `--shm-size=256m --restart=unless-stopped` |

Add these **Environment Variables** (click "+ Add another Path, Port, Variable, Label or Device"):

| Key | Value |
|---|---|
| `HARDCOVER_API_KEY` | Your Hardcover API key |
| `GOODREADS_EMAIL` | Your Goodreads email |
| `GOODREADS_PASSWORD` | Your Goodreads password |
| `STORYGRAPH_EMAIL` | Your StoryGraph email (or leave blank) |
| `STORYGRAPH_PASSWORD` | Your StoryGraph password (or leave blank) |
| `SYNC_INTERVAL_MINUTES` | `30` |
| `STATE_FILE` | `/app/state/sync_state.json` |
| `GOODREADS_ENABLED` | `true` |
| `STORYGRAPH_ENABLED` | `true` |

Add these **Volume mappings**:

| Container path | Host path | Access mode |
|---|---|---|
| `/app/cookies` | `/mnt/user/appdata/hardcover-sync/cookies` | Read Only |
| `/app/state` | `/mnt/user/appdata/hardcover-sync/state` | Read/Write |

> The `/app/state` mapping is required for durable operation. It preserves
> destination mappings, pending finished books, and progress signatures when
> Unraid replaces the container during an update.

**4. Click Apply.**

**5. View logs:** Docker tab → click the container icon → Logs.

The first lines should appear immediately, before any network requests:

```text
[hardcover-sync-entrypoint] container starting
[hardcover-sync-entrypoint] virtual display is ready
Hardcover Sync v2.1.0 starting
```

If those entrypoint lines do not appear, Unraid is running an old image or has
a custom command/entrypoint override. Force-update the image, then edit the
container and clear any value in **Post Arguments** or custom entrypoint fields.

### Updating

In the Unraid Docker tab, click the container icon → **Update** (or force re-pull with `docker pull ghcr.io/gmoran1016/hardcover-sync:latest` via SSH).

---

## Running locally (without Docker)

Requires Python 3.11+ and Google Chrome or Chromium installed.

```bash
pip install -r requirements.lock
cp .env.example .env   # fill in your credentials
python setup_cookies.py
python main.py
```

Run one sync locally and exit:

```bash
python main.py --once
python main.py --once --dry-run
python main.py --status
```

Test Goodreads and StoryGraph authentication without changing progress:

```bash
python main.py --diagnose-auth

Inspect or repair persisted destination mappings without editing JSON by hand:

```bash
python main.py --mappings
python main.py --mapping-set goodreads 123 https://www.goodreads.com/book/show/123
python main.py --mapping-reset goodreads 123
```
```

Application logs are written to standard output so they appear consistently in
terminals and Docker log collectors.

---

## Project structure

```
main.py           Entry point and sync loop
config.py         Environment configuration validation
cookie_bundle.py  Cookie and browser identity persistence
matching.py       Destination search-result matching
sync_result.py    Per-destination sync outcomes
sync_state.py     Durable progress and destination state
container_entrypoint.py  Container startup and virtual display
hardcover.py      Hardcover GraphQL API client
goodreads.py      Goodreads Selenium automation
storygraph.py     StoryGraph Selenium automation
driver.py         Shared Chrome WebDriver factory
setup_cookies.py  One-time interactive cookie setup
browser_smoke.py  Offline Chrome image smoke test
tests/            Automated test suite
.github/workflows/docker-publish.yml  CI quality and Docker image validation
requirements.txt
requirements.lock
requirements-dev.txt
Dockerfile
docker-compose.yml
.env.example
```

---

## Development verification

Install the development environment and run all local quality gates:

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -v
python -m compileall -q .
```

Docker image validation runs in GitHub Actions. When Docker is installed, run
the same image build locally with `docker build -t hardcover-sync:test .`.

---

## Troubleshooting

### Cookies expired / login fails
Re-run `python setup_cookies.py` on a machine with a display and re-copy the `cookies/` folder to your server.

Cookie files created before v2.0.2 do not include the browser identity required
by Goodreads' current WAF checks. After upgrading to v2.0.2, run
`python setup_cookies.py` once more and replace `cookies/goodreads.json`.
The Docker image runs Chromium under a virtual display and replays that captured
identity rather than using Chrome's detectable headless mode.

### Book not found
The matcher requires a confident title/author result. Ambiguous results are
deliberately skipped and retried later rather than defaulting to the first
search result. Confirmed destination URLs are persisted in the state volume.

### StoryGraph remembers percentage mode
Version 2.0.10 explicitly switches StoryGraph's progress editor to pages before
submitting page-based Hardcover progress. This avoids browser validation errors
when StoryGraph remembers percentage mode from a previous update. If the matched
StoryGraph edition or its current progress state makes the absolute page value
invalid, the sync instead submits the source percentage. This covers both shorter
editions and destination progress fields whose minimum is ahead of the Hardcover
page count.
It also recognizes StoryGraph cards that show a series label before the title,
waits for late-rendered search results, and treats duplicate cards with identical
visible evidence as equivalent while retaining the ambiguity guard for different
near-matches.

### Docker: Chrome crashes
Keep `shm_size: "256mb"` in your compose file (or `--shm-size=256m` in
Unraid). Chrome needs more shared memory than Docker's 64 MB default. The
container does not require `SYS_ADMIN`; remove that capability when upgrading.
Each browser launch uses a fresh temporary Chrome profile/cache directory and
writes ChromeDriver startup diagnostics to
`/tmp/hardcover-sync-chromedriver.log` inside the container. If Chrome exits
before Selenium can attach, inspect that file with
`docker compose exec hardcover-sync tail -100 /tmp/hardcover-sync-chromedriver.log`.

The container entrypoint supervises the virtual display and exits if Xvfb dies,
allowing Docker's `unless-stopped` policy to restart a complete browser environment. It
also removes stale display lock/socket files at startup and uses `tini` to reap
orphaned Chromium processes.

### Percentages differ between platforms
Different platforms may have your book in a different edition with a different page count. This is expected — the sync pushes the absolute page number when it fits, and StoryGraph falls back to the source percentage when it does not.

The synchronizer only sees books that are in Hardcover's Currently Reading list
at a polling interval. A book started and finished entirely between polls cannot
be discovered by this integration.

### "Could not save sync state: Permission denied"
The `/app/state` directory isn't writable. If using Docker Compose, make sure the `sync-state` named volume is defined (see `docker-compose.yml`). If using Unraid's Docker UI, add a path mapping for `/app/state` → `/mnt/user/appdata/hardcover-sync/state`, then run:

```bash
mkdir -p /mnt/user/appdata/hardcover-sync/state
chown -R 1000:1000 /mnt/user/appdata/hardcover-sync/state
touch /mnt/user/appdata/hardcover-sync/state/.write-test \
  && rm /mnt/user/appdata/hardcover-sync/state/.write-test
```

### Reliability behavior

- Hardcover network, authentication, GraphQL, and malformed-response failures
  never replace the previous state with an empty library.
- Goodreads and StoryGraph track success independently, so a failed destination
  is retried even when Hardcover progress has not changed.
- Finished books remain queued until configured destinations acknowledge them.
- Finished markers are scoped to the Hardcover read instance, so a restarted
  read is synchronized as a new operation.
- State writes are atomic, serialized across processes, and retain a `.bak`
  copy of the previous state. A corrupt or unknown-version file stops safely;
  inspect it and run `python main.py --recover-state` to restore the validated
  backup.

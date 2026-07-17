# Browser Lifecycle and Sync Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship and deploy a reliability release that supervises Xvfb, reaps browser zombies, selects the canonical Goodreads result, recognizes already-read StoryGraph books, and persists Unraid sync state across container replacement.

**Architecture:** Debian `tini` becomes PID 1 and runs a Python supervisor that owns Xvfb and the sync application as direct children. Matching remains conservative but separates displayed title from metadata, while StoryGraph finish becomes idempotent by inspecting the current status before looking for an action button. Deployment migrates the current in-container state into a persistent Unraid bind mount before the image is replaced.

**Tech Stack:** Python 3.11, Selenium 4.46, `unittest`, Ruff, Docker, Debian Chromium/Xvfb/tini, GitHub Actions, Unraid Docker.

## Global Constraints

- Keep headed Chromium under Xvfb because the observed Goodreads session depends on its browser identity.
- Remove only `/tmp/.X<display>-lock` and `/tmp/.X11-unix/X<display>` display artifacts.
- Preserve conservative rejection for genuinely ambiguous book results.
- Do not crawl Goodreads editions in this release; select the canonical work.
- Do not print or copy secret environment values into logs or repository files.
- Preserve the existing cookie files and sync state throughout deployment.
- The deployed image revision label must equal the pushed Git commit.

## File Map

- `container_entrypoint.py`: stale-display cleanup, Xvfb readiness, application supervision, and child shutdown.
- `Dockerfile`: install and invoke `tini` as PID 1.
- `driver.py`: remove the deprecated Selenium option.
- `matching.py`: title-line extraction and weighted title/author scoring.
- `storygraph.py`: visible current-status detection and idempotent finish.
- `tests/test_container_entrypoint.py`: lifecycle unit tests.
- `tests/test_driver.py`: Chrome option regression test.
- `tests/test_matching.py`: live Goodreads candidate regression fixture.
- `tests/test_storygraph.py`: StoryGraph finish-state unit tests.
- `main.py`, `README.md`: version and operational documentation.

---

### Task 1: Supervise the Browser Runtime

**Files:**
- Modify: `container_entrypoint.py`
- Modify: `Dockerfile`
- Modify: `driver.py`
- Modify: `tests/test_container_entrypoint.py`
- Modify: `tests/test_driver.py`

**Interfaces:**
- Produces: `display_paths(display: str) -> tuple[Path, Path]`
- Produces: `clear_display_artifacts(display: str) -> None`
- Produces: `wait_for_display(process: subprocess.Popen, socket_path: Path, timeout: float = 5.0) -> None`
- Produces: `stop_process(process: subprocess.Popen, timeout: float = 10.0) -> None`
- Produces: `supervise(xvfb: subprocess.Popen, application: subprocess.Popen) -> int`
- Consumes: the existing `DISPLAY`, entrypoint command, and Docker restart policy.

- [ ] **Step 1: Write failing lifecycle and Chrome-option tests**

Add tests that express the required boundaries before implementation:

```python
def test_clear_display_artifacts_removes_stale_lock_and_socket(self):
    with tempfile.TemporaryDirectory() as directory:
        lock = Path(directory, ".X99-lock")
        socket = Path(directory, "X99")
        lock.touch()
        socket.touch()
        with patch.object(container_entrypoint, "display_paths", return_value=(lock, socket)):
            container_entrypoint.clear_display_artifacts(":99")
        self.assertFalse(lock.exists())
        self.assertFalse(socket.exists())

def test_wait_for_display_rejects_dead_xvfb(self):
    process = Mock()
    process.poll.return_value = 1
    with self.assertRaisesRegex(RuntimeError, "Xvfb exited with code 1"):
        container_entrypoint.wait_for_display(process, Path("missing"), timeout=0)

def test_supervise_returns_application_status_and_stops_xvfb(self):
    xvfb = Mock()
    xvfb.poll.return_value = None
    app = Mock()
    app.poll.return_value = 7
    with patch.object(container_entrypoint, "stop_process") as stop:
        status = container_entrypoint.supervise(xvfb, app)
    self.assertEqual(status, 7)
    stop.assert_called_once_with(xvfb)

def test_supervise_fails_and_stops_application_when_xvfb_dies(self):
    xvfb = Mock()
    xvfb.poll.return_value = 3
    app = Mock()
    app.poll.return_value = None
    with patch.object(container_entrypoint, "stop_process") as stop:
        with self.assertRaisesRegex(RuntimeError, "Xvfb exited with code 3"):
            container_entrypoint.supervise(xvfb, app)
    stop.assert_called_once_with(app)
```

Extend `test_build_options_uses_isolated_writable_chrome_dirs`:

```python
self.assertNotIn("useAutomationExtension", options.experimental_options)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_container_entrypoint tests.test_driver -v
```

Expected: lifecycle tests fail because the new functions do not exist, and the driver test fails because `useAutomationExtension` is present.

- [ ] **Step 3: Implement stale cleanup, readiness, and supervision**

Refactor `container_entrypoint.py` so `start_xvfb()` clears the two resolved artifacts, launches Xvfb, and calls `wait_for_display`. Launch the application with `subprocess.Popen(command)` and pass both children to `supervise`. `supervise` checks the application first, then Xvfb, sleeps for 100 ms only when both remain live, and stops the sibling whenever one exits. `stop_process` performs terminate/wait and escalates to kill/wait after its timeout.

Use this main control flow:

```python
xvfb = start_xvfb()
log("virtual display is ready")
log("launching application")
application = subprocess.Popen(command)
try:
    status = supervise(xvfb, application)
except RuntimeError as exc:
    log(f"fatal runtime error: {exc}")
    raise SystemExit(1) from exc
raise SystemExit(status)
```

Remove the module-level `X_SOCKET`, because paths must be recomputed from the selected display and cleaned at each startup.

- [ ] **Step 4: Install `tini`, update the entrypoint, and remove the deprecated option**

Add `tini` to the Docker apt packages and use:

```dockerfile
ENTRYPOINT ["/usr/bin/tini", "-g", "-s", "--", "python", "-u", "container_entrypoint.py"]
```

Delete this line from `driver.py`:

```python
options.add_experimental_option("useAutomationExtension", False)
```

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run:

```powershell
python -m unittest tests.test_container_entrypoint tests.test_driver -v
python -m ruff check container_entrypoint.py driver.py tests/test_container_entrypoint.py tests/test_driver.py
python -m ruff format --check container_entrypoint.py driver.py tests/test_container_entrypoint.py tests/test_driver.py
```

Expected: all focused tests pass and Ruff reports no issues.

- [ ] **Step 6: Commit the lifecycle change**

```powershell
git add container_entrypoint.py Dockerfile driver.py tests/test_container_entrypoint.py tests/test_driver.py
git commit -m "fix: supervise browser runtime"
```

---

### Task 2: Select the Canonical Goodreads Work

**Files:**
- Modify: `matching.py`
- Modify: `tests/test_matching.py`

**Interfaces:**
- Produces: `candidate_title(candidate_text: str) -> str`
- Updates: `result_score(title: str, author: str | None, candidate_text: str) -> float`
- Preserves: `choose_match(title, author, candidates, threshold=0.82, margin=0.05) -> str | None`

- [ ] **Step 1: Add the observed Goodreads regression fixture**

Add a test with the first five observed result rows and URLs. Assert that `choose_match("Crossroads of Twilight", "Robert Jordan", candidates)` equals the canonical URL ending in `113435.Crossroads_of_Twilight`.

Also add:

```python
def test_candidate_title_uses_first_nonempty_line(self):
    self.assertEqual(
        candidate_title("\nCrossroads of Twilight (The Wheel of Time, #10)\nby Robert Jordan"),
        "Crossroads of Twilight (The Wheel of Time, #10)",
    )
```

- [ ] **Step 2: Run the matching tests and confirm RED**

```powershell
python -m unittest tests.test_matching -v
```

Expected: the canonical-work test returns `None`, and the helper import/function is missing.

- [ ] **Step 3: Implement title-line and weighted scoring**

Implement:

```python
def candidate_title(candidate_text: str) -> str:
    return next((line.strip() for line in candidate_text.splitlines() if line.strip()), "")


def result_score(title: str, author: str | None, candidate_text: str) -> float:
    title_component = title_score(title, candidate_title(candidate_text))
    author_norm = normalise(author or "")
    candidate_norm = normalise(candidate_text)
    if not author_norm or author_norm == "unknown":
        return title_component
    author_component = 1.0 if author_norm in candidate_norm else 0.0
    return 0.92 * title_component + 0.08 * author_component
```

This keeps exact canonical title plus matching author at `1.0`, prevents unrelated rows from inheriting a near-perfect title score from metadata, and retains a greater-than-0.05 gap over the observed runner-up.

- [ ] **Step 4: Run focused tests and confirm GREEN**

```powershell
python -m unittest tests.test_matching -v
python -m ruff check matching.py tests/test_matching.py
python -m ruff format --check matching.py tests/test_matching.py
```

Expected: all matching tests pass with no Ruff issues.

- [ ] **Step 5: Commit the matching change**

```powershell
git add matching.py tests/test_matching.py
git commit -m "fix: select canonical Goodreads results"
```

---

### Task 3: Make StoryGraph Finish Idempotent

**Files:**
- Modify: `storygraph.py`
- Create: `tests/test_storygraph.py`

**Interfaces:**
- Produces: `current_reading_status(driver) -> str | None`
- Updates: `StorygraphSync.mark_finished(book, book_url=None) -> SyncResult`

- [ ] **Step 1: Add failing already-read, actionable, and missing-control tests**

Create narrow fake elements/drivers that implement only `get`, `find_elements`, `execute_script`, `is_displayed`, and `text`. Patch `storygraph.time.sleep` and `storygraph.WebDriverWait` so no real browser is required.

Required assertions:

```python
def test_mark_finished_succeeds_without_click_when_status_is_read(self):
    sync = StorygraphSync("", "")
    sync.driver = FakeDriver(status="read", finish_buttons=[])
    result = sync.mark_finished(BOOK, BOOK_URL)
    self.assertTrue(result.success)
    self.assertEqual(result.target_url, BOOK_URL)
    self.assertEqual(sync.driver.clicked, [])

def test_mark_finished_clicks_action_for_currently_reading(self):
    sync = StorygraphSync("", "")
    sync.driver = FakeDriver(status="currently reading", finish_buttons=[FakeElement("finished")])
    result = sync.mark_finished(BOOK, BOOK_URL)
    self.assertTrue(result.success)
    self.assertIn("finished", sync.driver.clicked)

def test_mark_finished_fails_when_not_read_and_action_is_missing(self):
    sync = StorygraphSync("", "")
    sync.driver = FakeDriver(status="currently reading", finish_buttons=[])
    result = sync.mark_finished(BOOK, BOOK_URL)
    self.assertFalse(result.success)
```

Add helper-level coverage proving hidden `read` labels are ignored and `rereading` is not equal to `read`.

- [ ] **Step 2: Run StoryGraph tests and confirm RED**

```powershell
python -m unittest tests.test_storygraph -v
```

Expected: tests fail because `current_reading_status` and the idempotent branch do not exist.

- [ ] **Step 3: Implement exact visible-status detection**

Add:

```python
def current_reading_status(driver) -> str | None:
    for label in driver.find_elements(By.CSS_SELECTOR, ".read-status-label"):
        if label.is_displayed():
            value = " ".join(label.text.casefold().split())
            if value:
                return value
    return None
```

Immediately after `self.driver.get(book_url)` settles, branch in `mark_finished`:

```python
if current_reading_status(self.driver) == "read":
    logger.info("StoryGraph: '%s' is already marked as read", title)
    return SyncResult.ok(book_url)
```

Leave the existing actionable finish flow unchanged for other statuses.

- [ ] **Step 4: Run focused tests and confirm GREEN**

```powershell
python -m unittest tests.test_storygraph -v
python -m ruff check storygraph.py tests/test_storygraph.py
python -m ruff format --check storygraph.py tests/test_storygraph.py
```

Expected: all StoryGraph tests pass with no Ruff issues.

- [ ] **Step 5: Commit the StoryGraph change**

```powershell
git add storygraph.py tests/test_storygraph.py
git commit -m "fix: recognize completed StoryGraph books"
```

---

### Task 4: Release Verification and Documentation

**Files:**
- Modify: `main.py`
- Modify: `README.md`

**Interfaces:**
- Updates: `VERSION = "2.0.7"`
- Documents: supervised Xvfb recovery, persistent Unraid state mount, and read-only cookies.

- [ ] **Step 1: Update release metadata and operations documentation**

Set the application version to `2.0.7`. Update the Unraid setup and Chrome-crash troubleshooting sections to require these mappings:

```text
/mnt/user/appdata/hardcover-sync/cookies -> /app/cookies (read-only)
/mnt/user/appdata/hardcover-sync/state   -> /app/state (read-write)
```

Explain that v2.0.7 supervises Xvfb and exits for Docker restart if the display dies.

- [ ] **Step 2: Run the complete local verification gate**

```powershell
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m compileall -q .
python -m unittest discover -v
```

Expected: every command exits 0 and the unit suite reports zero failures/errors.

- [ ] **Step 3: Build and smoke-test the Docker image where Docker is available**

On Luthien, copy the Git checkout to a validated temporary build directory, then run:

```sh
docker build -t hardcover-sync:prepublish .
docker run --rm hardcover-sync:prepublish python -u main.py --help
```

Expected output includes `container starting`, `virtual display is ready`, and argparse `usage:`. The container exits 0 and leaves no running test container.

- [ ] **Step 4: Commit the release metadata**

```powershell
git add main.py README.md
git commit -m "release: bump version to 2.0.7"
```

- [ ] **Step 5: Review and push**

```powershell
git status --short
git log --oneline origin/main..HEAD
git diff origin/main...HEAD --check
git push origin main
```

Expected: the worktree is clean before push and `main` advances successfully.

---

### Task 5: Publish and Deploy Safely to Luthien

**Files:**
- No repository files changed.
- Server state paths: `/mnt/user/appdata/hardcover-sync/state`, `/mnt/user/appdata/hardcover-sync/cookies`.

**Interfaces:**
- Consumes: GitHub Actions published `ghcr.io/gmoran1016/hardcover-sync:latest`.
- Produces: recreated `hardcover-sync` container with persistent state and read-only cookies.

- [ ] **Step 1: Wait for GitHub tests and image publishing**

Use GitHub CLI to wait for the pushed workflow:

```powershell
$runId = gh run list --workflow docker-publish.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --exit-status
```

Expected: both `test` and `publish` jobs succeed.

- [ ] **Step 2: Back up and migrate state before pulling**

Over SSH, resolve the current container ID and copy state without deleting the original:

```sh
install -d -m 700 -o 1000 -g 1000 /mnt/user/appdata/hardcover-sync/state
docker cp hardcover-sync:/app/state/sync_state.json /mnt/user/appdata/hardcover-sync/state/sync_state.json
docker cp hardcover-sync:/app/state/sync_state.json.bak /mnt/user/appdata/hardcover-sync/state/sync_state.json.bak 2>/dev/null || true
cp -a /mnt/user/appdata/hardcover-sync/state/sync_state.json "/mnt/user/appdata/hardcover-sync/state/sync_state.pre-2.0.7.$(date +%Y%m%d%H%M%S).json"
chown -R 1000:1000 /mnt/user/appdata/hardcover-sync/state
chmod 600 /mnt/user/appdata/hardcover-sync/state/*.json
```

Expected: the source and timestamped backup exist and parse as JSON.

- [ ] **Step 3: Capture configuration and pull the exact release**

Record `docker inspect hardcover-sync` to a root-only temporary file, pull latest, and verify `org.opencontainers.image.revision` equals local `git rev-parse HEAD`. Do not print `.Config.Env`.

```sh
umask 077
docker inspect hardcover-sync > /tmp/hardcover-sync.pre-2.0.7.inspect.json
docker pull ghcr.io/gmoran1016/hardcover-sync:latest
docker image inspect ghcr.io/gmoran1016/hardcover-sync:latest --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

- [ ] **Step 4: Recreate the Unraid container with preserved settings**

Generate a root-only environment file from the saved inspect JSON on Luthien, stop and rename the old container to `hardcover-sync-pre-2.0.7`, and create the new `hardcover-sync` with the observed `unless-stopped` restart policy, bridge network, 256 MB shared memory, and complete environment. Mount:

```text
/mnt/user/appdata/hardcover-sync/cookies:/app/cookies:ro
/mnt/user/appdata/hardcover-sync/state:/app/state:rw
```

Use these commands without printing the environment file:

```sh
jq -r '.[0].Config.Env[]' /tmp/hardcover-sync.pre-2.0.7.inspect.json > /tmp/hardcover-sync.2.0.7.env
chmod 600 /tmp/hardcover-sync.2.0.7.env
docker stop hardcover-sync
docker rename hardcover-sync hardcover-sync-pre-2.0.7
docker run -d \
  --name hardcover-sync \
  --restart unless-stopped \
  --network bridge \
  --shm-size 256m \
  --env-file /tmp/hardcover-sync.2.0.7.env \
  -v /mnt/user/appdata/hardcover-sync/cookies:/app/cookies:ro \
  -v /mnt/user/appdata/hardcover-sync/state:/app/state:rw \
  ghcr.io/gmoran1016/hardcover-sync:latest
shred -u /tmp/hardcover-sync.2.0.7.env
```

Do not remove the renamed old container until all verification steps pass. If Docker rejects the new configuration, run `docker rm -f hardcover-sync 2>/dev/null || true`, then `docker rename hardcover-sync-pre-2.0.7 hardcover-sync` and `docker start hardcover-sync`.

- [ ] **Step 5: Verify startup, authentication, synchronization, and reaping**

```sh
docker logs --tail=50 hardcover-sync
docker exec hardcover-sync python -u main.py --diagnose-auth
docker exec hardcover-sync python -u main.py --once
docker exec hardcover-sync ps -ef
```

Expected:

- startup logs report a ready virtual display;
- both authentication diagnostics pass;
- Goodreads saves Crossroads of Twilight progress;
- StoryGraph reports Winter's Heart already read and persists it as finished;
- no new defunct Chromium/crashpad processes appear after the two browser runs.

- [ ] **Step 6: Verify automatic recovery from Xvfb failure**

Capture the container ID, kill only Xvfb inside the container, and wait for Docker's restart policy:

```sh
before=$(docker inspect hardcover-sync --format '{{.State.StartedAt}}')
docker exec hardcover-sync sh -c 'pid=$(ps -ef | awk '\''$8 == "Xvfb" {print $2; exit}'\''); test -n "$pid"; kill "$pid"'
for i in $(seq 1 30); do
  after=$(docker inspect hardcover-sync --format '{{.State.StartedAt}}')
  [ "$after" != "$before" ] && break
  sleep 1
done
docker exec hardcover-sync sh -c 'ps -ef | grep "[X]vfb"'
```

Expected: `StartedAt` changes and a live Xvfb process is present after restart.

- [ ] **Step 7: Finalize or roll back**

On success, retain the old container stopped until the next successful scheduled sync, then remove it. On any failure, stop/remove the new container, rename `hardcover-sync-pre-2.0.7` back to `hardcover-sync`, restart it, and keep the migrated state backup for investigation.

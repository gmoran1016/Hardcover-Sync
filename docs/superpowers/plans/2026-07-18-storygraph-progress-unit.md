# StoryGraph Progress Unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make StoryGraph page-progress updates succeed when its progress editor opens in percentage mode.

**Architecture:** Resolve the visible progress input first, scope the unit selector and Save control to that input's form, and use Selenium's normal select/input interactions. Verify success from StoryGraph's saved-progress marker rather than assuming the editor disappears.

**Tech Stack:** Python 3.11+, Selenium 4, `unittest`, Docker, GitHub Actions, Unraid.

## Global Constraints

- Page progress must explicitly select StoryGraph's `pages` unit before entering the value.
- Percentage-only progress must explicitly select `percentage` and submit a whole percentage from 0 through 100.
- Input, unit selector, saved-state marker, and Save control must come from the same visible form.
- Browser validation must pass before Save is clicked.
- Success requires form disappearance or the corresponding saved-state marker changing to the requested value.
- Do not expose credentials, cookies, or template values in logs or command output.
- Preserve `net.unraid.docker.managed=dockerman` and the corrected disk7 mounts during deployment.

---

### Task 1: Add failing progress-form regression tests

**Files:**
- Modify: `tests/test_storygraph.py`

**Interfaces:**
- Consumes: `StorygraphSync._do_update_progress(pages, pct, total_pages)`.
- Produces: Fake visible/hidden StoryGraph forms and tests for unit selection, form scoping, validation, and saved-state success.

- [ ] **Step 1: Add reusable fake progress-form elements**

Add fakes that implement the Selenium methods used by the production code: `is_displayed()`, `find_element()`, `get_attribute()`, `clear()`, `send_keys()`, and `click()`. Model one hidden duplicate form and one visible form whose number input starts with `max="100"`, unit `percentage`, last-page marker `75`, and page-count marker `624`.

- [ ] **Step 2: Add the page-unit regression test**

Add `test_update_progress_selects_pages_before_entering_page_count`. Configure the fake driver so choosing `pages` changes the visible input bounds to `min="75"` and `max="624"`. Assert that `_do_update_progress(103, 12.4, 832)` selects `pages`, enters `103`, clicks only the visible form's Save control, and succeeds when the visible form's saved-page marker becomes `103` while the editor remains visible.

- [ ] **Step 3: Add percentage and failure tests**

Add these tests:

- `test_update_progress_selects_percentage_for_percentage_only_progress` expects a whole percentage value and the percentage saved marker.
- `test_update_progress_uses_controls_from_visible_form` asserts hidden duplicate controls are untouched.
- `test_update_progress_rejects_invalid_form_without_submitting` returns browser validity false and asserts Save is not clicked.
- `test_update_progress_fails_when_saved_state_does_not_change` leaves the marker unchanged and expects failure.

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_storygraph -v
```

Expected: the new tests fail because production code does not explicitly select the unit, scopes Save globally, and waits only for input invisibility.

### Task 2: Implement unit-aware progress submission

**Files:**
- Modify: `storygraph.py`
- Test: `tests/test_storygraph.py`

**Interfaces:**
- Consumes: Visible `input#read_status_progress_number` and its ancestor form.
- Produces: `_progress_request(pages, pct) -> tuple[str, str] | None`, `_visible_progress_form() -> tuple[WebElement, WebElement] | None`, and `_saved_progress_matches(unit, value) -> bool`.

- [ ] **Step 1: Add progress request normalization**

Implement `_progress_request` so a non-`None` page count returns `("pages", str(pages))`. Otherwise, a non-`None` percentage returns `("percentage", str(round(pct)))` clamped to 0 through 100. Return `None` when neither exists.

- [ ] **Step 2: Resolve one visible form**

Implement `_visible_progress_form` by iterating all matching progress inputs, selecting the first displayed input, and resolving `./ancestor::form[1]`. Return only that form and input; do not fall back to hidden duplicates.

- [ ] **Step 3: Configure and validate the form**

Within `_do_update_progress`, resolve the form-scoped `select#read_status_progress_type`, select the requested unit with `selenium.webdriver.support.ui.Select.select_by_value`, and wait until the select reports that value. Clear and enter the normalized value with WebElement methods. Use `arguments[0].checkValidity()` for the number input and form; if either is false, log the input's validation message and return `False` without submitting.

- [ ] **Step 4: Submit and verify saved state**

Resolve `input.progress-tracker-update-button` from the same form, click it, and wait until either no visible progress form remains or `_saved_progress_matches` sees `read_status_last_reached_pages`/`read_status_last_reached_percent` equal the requested value. On timeout, log the requested unit/value and return `False`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_storygraph -v
```

Expected: all StoryGraph tests pass.

- [ ] **Step 6: Run the complete local suite**

Run:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m compileall -q .
python -m unittest discover -v
```

Expected: every command exits zero.

- [ ] **Step 7: Commit the bug fix**

```powershell
git add storygraph.py tests/test_storygraph.py
git commit -m "fix: select StoryGraph progress units"
```

### Task 3: Prepare release 2.0.8

**Files:**
- Modify: `main.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Verified StoryGraph fix.
- Produces: Publishable version 2.0.8 documentation and image metadata.

- [ ] **Step 1: Bump the application version**

Change `VERSION = "2.0.7"` to `VERSION = "2.0.8"` in `main.py`.

- [ ] **Step 2: Document the progress-unit behavior**

Add a concise README troubleshooting note explaining that page-based source progress is submitted using StoryGraph's page unit even when the site remembers percentage mode.

- [ ] **Step 3: Re-run the complete verification suite**

Run the Ruff, format, compile, and full unittest commands from Task 2. Expected: all pass.

- [ ] **Step 4: Commit the release**

```powershell
git add main.py README.md docs/superpowers/specs/2026-07-18-storygraph-progress-unit-design.md docs/superpowers/plans/2026-07-18-storygraph-progress-unit.md
git commit -m "release: bump version to 2.0.8"
```

### Task 4: Publish and verify the image

**Files:**
- Read: `.github/workflows/*`

**Interfaces:**
- Consumes: Clean `main` at version 2.0.8.
- Produces: GitHub `main` and GHCR `latest` at the same revision.

- [ ] **Step 1: Verify outgoing scope**

Run `git status -sb`, `git log --oneline origin/main..HEAD`, and `git diff --stat origin/main..HEAD`. Confirm only the approved fix, tests, docs, and version bump are outgoing.

- [ ] **Step 2: Push main**

Run `git push origin main`. Expected: the version guard passes because 2.0.8 differs from origin.

- [ ] **Step 3: Wait for GitHub Actions**

Use `gh run list` and `gh run watch` for the pushed revision. Expected: test and publish jobs succeed, including Docker build and image publication.

- [ ] **Step 4: Verify GHCR revision**

On Unraid, pull `ghcr.io/gmoran1016/hardcover-sync:latest` and assert its `org.opencontainers.image.revision` label equals the pushed commit.

### Task 5: Deploy and complete both verification plans

**Files:**
- Read: `/boot/config/plugins/dockerMan/templates-user/my-hardcover-sync.xml`
- Read: `/mnt/disk7/appdata/hardcover-sync/state/sync_state.json`

**Interfaces:**
- Consumes: Published 2.0.8 image and repaired Unraid template.
- Produces: Unraid-managed 2.0.8 container with StoryGraph at 103 pages and zero failed destinations.

- [ ] **Step 1: Back up and recreate with rollback**

Inspect and back up the current container configuration, stop and retain it under a unique rollback name, then recreate `hardcover-sync` from the 2.0.8 image with the Docker Manager label, existing environment, bridge network, 256 MiB shared memory, restart policy, and corrected disk7 mounts. Restore the previous container automatically if startup validation fails.

- [ ] **Step 2: Verify authentication**

Run `docker exec hardcover-sync python -u main.py --diagnose-auth`. Expected: Goodreads PASS, StoryGraph PASS, overall PASS, and no legacy warning.

- [ ] **Step 3: Verify the live StoryGraph update**

Run `docker exec hardcover-sync python -u main.py --once`. Expected: StoryGraph reports progress saved at 103 pages and the sync summary contains zero failures.

- [ ] **Step 4: Verify persisted state and processes**

Assert StoryGraph's destination state for Hardcover book `12736110` is `103:12.379807692307692`, exactly one Xvfb process exists, zero zombie processes exist, and the container remains labeled `net.unraid.docker.managed=dockerman`.

- [ ] **Step 5: Complete the paused Unraid repair plan**

Re-run the canonical-template assertions, confirm exactly one active template, and record the repair backup and rollback container names. Leave backups and rollback containers intact.

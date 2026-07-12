# Project Reliability Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hardcover Sync safer and more dependable under malformed data, partial API responses, individual destination failures, and long-running unattended deployment.

**Architecture:** Preserve the existing module-level CLI architecture. Strengthen validation at the API, persistence, and cookie boundaries; keep orchestration in `main.py`; and isolate individual book operations so one unexpected Selenium error does not discard unrelated progress.

**Tech Stack:** Python 3.11+, `unittest`, Requests, Selenium, python-dotenv, Ruff, Docker Compose, GitHub Actions.

## Global Constraints

- Preserve Hardcover as the sole source of truth.
- Preserve existing environment variables, cookie bundle formats, schema-v2 state, Docker Compose usage, `--once`, and `--diagnose-auth`.
- Do not modify the user-owned untracked `.claude/` directory.
- Do not perform live Goodreads or StoryGraph mutations during verification.
- Every behavioral fix must begin with a focused test that fails for the expected reason.
- Do not add destinations, bidirectional sync, a web UI, analytics, or a new framework.

---

## File Structure

- `sync_state.py`: validates, normalizes, migrates, and atomically persists sync state.
- `hardcover.py`: validates Hardcover HTTP and GraphQL response boundaries.
- `main.py`: orchestrates source reconciliation and isolated destination operations.
- `cookie_bundle.py`: validates legacy and identity-aware cookie bundles without exposing session data.
- `requirements.txt`: pins verified runtime dependencies.
- `requirements-dev.txt`: pins repository-only verification tools.
- `.github/workflows/docker-publish.yml`: runs lint, tests, dependency checks, and the Docker build.
- `README.md`: documents complete project structure and verification commands.

### Task 1: Reject structurally invalid persisted state safely

**Files:**
- Modify: `sync_state.py:20-61`
- Test: `tests/test_sync_state.py`

**Interfaces:**
- Consumes: JSON-compatible objects returned by `json.load()`.
- Produces: `load_state(path: str) -> dict`, returning a normalized schema-v2 state or raising `StateError` for unsafe input.

- [ ] **Step 1: Add failing tests for non-object and malformed schema-v2 state**

```python
def test_non_object_state_raises_controlled_error(self):
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "state.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([], handle)
        with self.assertRaisesRegex(StateError, "top-level object"):
            load_state(path)

def test_invalid_schema_v2_collections_raise_controlled_error(self):
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "state.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 2, "destinations": []}, handle)
        with self.assertRaisesRegex(StateError, "destinations"):
            load_state(path)
```

- [ ] **Step 2: Run the focused tests and confirm the uncontrolled failures**

Run: `python -m unittest tests.test_sync_state.StateTests.test_non_object_state_raises_controlled_error tests.test_sync_state.StateTests.test_invalid_schema_v2_collections_raise_controlled_error -v`

Expected: FAIL because a list has no `.get()` method and invalid `destinations` cannot be normalized.

- [ ] **Step 3: Add explicit mapping validation and normalized copies**

```python
def _mapping(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise StateError(f"Sync state field '{field}' must be an object")
    return dict(value)


def _normalize_v2(data: dict) -> dict:
    state = empty_state()
    state["source_books"] = _mapping(data.get("source_books", {}), "source_books")
    state["pending_finished"] = _mapping(
        data.get("pending_finished", {}), "pending_finished"
    )
    destinations = _mapping(data.get("destinations", {}), "destinations")
    for name in ("goodreads", "storygraph"):
        destination = _mapping(destinations.get(name, {}), f"destinations.{name}")
        state["destinations"][name] = {
            "books": _mapping(destination.get("books", {}), f"destinations.{name}.books"),
            "mappings": _mapping(
                destination.get("mappings", {}), f"destinations.{name}.mappings"
            ),
        }
    return state
```

After `json.load()`, reject non-dicts with `StateError("Sync state must be a top-level object")`; call `_normalize_v2()` for schema version 2 and leave the legacy migration path intact.

- [ ] **Step 4: Verify focused and full state tests**

Run: `python -m unittest tests.test_sync_state -v`

Expected: all state tests pass.

- [ ] **Step 5: Commit the state-boundary fix**

```bash
git add sync_state.py tests/test_sync_state.py
git commit -m "fix: validate persisted sync state"
```

### Task 2: Treat malformed Hardcover payloads as controlled source failures

**Files:**
- Modify: `hardcover.py:82-175`
- Test: `tests/test_hardcover.py`

**Interfaces:**
- Consumes: HTTP JSON payloads from Hardcover GraphQL.
- Produces: `_request(...) -> dict`, `get_currently_reading(...) -> list[dict]`, and `get_book_statuses(...) -> dict[str, dict]`, all raising `HardcoverAPIError` rather than leaking shape exceptions.

- [ ] **Step 1: Add failing tests for non-object payloads and malformed list entries**

```python
def response_with(self, payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    session = Mock()
    session.post.return_value = response
    return patch("hardcover._session", return_value=session)

def test_non_object_payload_raises_controlled_error(self):
    with self.response_with([]):
        with self.assertRaisesRegex(HardcoverAPIError, "JSON object"):
            get_currently_reading("token")

def test_non_object_book_entry_raises_controlled_error(self):
    payload = {"data": {"me": [{"user_books": [None]}]}}
    with self.response_with(payload):
        with self.assertRaisesRegex(HardcoverAPIError, "book entry"):
            get_currently_reading("token")
```

- [ ] **Step 2: Run focused tests and verify the shape exceptions escape today**

Run: `python -m unittest tests.test_hardcover.HardcoverTests.test_non_object_payload_raises_controlled_error tests.test_hardcover.HardcoverTests.test_non_object_book_entry_raises_controlled_error -v`

Expected: FAIL with `AttributeError` from `.get()`.

- [ ] **Step 3: Validate every GraphQL container before dereferencing it**

```python
if not isinstance(payload, dict):
    raise HardcoverAPIError("Hardcover returned a non-object JSON payload")
if payload.get("errors"):
    raise HardcoverAPIError(f"Hardcover GraphQL errors: {payload['errors']}")

def _book_entry(user_book: dict) -> dict:
    if not isinstance(user_book, dict):
        raise HardcoverAPIError("Hardcover returned a malformed book entry")
    book = user_book.get("book") or {}
    if not isinstance(book, dict):
        raise HardcoverAPIError("Hardcover returned a malformed book object")
    reads = user_book.get("user_book_reads") or []
    if not isinstance(reads, list) or any(not isinstance(read, dict) for read in reads):
        raise HardcoverAPIError("Hardcover returned malformed reading progress")
```

Apply equivalent dict checks to `get_book_statuses()` entries before accessing them.

- [ ] **Step 4: Run the complete Hardcover client tests**

Run: `python -m unittest tests.test_hardcover -v`

Expected: all Hardcover tests pass.

- [ ] **Step 5: Commit the API-boundary fix**

```bash
git add hardcover.py tests/test_hardcover.py
git commit -m "fix: validate Hardcover response shapes"
```

### Task 3: Preserve unresolved source books for a later status retry

**Files:**
- Modify: `main.py:97-127`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: prior `source_books`, current Hardcover books, and a possibly partial status response.
- Produces: persisted `source_books` containing current books plus prior books whose latest status was not returned.

- [ ] **Step 1: Add a failing regression test for a partial status response**

```python
def test_missing_status_record_is_preserved_for_retry(self):
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "state.json")
        state = empty_state()
        state["source_books"]["7"] = BOOK
        save_state(path, state)
        with (
            patch.object(main, "get_currently_reading", return_value=[]),
            patch.object(main, "get_book_statuses", return_value={}),
            patch.object(main, "GoodreadsSync", FakeAdapter),
        ):
            main.run_sync(self.config(path))
        self.assertIn("7", load_state(path)["source_books"])
```

- [ ] **Step 2: Run the regression test and confirm the record is currently lost**

Run: `python -m unittest tests.test_main.MainTests.test_missing_status_record_is_preserved_for_retry -v`

Expected: FAIL because `state["source_books"]` is replaced by the empty current collection.

- [ ] **Step 3: Merge unresolved records back into the next source snapshot**

```python
resolved_missing = set(statuses)
unresolved_books = {
    key: book
    for key, book in previous_books.items()
    if key not in current_books
    and key not in state["pending_finished"]
    and key not in resolved_missing
}
state["source_books"] = {**unresolved_books, **current_books}
```

Log the number of unresolved records retained when the mapping is non-empty. Preserve the existing finished and non-finished status behavior.

- [ ] **Step 4: Verify orchestration tests**

Run: `python -m unittest tests.test_main -v`

Expected: all orchestration tests pass.

- [ ] **Step 5: Commit partial-response recovery**

```bash
git add main.py tests/test_main.py
git commit -m "fix: retry unresolved Hardcover statuses"
```

### Task 4: Isolate unexpected failures to one book operation

**Files:**
- Modify: `main.py:35-87`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: destination adapter methods returning `SyncResult`, legacy booleans, or raising exceptions.
- Produces: `_run_operation(name: str, action, book: dict, target_url: str | None) -> SyncResult`, always returning a controlled result.

- [ ] **Step 1: Add a failing regression test proving later books still sync**

```python
def test_destination_exception_does_not_block_later_books(self):
    class RaisingAdapter(FakeAdapter):
        def update_progress(self, book, _url=None):
            if book["id"] == "7":
                raise RuntimeError("browser tab crashed")
            return SyncResult.ok(f"https://example.test/{book['id']}")

    second = dict(BOOK, id="8", user_book_id=8, book_id=80, title="Foundation")
    destination = {"books": {}, "mappings": {}}
    counts = main._sync_destination(
        "Goodreads", RaisingAdapter(), destination, {"7": BOOK, "8": second}, {}
    )
    self.assertEqual(counts, (1, 1, 0))
    self.assertNotIn("7", destination["books"])
    self.assertIn("8", destination["books"])
```

- [ ] **Step 2: Run the regression test and confirm the first exception aborts the loop**

Run: `python -m unittest tests.test_main.MainTests.test_destination_exception_does_not_block_later_books -v`

Expected: ERROR with `RuntimeError: browser tab crashed`.

- [ ] **Step 3: Add the operation boundary and use it in both loops**

```python
def _run_operation(name: str, action, book: dict, target_url: str | None) -> SyncResult:
    try:
        return _coerce_result(action(book, target_url), target_url)
    except Exception as exc:
        logger.exception("%s operation crashed for '%s': %s", name, book["title"], exc)
        return SyncResult.failed("unexpected destination operation failure", target_url=target_url)
```

Replace direct `adapter.update_progress(...)` and `adapter.mark_finished(...)` calls with `_run_operation(...)`. Do not include exception text in the returned reason, preventing external browser errors from being persisted or repeated as user-facing secret-bearing data.

- [ ] **Step 4: Verify focused and full orchestration behavior**

Run: `python -m unittest tests.test_main -v`

Expected: all orchestration tests pass, including one success after one raised operation.

- [ ] **Step 5: Commit per-book isolation**

```bash
git add main.py tests/test_main.py
git commit -m "fix: isolate destination book failures"
```

### Task 5: Validate cookie records and prevent caller-owned mutation

**Files:**
- Modify: `cookie_bundle.py`
- Test: `tests/test_cookie_bundle.py`

**Interfaces:**
- Consumes: legacy cookie lists and schema-v1 identity bundles.
- Produces: `decode_cookie_bundle(data) -> CookieBundle` with a private list of dictionary cookie records.

- [ ] **Step 1: Add failing tests for malformed records and mutation isolation**

```python
def test_non_object_cookie_record_is_rejected(self):
    with self.assertRaisesRegex(ValueError, "cookie records"):
        decode_cookie_bundle(["session=secret"])

def test_decoded_cookies_do_not_alias_input(self):
    source = [{"name": "session", "value": "redacted", "sameSite": "Lax"}]
    bundle = decode_cookie_bundle(source)
    bundle.cookies[0].pop("sameSite")
    self.assertEqual(source[0]["sameSite"], "Lax")
```

- [ ] **Step 2: Run focused tests and confirm acceptance/aliasing**

Run: `python -m unittest tests.test_cookie_bundle.CookieBundleTests.test_non_object_cookie_record_is_rejected tests.test_cookie_bundle.CookieBundleTests.test_decoded_cookies_do_not_alias_input -v`

Expected: FAIL because arbitrary list elements are accepted and cookie dictionaries alias input data.

- [ ] **Step 3: Normalize validated cookie lists**

```python
def _cookies(value) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("cookie file must contain a list of cookie records")
    return [dict(item) for item in value]
```

Use `_cookies(data)` for legacy lists and `_cookies(data["cookies"])` for bundles. Keep unknown cookie keys for Selenium compatibility and preserve the existing identity fields.

- [ ] **Step 4: Verify cookie compatibility tests**

Run: `python -m unittest tests.test_cookie_bundle -v`

Expected: all legacy, identity-aware, malformed, and mutation-isolation tests pass.

- [ ] **Step 5: Commit cookie-boundary validation**

```bash
git add cookie_bundle.py tests/test_cookie_bundle.py
git commit -m "fix: validate cookie bundle records"
```

### Task 6: Modernize pinned dependencies and CI quality gates

**Files:**
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `.github/workflows/docker-publish.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: Python 3.11 and the public package indexes.
- Produces: reproducible runtime/dev environments and CI enforcement for tests, lint, dependency consistency, and Docker builds.

- [ ] **Step 1: Pin current runtime and verification dependencies**

```text
# requirements.txt
requests==2.34.2
selenium==4.46.0
python-dotenv==1.2.2
```

```text
# requirements-dev.txt
-r requirements.txt
ruff==0.15.21
```

- [ ] **Step 2: Install the exact declared environment and verify compatibility**

Run: `python -m pip install -r requirements-dev.txt`

Expected: installation succeeds on Python 3.11 with no resolver conflict.

Run: `python -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 3: Format tracked Python and add CI lint/format gates**

Run: `python -m ruff format .`

Expected: Python files are formatted without behavioral edits.

Add these steps after dependency installation in the test job:

```yaml
      - run: pip install -r requirements-dev.txt
      - run: python -m pip check
      - run: python -m ruff check .
      - run: python -m ruff format --check .
```

Replace the existing runtime-only `pip install` step so dependencies are installed once.

- [ ] **Step 4: Update contributor-facing project and verification documentation**

Add `config.py`, `cookie_bundle.py`, `matching.py`, `sync_result.py`, `sync_state.py`, `container_entrypoint.py`, `tests/`, and `.github/workflows/docker-publish.yml` to the project structure. Add a development verification section containing:

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -v
python -m compileall -q .
```

State that Docker image validation runs in GitHub Actions and can be run locally with `docker build -t hardcover-sync:test .` when Docker is installed.

- [ ] **Step 5: Run all local quality gates**

Run: `python -m ruff check .`

Expected: `All checks passed!`

Run: `python -m ruff format --check .`

Expected: all Python files are already formatted.

Run: `python -m unittest discover -v`

Expected: all tests pass.

Run: `python -m compileall -q .`

Expected: exit code 0 and no output.

- [ ] **Step 6: Commit dependency, formatting, CI, and documentation updates**

```bash
git add requirements.txt requirements-dev.txt .github/workflows/docker-publish.yml README.md *.py tests/*.py
git commit -m "chore: modernize dependencies and quality gates"
```

### Task 7: Final security and deployment verification

**Files:**
- Modify only files implicated by a confirmed new finding, following a new red-green test cycle.
- Review: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`, `.env.example`, all Python source, and GitHub Actions.

**Interfaces:**
- Consumes: the completed working tree and exact dependency pins.
- Produces: final verification evidence and an explicit list of any environment-limited checks.

- [ ] **Step 1: Run repository secret and dangerous-pattern checks**

Run: `git grep -n -I -E '(HARDCOVER_API_KEY=.+|GOODREADS_PASSWORD=.+|STORYGRAPH_PASSWORD=.+|shell=True|eval\(|exec\()' -- ':!docs/superpowers/**' ':!.env.example'`

Expected: no committed credential values or unsafe execution patterns. `os.execvp` is reviewed separately as a fixed argv process handoff, not dynamic shell evaluation.

- [ ] **Step 2: Confirm ignored secret/state paths are not tracked**

Run: `git ls-files .env cookies state '*.log'`

Expected: no output.

- [ ] **Step 3: Run fresh complete verification**

Run: `python -m pip check && python -m ruff check . && python -m ruff format --check . && python -m unittest discover -v && python -m compileall -q .`

Expected: dependency consistency, lint, formatting, every test, and compilation all succeed with no failures.

- [ ] **Step 4: Validate deployment where tooling is available**

Run: `docker compose config --quiet && docker build -t hardcover-sync:test .`

Expected: exit code 0 for both commands. On this workstation Docker is not installed, so do not claim local Docker verification; rely on the unchanged GitHub Actions Docker build gate and report this limitation until CI runs.

- [ ] **Step 5: Inspect repository scope and final diff**

Run: `git status --short && git diff --check && git diff HEAD~6 --stat && git log --oneline -8`

Expected: only planned tracked files plus the pre-existing untracked `.claude/` directory; no whitespace errors; each task represented by a focused commit.

- [ ] **Step 6: Commit any final documentation-only correction**

If final verification changes documentation, commit only those files:

```bash
git add README.md
git commit -m "docs: finalize reliability review guidance"
```

If no files changed during final verification, do not create an empty commit.

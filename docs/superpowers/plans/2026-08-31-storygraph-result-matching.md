# StoryGraph Result Matching Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hardcover Sync reliably identify StoryGraph books when result cards place a series label before the book title and when search results render incrementally.

**Architecture:** Preserve the conservative shared matcher and Goodreads behavior while scoring every nonempty result-text line for the expected title and collapsing only tied candidates with identical normalized visible evidence. Make StoryGraph search wait for an unambiguous match instead of treating the first rendered card as the complete result set. Cover both behaviors with focused regression tests using the existing unittest/fake-driver patterns.

**Tech Stack:** Python 3.14, Selenium, unittest, Ruff.

**Spec:** The accepted scope is the user’s 2026-08-31 request to implement the confirmed StoryGraph matcher and dynamic-result improvements; the live reproduction showed `The Wheel of Time #11\\nKnife of Dreams\\nRobert Jordan` and an initially partial result list.

## Global Constraints

- Keep Goodreads matching behavior covered by the existing tests.
- Do not weaken the no-first-result fallback or the existing ambiguity margin.
- Keep StoryGraph search bounded by the existing 10-second wait.
- Do not change authentication, progress-form submission, state persistence, or version surfaces.
- Follow test-first development: each production behavior change gets a failing regression test before implementation.

### Task 1: Match titles on any StoryGraph result-text line

**Files:**
- Modify: `tests/test_matching.py`
- Modify: `matching.py:40-47`

**Interfaces:**
- Consumes: existing `choose_match(title, author, candidates)` API.
- Produces: `result_score` that can recognize an expected title on a later nonempty line while preserving author weighting and threshold/margin behavior.

- [x] **Step 1: Write the failing regression test**

```python
def test_choose_match_handles_storygraph_series_before_title(self):
    candidates = [
        (
            "The Wheel of Time #11\\nKnife of Dreams\\nRobert Jordan",
            "https://app.thestorygraph.com/books/knife-of-dreams",
        )
    ]
    self.assertEqual(
        choose_match("Knife of Dreams", "Robert Jordan", candidates),
        "https://app.thestorygraph.com/books/knife-of-dreams",
    )
```

- [x] **Step 2: Run the focused test and confirm the expected failure**

Run: `python -m unittest tests.test_matching.MatchingTests.test_choose_match_handles_storygraph_series_before_title -v`

Expected: FAIL because the current scorer uses only `The Wheel of Time #11` as the candidate title.

- [x] **Step 3: Implement the minimal scoring change**

Update `result_score` to calculate the title component from the highest-scoring nonempty line in `candidate_text`, falling back to `0.0` for empty text. Update `choose_match` to ignore only near-tied candidates whose normalized full visible evidence is identical, allowing duplicate StoryGraph records without weakening the threshold or rejecting genuinely different near-ties.

- [x] **Step 4: Run the focused matcher tests**

Run: `python -m unittest tests.test_matching -v`

Expected: all matcher tests pass, including the new StoryGraph regression and canonical Goodreads selection.

### Task 2: Wait for an unambiguous StoryGraph result

**Files:**
- Modify: `tests/test_storygraph.py`
- Modify: `storygraph.py:275-305`

**Interfaces:**
- Consumes: `StorygraphSync._search_book(title, author)` and the existing `_extract_href` helper.
- Produces: a bounded search that polls the current result elements until `choose_match` returns a valid URL, rather than accepting the first partially rendered result set.

- [x] **Step 1: Write the failing staged-render regression test**

Add fake search elements/driver behavior following the existing fake-driver style. Make the first poll expose only a nonmatching card and the second poll expose that card plus an exact title card. Patch `WebDriverWait` with a deterministic fake whose `until` invokes the predicate twice. Assert `_search_book("Knife of Dreams", "Robert Jordan")` returns the exact card URL and that both polls occurred.

- [x] **Step 2: Run the focused StoryGraph test and confirm the expected failure**

Run: `python -m unittest tests.test_storygraph.StorygraphTests.test_search_waits_for_late_unambiguous_result -v`

Expected: FAIL because the current implementation reads one presence snapshot and returns `None` before the exact card appears.

- [x] **Step 3: Implement the minimal bounded polling change**

Add a local search predicate in `_search_book` that collects the current selector matches, extracts `/books/` URLs, calls `choose_match`, and returns the URL only when a valid match exists. Pass that predicate to the existing 10-second `WebDriverWait`; log the existing no-match warning after timeout or an empty settled result. Do not add unbounded retries or alter URL extraction.

- [x] **Step 4: Run all StoryGraph and matching tests**

Run: `python -m unittest tests.test_matching tests.test_storygraph -v`

Expected: all focused tests pass.

### Task 3: Full verification and review

**Files:**
- Review: `matching.py`, `storygraph.py`, `tests/test_matching.py`, `tests/test_storygraph.py`

- [x] **Step 1: Inspect the diff and confirm scope**

Run: `git diff --check; git diff --stat; git status --short`

Expected: only the planned source/test files plus this plan document are changed; no whitespace errors.

- [x] **Step 2: Run the complete test suite**

Run: `python -m unittest discover -v`

Expected: zero failures.

- [x] **Step 3: Run lint, formatting, compilation, and Compose validation**

Run: `python -m ruff check .; python -m ruff format --check .; python -m compileall -q .; docker compose config`

Expected: every command exits successfully.

- [x] **Step 4: Review the final diff before reporting**

Run: `git diff -- matching.py storygraph.py tests/test_matching.py tests/test_storygraph.py`

Expected: the diff contains only the two requested runtime improvements and their regression coverage; no release/version bump is included.

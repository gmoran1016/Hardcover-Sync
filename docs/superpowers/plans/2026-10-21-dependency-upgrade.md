# Dependency Upgrade Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade every declared Python and GitHub Actions dependency to its latest stable version compatible with the project, then verify the pushed change in GitHub Actions.

**Architecture:** Keep the existing Python 3.11 runtime and exact-pin model. Refresh direct Python pins and the complete runtime lock from current upstream releases, then update workflow Actions to their latest stable releases. Pin Ruff's prior lint scope and Python-only file selection explicitly so upgrading Ruff does not silently expand CI checks. Use GitHub Actions for the Linux Docker build.

**Tech Stack:** Python 3.11, pip requirements files, Ruff, Docker, GitHub Actions.

**Spec:** User request in this thread.

## Global Constraints

- Preserve the supported Python 3.11 runtime.
- Preserve exact pins and the existing runtime/dev dependency split.
- Leave the untracked `output/` directory untouched and unstaged.
- Use the repository's GitHub Actions workflow to verify the pushed Linux Docker build.

## Review Focus

- Latest package releases must support Python 3.11 and the Linux image.
- The runtime lock must include a consistent complete dependency resolution.
- GitHub Action major releases must be compatible with hosted runners and existing workflow inputs.
- Only dependency, Ruff configuration, workflow, and plan files should enter the commit.
- Remote verification must target the exact pushed commit.

---

### Task 1: Upgrade Python runtime and development dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements.lock`
- Modify: `requirements-dev.txt`

- [x] Resolve the latest stable compatible package versions for Python 3.11.
- [x] Update direct pins and regenerate every runtime transitive pin.
- [x] Upgrade Ruff to its latest stable release.
- [x] Confirm the resulting dependency set is internally consistent.

### Task 2: Upgrade GitHub Actions dependencies

**Files:**
- Modify: `.github/workflows/docker-publish.yml`
- Create: `ruff.toml`

- [x] Verify current stable releases from the official action repositories.
- [x] Update each pinned action reference while preserving workflow behavior.
- [x] Preserve the prior lint rule set and Python-only formatting scope under the latest Ruff defaults.

### Task 3: Verify, commit, push, and inspect GitHub Actions

**Files:**
- Verify the complete scoped diff before staging.

- [x] Run dependency consistency, Ruff, formatting, compilation, and unit checks.
- [x] Validate Compose configuration; the local Docker engine is unavailable, so verify the image build through GitHub Actions.
- [ ] Commit only the plan, dependency, Ruff configuration, and workflow changes; push a branch and open a draft PR.
- [ ] Watch the workflow for the exact PR commit and report its test and Docker build results.

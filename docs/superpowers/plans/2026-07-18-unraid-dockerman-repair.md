# Unraid Docker Manager Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Unraid Edit and Update management for `hardcover-sync` while preserving its verified 2.0.7 runtime, cookies, state, and credentials.

**Architecture:** Treat Unraid's template XML and the Docker container label as one configuration unit. Capture root-only backups, derive the replacement from current inspected state without printing secrets, validate the candidate template before activation, then recreate the container with automatic rollback.

**Tech Stack:** Unraid Docker Manager, Docker CLI, POSIX shell, `jq`, Python XML parsing, SSH.

## Global Constraints

- Do not print environment values, template contents, or cookie contents.
- Preserve `ghcr.io/gmoran1016/hardcover-sync:latest` at revision `7306e099c6eb586386462d4020169d0d4f0b9f07`.
- Mount `/mnt/disk7/appdata/hardcover-sync/cookies` at `/app/cookies` read-only.
- Mount `/mnt/disk7/appdata/hardcover-sync/state` at `/app/state` read-write.
- Preserve bridge networking, 256 MiB shared memory, `unless-stopped`, and the existing environment.
- Keep root-only template/inspect backups and a stopped pre-repair container for rollback.

---

### Task 1: Capture the failing management condition and backups

**Files:**
- Read: `/boot/config/plugins/dockerMan/templates-user/my-hardcover-sync.xml`
- Read: `/boot/config/plugins/dockerMan/templates-user/my-Harcover_Sync.xml`
- Create: `/mnt/disk7/appdata/hardcover-sync/unraid-repair/<timestamp>/`

**Interfaces:**
- Consumes: Running `hardcover-sync` container and current Unraid templates.
- Produces: Root-only repair directory containing both XML files and `docker inspect` JSON.

- [ ] **Step 1: Run the pre-repair regression assertions**

Run over SSH without printing secrets:

```sh
test "$(docker inspect -f '{{index .Config.Labels "net.unraid.docker.managed"}}' hardcover-sync 2>/dev/null || true)" = dockerman
python - <<'PY'
import xml.etree.ElementTree as ET
p='/boot/config/plugins/dockerMan/templates-user/my-hardcover-sync.xml'
r=ET.parse(p).getroot()
assert r.findtext('Name') == 'hardcover-sync'
assert r.findtext('Repository') == 'ghcr.io/gmoran1016/hardcover-sync:latest'
paths={(c.get('Target'), c.get('Mode'), c.text) for c in r.findall('Config') if c.get('Type') == 'Path'}
assert ('/app/cookies', 'ro', '/mnt/disk7/appdata/hardcover-sync/cookies') in paths
assert ('/app/state', 'rw', '/mnt/disk7/appdata/hardcover-sync/state') in paths
PY
```

Expected: FAIL because the management label is absent; template path assertions would also fail.

- [ ] **Step 2: Resolve and validate backup targets**

Create `/mnt/disk7/appdata/hardcover-sync/unraid-repair/<UTC timestamp>`, verify its resolved path begins with `/mnt/disk7/appdata/hardcover-sync/unraid-repair/`, and set mode `700` with owner `root:root`.

- [ ] **Step 3: Save root-only backups**

Copy both template XML files into the repair directory, write `docker inspect hardcover-sync` to `hardcover-sync.inspect.json`, and set every backup file to mode `600`.

### Task 2: Build and validate the canonical template

**Files:**
- Modify: `/boot/config/plugins/dockerMan/templates-user/my-hardcover-sync.xml`
- Move: `/boot/config/plugins/dockerMan/templates-user/my-Harcover_Sync.xml` to the repair backup directory with suffix `.disabled`

**Interfaces:**
- Consumes: Existing canonical XML for non-runtime presentation fields and current container inspect JSON for environment values.
- Produces: One active canonical Unraid template whose runtime fields match production.

- [ ] **Step 1: Generate a candidate XML in the repair directory**

Use Python `xml.etree.ElementTree` to parse the backed-up canonical XML. Set `Name`, `Repository`, `Network`, and `ExtraParams` to the approved values. Preserve the template's declared Variable `Config` nodes, refresh each value from the matching key in `.Config.Env`, replace existing Path nodes with the two approved Path nodes, and reject any template variable that is absent from the running container. Do not import image-internal environment keys such as `PATH` or `LANG`. Preserve values only in the file; print only node names and path targets.

- [ ] **Step 2: Validate the candidate without activating it**

Parse the candidate again and assert:

```python
assert root.findtext('Name') == 'hardcover-sync'
assert root.findtext('Repository') == 'ghcr.io/gmoran1016/hardcover-sync:latest'
assert '--shm-size=256m' in root.findtext('ExtraParams')
assert '--restart=unless-stopped' in root.findtext('ExtraParams')
assert cookie_path == ('/mnt/disk7/appdata/hardcover-sync/cookies', '/app/cookies', 'ro')
assert state_path == ('/mnt/disk7/appdata/hardcover-sync/state', '/app/state', 'rw')
```

Expected: PASS, and every Variable target declared by the original canonical template appears exactly once with the current container value.

- [ ] **Step 3: Activate the canonical template**

Install the validated candidate atomically as `my-hardcover-sync.xml` with mode `600` and owner `root:root`. Move the misspelled duplicate into the repair directory as `my-Harcover_Sync.xml.disabled`, leaving no other active Hardcover Sync XML template.

### Task 3: Recreate the container as Unraid-managed

**Files:**
- Create temporarily: `/tmp/hardcover-sync.unraid-repair.env`
- Preserve container: `hardcover-sync-pre-unraid-repair`

**Interfaces:**
- Consumes: Current inspect JSON, canonical mount paths, and current GHCR image.
- Produces: Running `hardcover-sync` with `net.unraid.docker.managed=dockerman`.

- [ ] **Step 1: Validate replacement inputs**

Assert the source container is exactly `/hardcover-sync`, the rollback name does not exist, both host directories and required JSON files exist, and the image revision equals `7306e099c6eb586386462d4020169d0d4f0b9f07`.

- [ ] **Step 2: Create the root-only environment file**

Extract `.Config.Env[]` from the inspect backup into `/tmp/hardcover-sync.unraid-repair.env`; set mode `600` and never display it.

- [ ] **Step 3: Swap containers with an EXIT rollback trap**

Stop `hardcover-sync`, rename it to `hardcover-sync-pre-unraid-repair`, and run the replacement with:

```sh
docker run -d \
  --name hardcover-sync \
  --label net.unraid.docker.managed=dockerman \
  --restart unless-stopped \
  --network bridge \
  --shm-size 256m \
  --env-file /tmp/hardcover-sync.unraid-repair.env \
  -v /mnt/disk7/appdata/hardcover-sync/cookies:/app/cookies:ro \
  -v /mnt/disk7/appdata/hardcover-sync/state:/app/state:rw \
  ghcr.io/gmoran1016/hardcover-sync:latest
```

The EXIT trap removes a failed replacement, restores the old container name, and restarts it. Remove the temporary environment file whether the operation succeeds or fails.

- [ ] **Step 4: Confirm initial runtime health**

Assert the new container remains running, has the expected image revision, label, restart policy, shared-memory size, and mounts. Then disarm the rollback trap while retaining the stopped rollback container.

### Task 4: Verify Unraid management and application behavior

**Files:**
- Read: `/boot/config/plugins/dockerMan/templates-user/my-hardcover-sync.xml`
- Read: `/mnt/disk7/appdata/hardcover-sync/state/sync_state.json`

**Interfaces:**
- Consumes: Repaired template and replacement container.
- Produces: Evidence that UI management metadata and application behavior are both restored.

- [ ] **Step 1: Re-run the original regression assertions**

Run the label and XML assertions from Task 1.

Expected: PASS. Also assert exactly one active matching template exists.

- [ ] **Step 2: Verify persisted inputs and process health**

Assert both cookie JSON files are readable inside the container, `sync_state.json` parses, exactly one Xvfb process exists, and no process has zombie state.

- [ ] **Step 3: Verify authentication**

Run:

```sh
docker exec hardcover-sync python -u main.py --diagnose-auth
```

Expected: Goodreads PASS, StoryGraph PASS, overall PASS, and no legacy-cookie warning.

- [ ] **Step 4: Verify synchronization**

Run:

```sh
docker exec hardcover-sync python -u main.py --once
```

Expected: one-shot sync completes with `0 failed`.

- [ ] **Step 5: Record rollback details**

Report the repair backup directory and stopped rollback-container name. Do not delete either during this task.

### Task 5: Commit the operational documentation

**Files:**
- Create: `docs/superpowers/specs/2026-07-18-unraid-dockerman-repair-design.md`
- Create: `docs/superpowers/plans/2026-07-18-unraid-dockerman-repair.md`

**Interfaces:**
- Consumes: Approved design and executed plan.
- Produces: Versioned record of the repair rationale and procedure.

- [ ] **Step 1: Run documentation checks**

Run:

```powershell
git diff --check
rg -n "[T]BD|[T]ODO|[P]LACEHOLDER" docs/superpowers/specs/2026-07-18-unraid-dockerman-repair-design.md docs/superpowers/plans/2026-07-18-unraid-dockerman-repair.md
```

Expected: `git diff --check` exits zero and the placeholder scan returns no matches.

- [ ] **Step 2: Commit the plan**

```powershell
git add docs/superpowers/specs/2026-07-18-unraid-dockerman-repair-design.md docs/superpowers/plans/2026-07-18-unraid-dockerman-repair.md
git commit -m "docs: plan Unraid management repair"
```

Expected: commit succeeds; the design may already be committed, in which case only the plan is included.

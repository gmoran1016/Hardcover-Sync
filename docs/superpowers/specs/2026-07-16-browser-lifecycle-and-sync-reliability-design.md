# Browser Lifecycle and Sync Reliability Design

## Objective

Release a tested reliability update that prevents a dead Xvfb process from
stranding the sync service, reaps orphaned Chromium processes, selects the
canonical Goodreads result for a book, and treats an already-read StoryGraph
book as a successful finish operation. Deploy the release to the Luthien
Unraid server without losing sync state or exposing credentials.

## Confirmed Failure Evidence

- Xvfb became a defunct child while the long-running Python process remained
  alive. Chromium then failed every hour with `Missing X server or $DISPLAY`.
- A stale `/tmp/.X99-lock` and `/tmp/.X11-unix/X99` caused the existing
  socket-only readiness check to report a dead display as ready after restart.
- PID 1 accumulated defunct Chromium and crashpad processes because it did not
  reap orphaned browser descendants.
- Goodreads returned several rows containing “Crossroads of Twilight.” The
  current matcher scored the entire row and saturated multiple candidates near
  `1.0`, so its ambiguity margin rejected the canonical work.
- The cached StoryGraph page for *Winter's Heart* visibly reports status
  `read`. StoryGraph omits the `mark-as-finished-btn` for that state, so waiting
  for the button can only time out.
- The Unraid container has no persistent mount for `/app/state`, and its cookie
  bind mount is writable. Recreating the current container without migration
  would discard its sync state.

## Container Lifecycle Design

Install Debian's `tini` package and make it PID 1 with subreaper and process
group signaling enabled. `tini` will reap orphaned Chromium descendants and
forward Docker stop signals to the managed process group.

Keep `container_entrypoint.py` as the service supervisor rather than replacing
it with `main.py`. At startup it will:

1. Resolve the display number and the exact lock/socket paths under `/tmp`.
2. Remove those two artifacts if they predate the new Xvfb process.
3. Start Xvfb and wait for both a live process and a newly created socket.
4. Start the configured application as a child process.
5. Monitor both direct children. If the application exits, terminate Xvfb and
   return the application's exit status. If Xvfb exits first, terminate the
   application and return a nonzero status so Docker's restart policy can
   recreate a complete browser environment.

Signal delivery is handled by `tini -g`, which forwards termination to the
whole child process group. The supervisor still performs bounded cleanup when
one child exits independently.

The Selenium configuration will remove the ignored
`useAutomationExtension` experimental option. The headed browser, Xvfb,
isolated profile directories, no-sandbox setting, and captured browser
identity remain unchanged because they are required by the observed Goodreads
authentication behavior.

## Goodreads Matching Design

Continue using conservative automatic selection, but separate the title
signal from metadata:

- Treat the first nonempty line of a result row as its displayed title.
- Score title similarity against that title line only.
- Use the full row solely to confirm the author.
- Combine the two as weighted evidence instead of adding a bonus that clamps
  several candidates to `1.0`.
- Keep the existing minimum score and runner-up margin, so genuinely ambiguous
  results remain rejected.

A regression fixture based on the observed Goodreads results must choose
`https://www.goodreads.com/book/show/113435.Crossroads_of_Twilight` over the
audiobook, marketplace listing, misleading subtitle, prequel, boxed set, and
article collection. Existing author-mismatch and no-fallback behavior remains
covered.

This release selects the canonical Goodreads work; it does not crawl all 108
Goodreads editions for an exact page-count match. Edition crawling would add
multiple fragile page interactions and is unnecessary to resolve the confirmed
search ambiguity.

## StoryGraph Finish Design

Before opening the reading-status dropdown, inspect visible
`.read-status-label` elements. Normalize their text and apply these rules:

- Exact `read` means the requested finished state already exists. Return a
  successful `SyncResult` with the mapped book URL without mutating the page.
- Any other status continues through the existing dropdown and
  `mark-as-finished-btn` interaction.
- Absence of both an already-read state and an actionable finish control
  remains a failure and is retried later.

This makes the finish operation idempotent and allows orchestration to persist
`finished` for StoryGraph when the user or an earlier run already completed the
book.

## Tests

All production behavior changes follow red-green TDD.

- `tests/test_container_entrypoint.py` will cover stale artifact cleanup,
  readiness rejection when Xvfb exits, application exit propagation, and Xvfb
  failure propagation/child termination.
- `tests/test_driver.py` will assert the deprecated Chrome option is absent.
- `tests/test_matching.py` will include the observed Goodreads candidate set
  and retain ambiguity/author tests.
- New StoryGraph unit tests will use a narrow fake driver to verify already-read
  success without a click, normal finish clicking, and missing-control failure.
- The complete local unit suite and formatting/lint/type checks configured by
  the repository must pass before push.

Live verification on Luthien will include container startup logs, a live Xvfb
process, absence of newly accumulating zombies across browser sessions,
`--diagnose-auth`, and `--once`. The final state must record Crossroads of
Twilight progress for Goodreads and `finished` for Winter's Heart on
StoryGraph.

## GitHub and Unraid Deployment

Commit the implementation to `main`, push to GitHub, and wait for the GitHub
container publishing workflow to produce an image whose revision label matches
the pushed commit.

Before replacing the running container:

1. Create `/mnt/user/appdata/hardcover-sync/state` with restrictive, appuser-
   writable ownership.
2. Copy `/app/state/sync_state.json` and its backup from the current container
   into that directory and preserve an additional timestamped backup.
3. Capture the current container configuration locally on Luthien without
   printing secret environment values.
4. Pull `ghcr.io/gmoran1016/hardcover-sync:latest` and verify its revision.
5. Recreate `hardcover-sync` with the same environment, restart policy,
   network, shared-memory size, and security settings; bind the migrated state
   directory to `/app/state` read-write and cookies to `/app/cookies` read-only.

If the new container fails startup or authentication verification, stop it and
recreate the previous image using the preserved configuration and state
backup. No cookie or state file will be deleted during deployment.

## Success Criteria

- Restarting with stale display artifacts starts a live Xvfb instance.
- Killing Xvfb causes the container to exit and Docker to restart it.
- Repeated browser sessions do not accumulate new defunct Chromium children.
- The canonical Goodreads Crossroads of Twilight work is selected and progress
  is saved.
- An already-read StoryGraph Winter's Heart mapping returns success and is
  persisted as `finished`.
- Authentication diagnostics pass for Goodreads and StoryGraph.
- Sync state survives container replacement in the new persistent bind mount.
- The deployed image revision equals the pushed Git commit.

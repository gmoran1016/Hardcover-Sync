# Audit Remediation Implementation Plan

**Goal:** Fix all fourteen findings and the operational improvements in the approved 2026-09-05 audit.

**Architecture:** Retain the Python CLI, Selenium adapters, and compatible schema-v2 JSON state. Require confirmed book identity and saved outcomes, serialize state writers, checkpoint before and after remote work, and expose actionable CLI diagnostics.

**Spec:** C:/Users/Griffin/Documents/Codex/Audits/hardcover-sync-2026-09-05-b7c0749/project-audit.md (user approved implementation with “Fix all these issues”).

**Constraints:** Python 3.11+, existing cookie bundles and schema-v2 files remain readable; fail closed on unknown state versions; preserve valid matching regressions; never alter live cookies/state in tests; no live progress writes, commits, pushes, or deployments during implementation. The clean checkout uses branch `codex/audit-fixes-2026-09-05`.

- [x] Destination correctness: reproduce and fix F01/F02/F04/F10 in matching.py, goodreads.py, storygraph.py and their tests. Preserve adapter public methods. Validate provider book URLs, match canonical identities, and read persisted status after mutations.
- [x] Storage and source boundary: reproduce and fix F06/F07/F11/F12 in sync_state.py, hardcover.py and tests. Provide `state_lock(path)`, `probe_state_storage(path)`, `recover_state(path)` (recovery owns its lock), and `HardcoverDeferredError.retry_after_seconds`. Preserve optional state fields `health`, `source_retry_at`, and per-destination `failures`. Read IDs and edition metadata participate in progress signatures.
- [x] Runtime and image validation: reproduce and fix F14 and graceful shutdown in driver.py/container_entrypoint.py with tests. Add a local-page Chrome image smoke test; test and publish the same image; harden build-context exclusions. Runtime additions in the Docker COPY list must include root-owned maintenance.py if created.
- [x] Orchestration and operator controls: reproduce and fix F03/F05/F08/F13 in main.py/config.py/sync_result.py/tests. Reconcile pending completions with fresh statuses, checkpoint outcomes, skip idle sessions, return structured cycle outcomes, respect persisted cooldowns, and support cancellation between operations.
- [x] Add explicit destination switches, log level, preflight, dry-run, status, mapping inspection/set/reset, and validated backup recovery. Keep a pending action's failure reason and latest successful sync time visible.
- [x] Update cookie setup feedback, Unraid ownership/write probes, polling limitations, recovery instructions, and a remediation checklist. Resolve dependencies into a reproducible lock and validate it in CI.
- [x] Run unit/regression suites, real local-browser fixtures, lint/format, dependency consistency, compilation, Compose validation, and review the complete diff. Attempt container checks if an engine is available. Document any remaining environmental validation limits accurately.

Independent implementation domains use disjoint file ownership and fixed interfaces. Integration and cross-domain review belong to the parent agent. Existing audit reproduction scripts are preserved as historical evidence; new tests assert corrected behavior.

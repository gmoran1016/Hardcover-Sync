# Project Reliability Review Design

## Objective

Perform a repository-wide review of Hardcover Sync and make evidence-backed improvements that maximize dependable unattended syncing, safe credential and session handling, and maintainability. Preserve Hardcover as the sole source of truth and avoid speculative new features.

## Scope

The review covers all tracked application code, tests, deployment configuration, dependency declarations, CI configuration, and user documentation. The untracked `.claude/` directory is user-owned and must remain untouched.

Backward compatibility is required for existing environment variables, cookie bundle formats, schema-v2 sync state, Docker Compose usage, and the `--once` and `--diagnose-auth` command-line modes. A compatibility break is allowed only when a confirmed security or correctness issue cannot be resolved safely otherwise, and it must be documented.

## Architecture

Retain the current module-level architecture and Python CLI. `main.py` remains the orchestration boundary, while API access, state persistence, matching, browser management, and destination automation remain independently understandable units.

Refactor oversized destination modules only when doing so removes duplicated risky behavior, establishes a testable boundary, or fixes a confirmed correctness problem. Do not introduce a new framework or perform a wholesale rewrite.

Every behavioral change must be supported by a reproducible failing test or concrete static evidence. Dependency and deployment changes must follow current upstream guidance and be verified for compatibility with the supported Python and container environments.

## Runtime and Data Flow

1. Load and validate runtime configuration without exposing secret values.
2. Fetch Hardcover data with bounded retries, strict response validation, and actionable failure classification.
3. Compare valid source progress with durable, per-destination synchronization state.
4. Run each enabled destination independently so one destination failure cannot block another.
5. Record only confirmed destination outcomes. Leave failed operations pending for a later retry.
6. Persist state atomically and retain the existing recovery copy behavior.
7. Validate book identity and progress before browser actions. Reject malformed or ambiguous data rather than risking an incorrect update.
8. Clean up WebDrivers, file handles, temporary directories, and virtual-display processes on success and failure.
9. Log the failing stage and recovery action without logging API keys, passwords, cookies, or session data.

## Review Areas

The review will examine:

- Hardcover request construction, retry behavior, response parsing, and progress normalization.
- State schema validation, migration, atomic writes, recovery behavior, and partial/corrupt data handling.
- Per-destination isolation, matching confidence, progress and completion transitions, and retry semantics.
- Selenium lifecycle management, temporary resources, timeouts, selectors, and exception handling.
- Cookie bundle validation, storage permissions, mutation safety, and secret exposure.
- Configuration validation and CLI/scheduler shutdown behavior.
- Docker image and Compose security, process lifecycle, filesystem permissions, and health diagnostics.
- Dependency versions and known vulnerabilities.
- CI coverage, documentation correctness, encoding, setup instructions, and troubleshooting guidance.

## Error Handling

External failures must not replace trustworthy state or mark work complete. Hardcover failures abort only the current cycle. Destination failures are isolated and retained as pending work. State read failures must remain explicit so corrupt state is not silently overwritten. Cleanup failures may be logged but must not mask the primary failure.

Logs must distinguish configuration, source API, matching, authentication, browser startup, destination update, persistence, and scheduler failures. Secret-bearing values must never appear in logs or exception messages produced by project code.

## Testing Strategy

Preserve the existing 29-test baseline. For each behavioral bug fix, first add a focused regression test and confirm that it fails for the expected reason. Implement the smallest correction, confirm the test passes, and then run the full suite.

Expand automated coverage where review evidence identifies gaps, prioritizing malformed API and state data, progress edge cases, destination isolation, cleanup paths, cookie validation, and configuration boundaries. Mock only external boundaries such as HTTP, Selenium, filesystem failure injection, time, and signals.

Final verification includes the complete unit suite, Python compilation, applicable static checks, dependency and security checks, Docker Compose validation, and a Docker image build when supported locally. The final diff must be inspected for unintended or user-owned changes.

## Deliverables

- Test-first fixes for confirmed correctness, reliability, or security problems.
- Bounded maintainability improvements that directly support those fixes.
- Corrected and current setup, deployment, and troubleshooting documentation.
- A final review summary listing resolved issues, verification evidence, and any remaining limitations that require live credentials or external services.

## Non-Goals

- Adding bidirectional sync or changing Hardcover's source-of-truth role.
- Adding destinations, a web interface, analytics, or unrelated features.
- Rewriting the service in another language or framework.
- Live progress mutations against the user's Goodreads or StoryGraph accounts during automated verification.

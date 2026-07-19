# StoryGraph Progress Unit Design

## Objective

Reliably save page-based Hardcover progress to StoryGraph when StoryGraph opens its progress editor in percentage mode.

## Root Cause

StoryGraph now remembers or defaults the progress editor's unit to `percentage`. In that state, `read_status_progress_number` has a maximum of 100. Hardcover Sync currently writes the absolute page count without changing the unit. For the observed update from 75 to 103 pages, browser validation rejects 103 as an invalid percentage, the form never submits, and the existing invisibility wait times out.

## Behavior

Before entering an absolute page count, Hardcover Sync will locate the visible progress editor and select its `pages` option. It will dispatch normal input and change events, then wait until the progress-number input reflects page-mode constraints. If only percentage progress is available, it will explicitly select `percentage` and submit the percentage value.

The input and submit button will be resolved from the same visible form. The implementation will reject missing unit controls, invalid form state, or values outside the active input bounds with a specific log message rather than clicking Save and reporting a generic timeout.

## Success Detection

Form disappearance is not a reliable success signal because StoryGraph may leave the editor open. After submission, Hardcover Sync will wait for evidence that the saved-progress state changed to the requested value. Success may be established by the form closing or by StoryGraph's last-reached page/percentage field updating. A visible validation error or unchanged saved state at timeout is a failure.

## Testing

Regression tests will model StoryGraph's observed percentage-default form:

- Page update selects `pages` before setting a value greater than 100.
- Percentage-only update explicitly selects `percentage`.
- Input and Save are taken from the same visible form when duplicate mobile/desktop forms exist.
- Invalid form state fails without submitting.
- A saved-state update succeeds even when the editor remains visible.
- Unchanged saved state times out as a failure.

The complete local test suite and a live server one-shot synchronization must pass. Live verification must record StoryGraph at 103 pages, complete with zero failed destinations, and leave no zombie browser processes.

# Unraid Docker Manager Repair Design

## Objective

Restore Edit and Update support for the `hardcover-sync` container in Unraid without changing application behavior, losing persistent data, or exposing credentials.

## Root Cause

The production container was recreated with the Docker CLI during the 2.0.7 deployment. Its configuration lacks the `net.unraid.docker.managed=dockerman` label used by Unraid-managed application containers. The canonical saved template also contains obsolete cache-backed cookie storage, omits the persistent state mount, and coexists with an older misspelled duplicate template.

## Repair

1. Save root-only backups of both existing template XML files and the current container inspection data.
2. Generate one canonical `my-hardcover-sync.xml` template from the current working container configuration. It will retain the GHCR image, bridge networking, 256 MiB shared memory, restart policy, sync interval, and existing credentials. It will define:
   - `/mnt/disk7/appdata/hardcover-sync/cookies` to `/app/cookies` as read-only.
   - `/mnt/disk7/appdata/hardcover-sync/state` to `/app/state` as read-write.
3. Move the misspelled `my-Harcover_Sync.xml` out of the active `templates-user` XML set while preserving it in the repair backup.
4. Recreate `hardcover-sync` with its current image revision and configuration plus `net.unraid.docker.managed=dockerman`. Preserve the stopped pre-repair container under a unique rollback name until verification succeeds.
5. Do not print environment values or template contents during the repair.

## Failure Handling

All target paths and container names will be validated before mutation. If the replacement container cannot be created or remain running, it will be removed and the stopped pre-repair container will be restored to its original name and started. Template backups will remain available independently of container rollback.

## Verification

The repair is complete only when all of the following pass:

- The running container has `net.unraid.docker.managed=dockerman`.
- The canonical template has the correct name, image, cookie mount, state mount, modes, shared-memory setting, and restart policy.
- No duplicate active Hardcover Sync template remains.
- The container runs the expected Git revision.
- Both cookie files are readable inside the container and state remains valid JSON.
- Saved-cookie authentication succeeds for Goodreads and StoryGraph.
- A one-shot synchronization completes without failures.
- Xvfb is running and no zombie processes exist.

The pre-repair container and template backups will be retained for rollback after verification.

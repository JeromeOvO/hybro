# Conversation History Canonical Cutover Runbook

## Target schema

`room_memories.conversation_history` is the only persisted conversation-history
field. `memory_content` continues to hold `summary` (and any other unrelated
metadata), but must not contain `conversation_history`.

The migration reconciles transition-era documents in the same order as the former
runtime reducer: nested history first, direct history second. A later direct item
replaces an earlier item with the same non-null `turn_id` at its stable position.
Items with no `turn_id` or a null id are retained in stable order and are never
deduplicated. After reconciliation, compact turns with a missing or blank
`brief_summary` are backfilled with the same deterministic 200-character helper
used by current compaction. The migration reads full content from
`conversation_content` by `content_ref.document_id`, with a legacy
`room_id` + `turn_id` lookup when needed. A non-empty existing
`turn_notes.one_liner` is the only fallback when full content is unavailable.
Non-array history fields, non-object history items, malformed `memory_content`
values, and compact turns with neither recoverable content nor a reliable one-liner
are blockers; they are reported and never silently replaced with a placeholder.

## Rollout preconditions (operations must execute)

This repository change does **not** apply or observe the migration in production.
Before enabling the cutover runtime, operations must complete the following
steps.

Before running either migration command, set `MONGODB_URL` and
`MONGODB_DB_NAME` in the environment consumed by backend settings (for example,
the approved deployment secret/configuration source) and verify the effective
target without echoing credentials. The script reads the URI only from settings
and uses the settings database name by default; the CLI intentionally has no
`--mongo-url` option because process arguments may be visible to other users or
captured by process tooling. A non-sensitive database-name override may be
supplied as `--database <database-name>` when required. All invalid CLI input,
including unknown or misspelled options, positional arguments, invalid values,
and missing values, exits with a generic error that never repeats the original
argument or value. Archived script summaries must not contain the URI,
credentials, or other secrets; the summary is limited to the collection name,
migration phase, and counts.

1. Take and verify restorable backups/snapshots of both `room_memories` and
   `conversation_content`. Record the `conversation_content` TTL policy and
   confirm the migration identity can read that collection; the migration does
   not modify it. Before the initial audit, pause TTL deletion for
   `conversation_content` or establish an approved retention hold that guarantees
   every document needed for backfill remains readable in the live collection.
   Keep that pause/hold in place through apply and its successful final audit. A
   backup alone does not provide this live-read guarantee.
2. Run the read-only audit against the target database and archive its output:

   ```bash
   cd backend
   uv run --frozen python scripts/migrate_conversation_history.py
   ```

3. Resolve every reported blocker. An apply is prohibited while blockers remain.
   Review `backfill_count` separately from `missing_content_blockers`. A missing
   content document may have been removed by the `conversation_content` TTL; if no
   trustworthy existing one-liner remains, restore/recover the content from the
   verified snapshot through an approved operational process before rerunning. Do
   not substitute `[compact turn]` or another placeholder. Confirm that every API
   client and worker
   which submits a full room-memory DTO
   populates top-level `conversation_history`; nested-only payload producers must be
   upgraded before cutover.
4. Establish a maintenance window and stop **all** room-memory writers, including
   API instances, background jobs, and ad-hoc workers. The migration's optimistic
   snapshot check detects many write races, but quiescence is a rollout
   precondition, not an optional safety mechanism.
5. Rerun the dry-run audit while writers are stopped. Confirm the target database,
   both backup identifiers, counts, zero blockers, and that the TTL pause/retention
   hold will remain effective until the final audit completes.
6. Apply only with the explicit flag:

   ```bash
   uv run --frozen python scripts/migrate_conversation_history.py --apply
   ```

   The command performs a final audit. It must finish with `blockers=0` and
   `would_update=0`. Do not release the TTL pause/retention hold between the
   pre-apply audit and this final audit: content disappearing during apply can
   stop the command after earlier documents have already been updated.
7. Deploy the canonical-only runtime before resuming writers. Verify representative
   reads, append, duplicate projection, summary boundary behavior, compaction, and
   context assembly. Then monitor application errors and room-memory write metrics
   for the agreed observation window.

The script is idempotent: a repeat apply after success reports zero updates and
zero backfills. If apply is interrupted after partial updates, keep writers
stopped, restore any required content from the verified backup/snapshot, and rerun
first the audit and then apply; already migrated documents are safe to encounter
again. Idempotency is not a substitute for either backup: it cannot recover
expired source content or roll back migrated documents. The script is read-only
by default, performs only reads against `conversation_content`, and does not write
completion markers. The optimistic apply predicate still snapshots both top-level
and nested history fields; any concurrent history change stops the migration.

## Rollback

Stop all writers before rollback. Do not attempt to synthesize nested history by
hand or from a bounded display window.

- If the previous runtime is confirmed to operate from top-level history, roll back
  the application while retaining the migrated documents, then audit immediately.
- If the rollback release requires nested history, restore `room_memories` from the
  verified pre-migration backup. Any writes accepted after the snapshot must first
  be accounted for; restoring the collection without reconciliation can lose them.
- After either path, rerun the dry-run audit and record the result. Do not resume
  traffic until the chosen runtime and persisted schema are mutually compatible.

There is intentionally no automatic reverse migration: reconstructing the removed
nested field could reintroduce a stale or truncated copy and make history ownership
ambiguous again.

## Validation limits

Automated tests exercise migration semantics and repository pipelines with fakes.
No real MongoDB integration or production traffic observation is performed by this
change; those remain operational rollout work.

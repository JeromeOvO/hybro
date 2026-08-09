# Conversation History Canonical Cutover Runbook

## Target schema

`room_memories.conversation_history` is the only persisted conversation-history
field. `memory_content` continues to hold `summary` (and any other unrelated
metadata), but must not contain `conversation_history`.

The migration reconciles transition-era documents in the same order as the former
runtime reducer: nested history first, direct history second. A later direct item
replaces an earlier item with the same non-null `turn_id` at its stable position.
Items with no `turn_id` or a null id are retained in stable order and are never
deduplicated. Non-array history fields, non-object history items, and malformed
`memory_content` values are blockers; they are reported and never silently dropped.

## Rollout preconditions (operations must execute)

This repository change does **not** apply or observe the migration in production.
Before enabling the cutover runtime, operations must:

1. Take and verify a restorable backup/snapshot of `room_memories`.
2. Run the read-only audit against the target database and archive its output:

   ```bash
   cd backend
   uv run python scripts/migrate_conversation_history.py \
     --mongo-url "$MONGODB_URL" --database "$MONGODB_DB_NAME"
   ```

3. Resolve every reported blocker. An apply is prohibited while blockers remain.
   Confirm that every API client and worker which submits a full room-memory DTO
   populates top-level `conversation_history`; nested-only payload producers must be
   upgraded before cutover.
4. Establish a maintenance window and stop **all** room-memory writers, including
   API instances, background jobs, and ad-hoc workers. The migration's optimistic
   snapshot check detects many write races, but quiescence is a rollout
   precondition, not an optional safety mechanism.
5. Rerun the dry-run audit while writers are stopped. Confirm the target database,
   backup identifier, counts, and zero blockers.
6. Apply only with the explicit flag:

   ```bash
   uv run python scripts/migrate_conversation_history.py \
     --mongo-url "$MONGODB_URL" --database "$MONGODB_DB_NAME" --apply
   ```

   The command performs a final audit. It must finish with `blockers=0` and
   `would_update=0`.
7. Deploy the canonical-only runtime before resuming writers. Verify representative
   reads, append, duplicate projection, summary boundary behavior, compaction, and
   context assembly. Then monitor application errors and room-memory write metrics
   for the agreed observation window.

The script is idempotent: a repeat apply after success reports zero updates. It is
read-only by default and does not write a completion marker.

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

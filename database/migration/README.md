# Database migrations (operational scripts)

Run these **before** or **during** deploy when the release notes call for it. Scripts are idempotent where possible (Mongo `create_index` is a no-op if the index already exists with the same options).

## Run lifecycle (`runs` / `run_events`)

**When:** Any release that adds or changes run lifecycle indexes. The legacy
`database/mongodb.py` helper has been deleted; port this migration to `MongoDAL`
or use the runtime index registry before running it again.

**Command** (from `multi-agents-backend` repo root, venv active, `MONGODB_URL` set):

```bash
python database/migration/add_run_lifecycle_indexes.py
```

## Legacy `rooms.processing_message_id` null repair

**When:** After releases that remove projector writes to `rooms.processing_message_id`. Clears the field only for rooms that have **no** non-terminal runs (same rule as compaction and `stale_task_checker` legacy cleanup).

**Command** (venv active, `MONGODB_URL` set):

```bash
DRY_RUN=1 python database/migration/null_legacy_room_processing_message_id.py
python database/migration/null_legacy_room_processing_message_id.py
```

`DRY_RUN=1` prints the match count and performs no writes.

## Purge empty artifact parts

**When:** After fixing the `sanitize_artifact_parts` / `normalize_inbound_parts` guards that allowed empty `{}` entries to slip into `room_agent_messages.message_content.message_task.artifacts.*.parts`. These corrupt entries cause Pydantic validation failures on every read.

**Command** (venv active, `MONGODB_URL` set):

```bash
DRY_RUN=1 python database/migration/purge_empty_artifact_parts.py
python database/migration/purge_empty_artifact_parts.py
```

`DRY_RUN=1` prints the match count and performs no writes.

## Rename legacy supervisor v2 field names

**When:** Deploying the supervisor v2 naming cleanup (code uses `extend_info.supervisor`).

**Command** (venv active, `MONGODB_URL` set):

```bash
python -m database.migration.rename_supervisor_v2_fields --dry-run
python -m database.migration.rename_supervisor_v2_fields
```

Renames `extend_info.supervisor_v2`, `extend_info.supervisor_v2_clarify_resume`, and `pending_continuation.supervisor_v2` on `room_user_messages` and `room_agent_messages`.

## Feature flags (rollout; set in your deploy config after indexes exist)

| Variable | Purpose |
|----------|---------|
| `FEATURE_RUN_DUAL_WRITE` | Persist `runs` / `run_events` from processing SSE (default on: `1`) |
| `FEATURE_RUN_PROJECTOR_MIRROR` | **Removed** — `rooms.processing_message_id` is no longer written on the lifecycle path; use the null repair script above for data cleanup |
| `FEATURE_RUN_EVENT_SSE` | Emit extra `run_event` SSE after each persisted event |
| `FEATURE_RUN_PARITY_LOG` | **Removed** with mirror writes (was projector drift logging) |
| `FEATURE_RUN_WATCHDOG` | Stale-run watchdog in `stale_task_checker` (default on) |
| `RUN_WATCHDOG_STALE_MINUTES` | Non-terminal run age before timeout (default `90`) |

Frontend (Hybro app): `NEXT_PUBLIC_FEATURE_RUN_LIFECYCLE_READ`, `NEXT_PUBLIC_FEATURE_RUN_EVENT_SSE` (optional; must match backend if using `run_event` reconciliation).

# Database migrations (operational scripts)

Run these **before** or **during** deploy when the release notes call for it. Scripts are idempotent where possible (Mongo `create_index` is a no-op if the index already exists with the same options).

## Run lifecycle (`runs` / `run_events`)

**When:** Any release that adds or changes run lifecycle indexes (see `database/mongodb.py` → `create_run_lifecycle_indexes`).

**Command** (from `multi-agents-backend` repo root, venv active, `MONGODB_URL` set):

```bash
python scripts/migrations/run_run_lifecycle_indexes.py
```

**Environment flags** (rollout; set in your deploy config after indexes exist):

| Variable | Purpose |
|----------|---------|
| `FEATURE_RUN_DUAL_WRITE` | Persist `runs` / `run_events` from processing SSE (default on: `1`) |
| `FEATURE_RUN_PROJECTOR_MIRROR` | Derive `rooms.processing_message_id` from active runs in `send_processing_status` |
| `FEATURE_RUN_EVENT_SSE` | Emit extra `run_event` SSE after each persisted event |
| `FEATURE_RUN_PARITY_LOG` | Log projector mirror drift (`1` to enable) |
| `FEATURE_RUN_WATCHDOG` | Stale-run watchdog in `stale_task_checker` (default on) |
| `RUN_WATCHDOG_STALE_MINUTES` | Non-terminal run age before timeout (default `90`) |

Frontend (Hybro app): `NEXT_PUBLIC_FEATURE_RUN_LIFECYCLE_READ`, `NEXT_PUBLIC_FEATURE_RUN_EVENT_SSE` (optional; must match backend if using `run_event` reconciliation).

# Database migrations

Legacy migration scripts that depended on `database.mongodb` were removed with
the legacy database runtime. New migrations must be standalone scripts that use
`MONGODB_URL` and `MONGODB_DB_NAME` directly, or use explicit DAL/repository
interfaces. Do not reintroduce `database/mongodb.py` for migration
compatibility.

## Legacy system agent IDs

`migrate_legacy_system_agents.py` updates historical system-agent IDs in
`room_agent_messages`, `room_memories`, and `run_events`.

```bash
python -m database.migration.migrate_legacy_system_agents
```

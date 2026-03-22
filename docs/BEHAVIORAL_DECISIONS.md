# Behavioral Decisions Record

**Date**: March 2026  
**Status**: DRAFT — must be completed before Phase 1 (Path C Greenfield) code is written  
**Owner**: Engineering lead  
**Referenced by**: `MIGRATION_STRATEGY_ANALYSIS.md §8 Constraint 3`, `PERSISTENCE_UNIFICATION_DESIGN.md §10`

> This document is the primary risk mitigation for the "parity illusion" problem identified in `MIGRATION_STRATEGY_ANALYSIS.md §4`. It turns unknown unknowns into explicit trade-offs. **Every behavior listed below must have a decision before production code for the new backend is written.**

---

## How to Use This Document

For each behavior, mark one of:

- **Keep** — reproduce exactly in the new system; note the exact expected behavior
- **Improve** — specify the new behavior explicitly; note what changes and why
- **Drop** — accept this edge case will not be handled in new system v1; note the user impact

A behavior marked **TBD** blocks Phase 1 from starting.

---

## 1. Execution Behaviors

| # | Behavior | Current Location | Decision | New Behavior / Notes |
|---|---|---|---|---|
| E1 | Text artifact backfill: synthesize `TextPart` from `message_text` when `artifacts` is empty | `room_services.py` | **Keep** | Replicate as post-processing in `invoke_agent` step finalization (see `PERSISTENCE_UNIFICATION_DESIGN.md §10`) |
| E2 | Supervisor V2 three distinct resume paths (message-level, user-message-level, HITL) | `RoomMessageCenter._resume_supervisor_v2` | **Improve** | DBOS `recv()` is a single durable resume model; three-path complexity disappears. Verify no user-visible behavior depends on the path distinction |
| E3 | HITL interrupt/resume state with artifact and task state preservation | `hitl_service.py`, `SupervisorExecutor.py` | **Keep** | DBOS `send/recv` carries the full payload; `hitl_requests` collection removed but behavior preserved |
| E4 | Continuation blob storage variants (message-level vs. user-message-level) | `SupervisorExecutor.py`, `RoomMessageCenter.py` | **Drop** | DBOS replaces all continuation storage; schema variants disappear entirely |
| E5 | Debate mode multi-agent sequencing and result aggregation | `DebationCenter`, `WorkflowCenter` | TBD | ⚠️ Needs explicit decision before debate mode is migrated in Phase 2 |
| E6 | `room.extend_info.use_supervisor` flag (stored at room creation time) | `room_services.py` | **Improve** | Moved to run-time request parameter `workflow_type`. ⚠️ Frontend change required |
| E7 | Max step count enforcement in supervisor | `SupervisorExecutor.py` | **Keep** | Port to `supervisor_run` DBOS workflow; `max_steps` from `OrchestrationRequest` |
| E8 | Synthesis step after all agents respond (supervisor) | `SupervisorExecutor.py` | **Keep** | Implement as a named `@DBOS.step()` in supervisor workflow |

---

## 2. SSE / Streaming Behaviors

| # | Behavior | Current Location | Decision | New Behavior / Notes |
|---|---|---|---|---|
| S1 | SSE event ordering: `task_submitted` before `artifact_update` before `task_update(completed)` | `direct.py`, `AgentResponseHandler` | **Keep** | AG-UI equivalent ordering: `RUN_STARTED` → `TEXT_MESSAGE_START/CONTENT` → `RUN_FINISHED`. Frontend depends on this ordering |
| S2 | Terminal status dedup via `_terminal_status_sent` TTLCache (now Redis) | `sse_services.py` | **Keep** | Already in Redis; no change needed |
| S3 | `notify_task_update` bypass of broadcaster for terminal events | `task_notification_service.py` | **Improve** | All events go through `InteractionAdapter.emit()`; no broadcaster bypass |
| S4 | `processing_status` side effects: dedup + DB persistence alongside broadcast | `sse_services.py` | **Improve** | Separate concerns: DBOS owns execution status; Redis/InteractionAdapter owns delivery |
| S5 | DirectTransport 10 direct `sse_manager` calls that bypass `AgentResponseHandler` | `direct.py` | **Improve** | All delivery via `interaction_adapter.emit()` (completing EVENT_PIPELINE_DESIGN G1) |
| S6 | Heartbeat event format and cadence | `sse_services.py` | TBD | ⚠️ Check if frontend depends on heartbeat payload shape; AG-UI handles keep-alive differently |

---

## 3. Persistence Behaviors

| # | Behavior | Current Location | Decision | New Behavior / Notes |
|---|---|---|---|---|
| P1 | Per-chunk MongoDB writes (full doc replace) | `DirectTransport.persist_message()` | **Improve** | Replaced by Redis accumulation buffer + single finalized insert (see `PERSISTENCE_UNIFICATION_DESIGN.md §6`) |
| P2 | Per-chunk `$push` in handler path | `AgentResponseHandler.accumulate_artifact_on_message()` | **Improve** | Same as P1 |
| P3 | `supervisor_trajectory` embedded in `room_agent_messages.extend_info` | `SupervisorExecutor.py` | **Drop** | DBOS `operation_outputs` holds step outputs; trajectory is reconstructable from step audit log |
| P4 | `pending_continuation` field on message documents | `RoomMessageCenter.py` | **Drop** | DBOS `notifications` (send/recv) replaces all continuation storage |
| P5 | `processing_status` as a persistent MongoDB field | `room_agent_messages` | **Drop** | Runtime-only field; DBOS `workflow_status` is the authoritative source |
| P6 | `hitl_requests` collection | `hitl_service.py` | **Drop** | DBOS `notifications` holds HITL state; `agent_invocations` audit log holds history |
| P7 | Compaction trigger: turn-count threshold in `room_services.py` | `compaction_service.py`, `room_services.py` | **Keep** | Port trigger to `context_memory/compaction/`; read from `messages` collection length instead of `conversation_history` array length |
| P8 | Artifact backfill: `s3_converted` flag to prevent double S3 conversion | `room_services.py` | **Drop** | Single finalization path eliminates double-write possibility |

---

## 4. API / Integration Behaviors

| # | Behavior | Current Location | Decision | New Behavior / Notes |
|---|---|---|---|---|
| A1 | A2A protocol: `tasks/send`, `tasks/sendSubscribe` legacy RPC method names | `common/types.py`, `common/server/` | **Improve** | Consolidate to `message/send`, `message/stream` (current spec). See `A2A_UPGRADE_ROADMAP.md` Phase 2 |
| A2 | `sessionId` field on Task (legacy, should be `context_id`) | `common/types.py:66,112` | **Improve** | Rename to `context_id` during A2A v1.0 upgrade |
| A3 | Hub relay offline queue: 100-message cap, 24h TTL, periodic sweep | `relay_service.py`, `stale_task_checker.py` | **Improve** | DBOS durable wait (see `RECOMMENDED_ARCHITECTURE.md §Gap 1`); cap removed |
| A4 | A2A agent health check and capability issue detection | `agent_health_service.py`, `agent_capability_issue_service.py` | **Keep** | Port to `agent_intelligence/health_checker.py` unchanged |
| A5 | Webhook transport behavior | `webhook.py` | TBD | ⚠️ Needs review before Phase 2 migration |

---

## 5. Frontend Contract Behaviors

These must be validated with frontend contract tests **before** Phase 1 → Phase 2 migration begins. See `MIGRATION_STRATEGY_ANALYSIS.md §Appendix` for the full SSE event list.

| # | Behavior | Decision | AG-UI Replacement |
|---|---|---|---|
| F1 | `task_submitted` event shape | **Keep until Phase 3** | `RUN_STARTED` + metadata in `STATE_SNAPSHOT` |
| F2 | `task_update` event shape and `status` values | **Keep until Phase 3** | `RUN_FINISHED` + `ACTIVITY_DELTA` |
| F3 | `artifact_update` event shape (`append`, `last_chunk`) | **Keep until Phase 3** | `TOOL_CALL_RESULT` or `CUSTOM("artifact")` |
| F4 | `processing_status` event shape | **Keep until Phase 3** | `STEP_STARTED` / `STEP_FINISHED` |
| F5 | `hitl_input_requested` / `hitl_status_update` events | **Keep until Phase 3** | AG-UI interrupt spec (`RUN_FINISHED` + `outcome: "interrupt"`) |
| F6 | `agent_response` event shape | **Keep until Phase 3** | `TEXT_MESSAGE_START/CONTENT/END` |
| F7 | `error` event shape | **Keep until Phase 3** | `RUN_ERROR` |

> **Note**: All F1–F7 decisions are "Keep until Phase 3." Phase 3 is when AG-UI adoption replaces these events. The contract tests written in Phase 1 serve as the regression baseline for this cutover.

---

## Open Questions (must resolve before code)

| # | Question | Owner | Target Phase |
|---|---|---|---|
| Q1 | Does the frontend depend on heartbeat payload shape (S6)? What is the current shape? | Frontend + Backend | Phase 1 |
| Q2 | What are the exact debate mode sequencing semantics and result aggregation contract (E5)? | Backend | Phase 2 |
| Q3 | Does any external API consumer depend on the `tasks/sendSubscribe` legacy RPC name (A1)? | Backend + DevRel | Phase 2 |
| Q4 | What is the exact retry and fallback behavior when a webhook transport call fails? (A5) | Backend | Phase 2 |
| Q5 | Which `room.extend_info.use_supervisor` values are set by existing users? Needs audit before removing (E6). | Backend + Data | Phase 1 |

---

*Related documents: `MIGRATION_STRATEGY_ANALYSIS.md` · `PERSISTENCE_UNIFICATION_DESIGN.md` · `RECOMMENDED_ARCHITECTURE.md`*

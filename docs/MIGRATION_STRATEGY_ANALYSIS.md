# Backend Migration Strategy Analysis

**Date**: March 2026  
**Author**: Kevin Lu  
**Context**: Evaluation of three migration paths for the multi-agents backend ahead of a marketing-driven growth push. Current user base: <100 active users.

---

## Table of Contents

1. [Situation Assessment](#1-situation-assessment)
2. [The Three Paths](#2-the-three-paths)
3. [Path A: Gradual Refactoring (Strangler Fig)](#3-path-a-gradual-refactoring-strangler-fig)
4. [Path B: Parallel Rebuild (Hard Cutover)](#4-path-b-parallel-rebuild-hard-cutover)
5. [Path C: Greenfield New Build (Intentional Redesign)](#5-path-c-greenfield-new-build-intentional-redesign)
6. [How the User Count Changes the Math](#6-how-the-user-count-changes-the-math)
7. [Industry Evidence](#7-industry-evidence)
8. [Recommendation](#8-recommendation)
9. [Decision Criteria Summary](#9-decision-criteria-summary)

---

## 1. Situation Assessment

### Codebase Reality

| Area | Key Files | Lines |
|---|---|---|
| Execution modules | `RoomMessageCenter`, `SupervisorExecutor`, `QueueExecutor`, `WorkflowCenter` | ~5,100 |
| Transport layer | `direct.py`, `relay.py`, `webhook.py` | ~2,000 |
| Services layer | `room_services`, `database_service`, `sse_services`, `hitl_service`, `relay_service` | ~9,000+ |
| API endpoints | `room_center`, `viewset`, `agent`, `sse`, `relay`, `hitl` | ~2,800 |
| **Total** | | **~28,000+ lines** |

The five most complex files alone (`RoomMessageCenter.py` at 1,629 lines, `SupervisorExecutor.py` at 1,506 lines, `direct.py` at 1,600 lines, `WorkflowCenter.py` at 1,195 lines, `room_services.py` at 3,635 lines) total ~9,500 lines of deeply stateful, interleaved execution logic.

### What Makes This Codebase Hard to Migrate

Several structural properties make this specific system harder than a typical backend migration:

1. **Execution state is embedded in MongoDB documents.** Task state, supervisor trajectories, HITL continuations, and resume blobs live inside `room_agent_messages` documents via `extend_info`, `pending_continuation`, and embedded task sub-documents. There is no clean separation between "execution truth" and "conversation history."

2. **Two overlapping execution paths.** `RoomMessageCenter._process_supervisor_v2` and `QueueExecutor` represent fundamentally different execution models routing through the same entry point (`process_room_user_message`). `_resume_supervisor_v2` alone handles multiple distinct resume scenarios with different continuation storage.

3. **SSE event routing is fragmented.** `DirectTransport` has 10 direct `sse_manager` calls that bypass `AgentResponseHandler`, while `RelayTransport` and `WebhookTransport` route all events through the handler. This creates behavioral drift that must be tracked across any migration.

4. **The `notify_task_update` bypass.** Terminal `task_update` SSE events go through `SSEManager` directly, bypassing the broadcaster. In a multi-instance deployment this is a critical correctness bug; in migration it means terminal events need special handling.

5. **`room_services.py` encodes years of implicit decisions.** At 3,635 lines, this file contains context window management heuristics, artifact backfill logic, coordinator summary assembly, compaction triggers, and HITL state management — much of it without documentation of the "why."

### What Has Already Been Built

**Horizontal scaling** (`feature/redis-implement`, merged): All 5 phases of `HORIZONTAL_SCALING_DESIGN.md`:
- Redis Pub/Sub for cross-instance SSE fan-out
- Redis Streams for hub relay durability
- Redis-based leader election for background jobs
- Cross-instance cancellation and terminal dedup

**Hub / local-remote hybrid** (`HYBRO_HUB_DESIGN.md`, all phases merged):
- Phase 1 ✅ — Gateway API: hub code can discover and call cloud agents via `api.hybro.ai/v1/gateway`
- Phase 2a ✅ — Cloud Relay Service + `DispatchMiddleware` architecture; per-agent local/cloud routing via `agent.source`
- Phase 2b ✅ — Hub daemon (`pip install hybro-hub`), Ollama A2A adapter, auto-discovery, relay client
- Phase 2c ✅ — Frontend hub/cloud badges, offline dimming, privacy indicators, hub settings section

The next layer of designed-but-not-yet-implemented work is in PR #127: a full execution plane redesign (`ExecutionRun/Step/Invocation/Interruption`), runtime consistency model (outbox/inbox), and 8-module business capability architecture.

**Note**: The recommended target for Path C is NOT the PR #127 design as written, but the revised architecture in `RECOMMENDED_ARCHITECTURE.md` (DBOS instead of custom runtime + arq, AG-UI instead of custom SSE schema, plus workflow authoring layer). The core issues with PR #127 are documented there.

### The Business Context

The team is preparing a significant marketing push. This changes the calculus in a specific way:
- **Current state**: <100 users means migration disruptions affect very few people
- **Target state**: Active user acquisition means the system must be production-grade before or shortly after launch
- **Window**: There is a limited period where low user count makes risky moves acceptable

---

## 2. The Three Paths

### Definitions

**Path A — Gradual Refactoring (Strangler Fig)**  
Incrementally hollow out the existing system by routing subsystems through new abstractions while the old execution paths remain functional. The new architecture grows its share of responsibility piece by piece until the old code can be deleted. This is what PR #127's migration plan proposes.

**Path B — Parallel Rebuild (Hard Cutover)**  
Keep the current backend running exactly as-is. Build a new backend targeting the `RECOMMENDED_ARCHITECTURE.md` design (DBOS + AG-UI + workflow authoring) from scratch, using the current codebase as a reference specification. When complete and tested, perform a hard cutover and shut down the old system.

**Path C — Greenfield New Build (Intentional Redesign)**  
Build a new backend implementing the `RECOMMENDED_ARCHITECTURE.md` design without a requirement to reproduce all existing behavior. Explicitly decide what to keep, what to improve, and what to drop. Not a rewrite of the old system — a new product serving the same user needs with a better execution model. The recommended target is NOT the PR #127 design as written: DBOS replaces the custom runtime+arq, AG-UI replaces the custom SSE schema, and workflow authoring is an additive layer.

---

## 3. Path A: Gradual Refactoring (Strangler Fig)

### How It Works

Identify "choke points" that both old and new code flow through, build new behavior behind the new abstraction, route traffic incrementally, and retire old code segment by segment.

For this codebase, the natural choke points are:
- `RoomMessageCenter.process_room_user_message()` — all execution starts here
- `AgentResponseHandler` — all events should flow through here (per `EVENT_PIPELINE_DESIGN.md`)
- `broadcast_to_room()` in `SSEManager` — all SSE output flows here

The PR #127 migration plan phases this as:
- **Phase 1a (Reliable)**: Introduce `ExecutionRun` table, `arq` worker queue, outbox pattern for atomic state+event commits
- **Phase 1b**: Migrate direct/passthrough workflow to new runtime; project into existing `RoomAgentMessage` schema
- **Phase 1c**: Migrate supervisor V2, debate, HITL workflows
- **Phase 2 (Scalable)**: Full multi-instance horizontal scaling
- **Phase 3 (Fast Iteration)**: Module boundary enforcement, developer tooling

> **Note on arq**: arq has been in maintenance-only mode since October 2025. Phase 1a of the PR #127 plan builds on a dying dependency. The recommended replacement is SAQ (drop-in, same Redis API) for the short term, or DBOS (preferred — replaces the entire custom runtime, not just the queue). See `RECOMMENDED_ARCHITECTURE.md`.

### Strengths

- **Zero production regression risk during build.** Old code runs until the new path is proven on real traffic. Each phase is independently shippable and reversible.
- **Living specification.** The existing code is always the ground truth. Edge cases can't be forgotten — they're still running.
- **Incremental value delivery.** Phase 1a (outbox, `arq`, durable inbox) delivers production reliability improvements weeks into the work, not after months.
- **No data migration cliff.** Old and new data models coexist progressively via projection.
- **No maintenance window required.** Cutover per workflow type is invisible to users.

### Weaknesses

- **Design compromises required during transition.** The new `ExecutionRun` model must coexist with `RoomAgentMessage` schema, `SupervisorTrajectory` blobs, and `pending_continuation` for months. The clean entity model gets bridged to the old schema via projection, which is real engineering complexity.
- **Migration machinery gets thrown away.** Dual-write bridges, compatibility shims, and projection layers are temporary code written only to enable the migration.
- **Hardest paths migrate last.** Supervisor V2 resume, debate mode, and HITL multi-agent are the most complex — they go last, meaning the team lives with them the longest.
- **The 80% stall problem.** Strangler projects notoriously stall: the last 20% (the most complex flows) never gets migrated because "the old path still works" and newer feature work takes priority.

### What PR #127's Plan Specifically Requires

Phase 1b alone is substantial:
- Contract tests for all existing execution behavior (prerequisite to any migration)
- `ExecutionRun/Step/Invocation/HumanInterruption` table creation and lifecycle management
- `RunInboxEvent` durable inbox for all external inputs  ← **replaced by `DBOS.send/recv`** in `RECOMMENDED_ARCHITECTURE.md`
- `run_outbox_events` atomic outbox for state+event commits  ← **replaced by DBOS atomic step semantics** in `RECOMMENDED_ARCHITECTURE.md`
- `arq` worker for durable background task processing  ← **replaced by `@DBOS.workflow()`**; see arq note in §3
- Projection layer mapping new entities → existing `RoomAgentMessage` schema

This is 4–8 weeks of engineering for Phase 1b alone before a single user workflow is migrated.

### LOE Estimate

> **Note**: These LOE estimates were written against the original PR #127 architecture and do not account for the additional scope in `RECOMMENDED_ARCHITECTURE.md`. Specifically: (a) DBOS introduction replaces Phase 1a/1b machinery and reduces that effort, but (b) AG-UI adoption + streaming unification (`PERSISTENCE_UNIFICATION_DESIGN.md`) adds 3–5 weeks not reflected below. Net effect is roughly neutral, but the work is different in character — less custom infrastructure, more integration and migration.

| Phase | Description | Estimated Duration |
|---|---|---|
| Phase 1a | Reliability: outbox, inbox, arq | 4–6 weeks |
| Phase 1b | Migrate direct workflow to new runtime | 4–6 weeks |
| Phase 1c | Migrate supervisor V2, debate, HITL | 6–10 weeks |
| Phase 2 | Full horizontal scaling | 4–6 weeks |
| Phase 3 | Module boundaries | 6–8 weeks |
| **Total** | | **24–36 weeks** |

---

## 4. Path B: Parallel Rebuild (Hard Cutover)

### How It Works

Build a complete new backend implementing the `RECOMMENDED_ARCHITECTURE.md` design (DBOS + AG-UI + workflow authoring) from scratch. The existing backend continues running in production. When the new backend reaches full feature parity and passes integration tests, perform a hard cutover: migrate data, update DNS/load balancer, shut down old system.

The existing codebase serves as the behavioral specification: every method, every edge case, every SSE event format is a reference to be reproduced in the new system.

### Strengths

- **No design compromise.** Build `ExecutionRun/Step/Invocation` from day one, no bridging.
- **No migration machinery.** No dual-write bridges, no projection layers, no compatibility shims.
- **Clean test surface.** Write tests for the new system's behavior, not to protect old behavior during migration.
- **No interference with production.** Old system runs without being touched during the build.
- **Team can work without worrying about breaking prod.** New codebase is a greenfield workspace.

### Weaknesses

#### The Cutover Problem (Severe for This System)

A "conversation" in progress at cutover time involves state spread across:
- `room_agent_messages` with embedded task state
- Continuation blobs on message documents or in `pending_continuation` fields
- HITL state in `hitl_requests` collection
- Supervisor trajectories in `extend_info` fields inside message documents
- In-flight LLM or agent calls at the transport level

There is no clean moment to migrate an in-progress conversation. Options are:
1. Drain all in-progress work before cutover (requires maintenance window or draining period)
2. Accept that in-flight conversations are dropped at cutover
3. Write a complex state migration that maps all of the above into `ExecutionRun` entities — which requires understanding every field's meaning across schema variations

**The data migration is a one-shot, high-stakes operation on production data.**

#### The Moving Target Problem

While building the new backend (estimated 4–6 months), the existing backend continues to receive bug fixes, edge-case patches, and new features. Every production change creates a discrepancy that is either:
- Debt on the new system (must be replicated), or
- An intentional divergence (must be documented as such)

Managing this requires either freezing the old backend (not viable) or dual-tracking every change (expensive).

#### The Parity Illusion

`room_services.py` alone is 3,635 lines. `database_service.py` is 2,309 lines. These are not thin wrappers — they encode implicit decisions:

- What happens to the SSE room when a processing status transitions from `completed` to a second `completed`? (There's a dedup cache.)
- What artifact backfill fires when `message_text` is non-empty but `artifacts` is empty? (There's a silent synthesizer.)
- What continuation schema variant applies when HITL resumes via supervisor V2 vs. queue path?
- When does compaction trigger and what context window heuristic applies?

None of these are documented to a level that allows confident reproduction from scratch.

### LOE Estimate

| Phase | Description | Estimated Duration |
|---|---|---|
| Behavioral audit | Document every implicit behavior | 2–3 weeks |
| New backend build | Full implementation of PR #127 architecture | 14–20 weeks |
| Integration testing | Parity validation, SSE contract tests | 3–4 weeks |
| Data migration tooling | MongoDB schema transformation scripts | 2–3 weeks |
| Cutover rehearsal | Staged test migrations | 1–2 weeks |
| **Total** | | **22–32 weeks** |

### Verdict

Similar total LOE to Path A, but with the risk concentrated in a single cutover event rather than spread across many small deployments. With <100 users, the cutover risk is manageable. With 1,000+ users (post-marketing push), it becomes much harder to justify a maintenance window.

---

## 5. Path C: Greenfield New Build (Intentional Redesign)

### How It Works

Build a new backend implementing the `RECOMMENDED_ARCHITECTURE.md` design (DBOS + AG-UI + workflow authoring), making explicit decisions about which existing behaviors to keep, which to improve, and which to drop. Not constrained by parity — constrained by explicit product decisions.

The key difference from Path B is the starting posture: **"we will knowingly change some behaviors"** rather than "we must reproduce everything exactly."

### Strengths

- **Maximum design freedom.** The new execution model is built as designed, no constraints from the old model.
- **Second-iteration wisdom.** The domain is much better understood now than when the current system was written.
- **Can be faster than Path B.** Skipping the behavioral audit and parity validation work can save 3–6 weeks.
- **Opportunity to simplify.** Some of the current system's complexity exists because of incremental patches. A clean build can make cleaner decisions.
- **Explicitly aligns with "we're improving execution logic anyway."** The team already intends to change behaviors — this path acknowledges it directly.

### What Stays Non-Negotiable Regardless

Even with intentional redesign, some parity is not optional:

**The SSE/frontend contract.** Every SSE event the frontend consumes has a name, shape, and timing the frontend hardcodes. `task_update`, `artifact_update`, `processing_status`, `task_submitted`, `hitl_input_requested` — these must match exactly unless the frontend changes simultaneously. A coordinated cutover with frontend changes is feasible; silent behavioral changes are not.

**Production data.** Existing conversations, tasks, artifacts, and HITL interactions don't disappear. A data migration or backward-compatible read layer is required regardless of how clean the new schema is.

**Integration points.** A2A protocol, hub relay, gateway API, agent registration — these have external contracts that cannot be changed unilaterally.

### The Second System Effect Risk

Fred Brooks identified this pattern in 1975: the second system is the most dangerous because designers bring accumulated deferred improvements, generalization, and "wished-for features" all at once. The result is over-engineering that delays delivery.

In this context, the PR #127 designs (8 business modules, `ExecutionRun/Step/Invocation/Interruption`, outbox/inbox, arq, LLM gateway, Hub federation) represent exactly the kind of comprehensive rethinking that triggers the second system effect. The risk is not that the designs are wrong — they are thoughtful. The risk is building all of it before shipping.

### The Key Discipline Required

Path C succeeds when the team:
1. **Decides upfront what behavior changes are acceptable** — documented as explicit change records, not discovered post-launch
2. **Coordinates frontend changes as part of the cutover** — not as a follow-up
3. **Scopes the MVP aggressively** — the new system ships the simplest version that covers the core workflows (direct, passthrough), not all 8 modules
4. **Keeps the old system running until the new one is proven** — the option to roll back should exist until launch

### LOE Estimate

| Phase | Description | Estimated Duration |
|---|---|---|
| Behavioral decision audit | Explicit accept/change/drop decisions per behavior | 1–2 weeks |
| New backend MVP build | Core execution path, direct workflow, SSE, API | 8–12 weeks |
| Extended coverage | Supervisor V2, debate, HITL, Hub | 4–8 weeks |
| Integration testing | SSE contract tests, data migration | 2–3 weeks |
| Cutover coordination | Frontend alignment, migration rehearsal | 1–2 weeks |
| **Total** | | **16–27 weeks** |

---

## 6. How the User Count Changes the Math

The <100 active user context is a genuine strategic asset that expires. It changes the risk profile of each path in specific ways.

### What Low User Count Enables

**Maintenance windows are feasible.** A 2-hour maintenance window for a hard cutover is disruptive for 10,000 users; it is barely noticed by 50.

**Behavioral changes are cheaper to communicate.** If the new system behaves differently on edge cases, there are very few people who hit those edge cases. Bugs surface quickly, get fixed quickly, and affect few people.

**Rollback is less costly.** If the new backend has a critical bug after cutover, reverting to the old system affects almost nobody. This safety net disappears once the marketing push succeeds.

**Data migration complexity is lower.** Fewer users means fewer documents in edge states (in-flight HITL, supervisor V2 with saved continuations, multi-step debate runs). The data migration surface is smaller.

### What Low User Count Does Not Change

**The SSE/frontend contract is still non-negotiable.** Even one user hitting a broken SSE event shape means a broken product. The frontend contract must be handled correctly regardless of user count.

**Technical debt compounds regardless.** The architectural problems identified in `SYSTEM_DESIGN_REVIEW.md` (message-centric execution state, fragmented SSE routing, 5 overlapping runtimes) don't disappear because there are few users. They make future feature development slower regardless of user count.

**The marketing push changes the deadline, not the scope.** Whatever path is chosen, it must reach "production-grade" before or very shortly after the marketing push succeeds. The window for risky moves is the pre-growth period — which is now.

### Revised Path Assessment Given Low User Count

| Path | With 10K users | With <100 users |
|---|---|---|
| **Strangler** | Best risk profile | Good — but slow to deliver the new architecture |
| **Parallel Rebuild** | High cutover risk | **Manageable** — cutover affects few users |
| **Greenfield** | Very high regression risk | **Manageable** — regressions surface fast, affect few |

The low user count effectively neutralizes the biggest risk of Paths B and C: catastrophic cutover impact. It does not neutralize the technical execution risks (parity gaps, moving target, second system effect), but it makes recovery from those risks much less consequential.

**The window for this advantage closes the moment the marketing push succeeds.**

---

## 7. Industry Evidence

### The Consensus Position

Analysis of 94 refactor-vs-rewrite decisions (2021–2025) shows:
- Rewrite projects: **21% success rate**, median cost $2.1M, median timeline 19.4 months
- Refactor projects: **68% success rate**, median cost $240K, median timeline 5.2 months

The industry default recommendation is strongly against rewrites. However, the key qualification in every major analysis is: **the success rate of rewrites is correlated with scope**. Full-system rewrites have the worst outcomes. Bounded service rewrites with clear contracts succeed more often.

### The Wix Case Study

Wix rewrote hundreds of services (out of ~3,000 total) across 40+ product groups. Their key methodological findings:

1. **Scope size is the critical factor.** Too large → slow value delivery and constant feature-sync between old and new systems. Too small → excessive integration complexity. Their recommendation: rewrite at complete business-flow boundaries within a service.

2. **One-directional sync only.** They tried bidirectional sync (old ↔ new) and found it creates split brain, race conditions, and conflict resolution problems with no real benefit. One-way sync (old → new) is simpler and sufficient.

3. **Gradual rollout by tenant.** They rolled new systems out to new users first, then gradually migrated existing users. This creates a natural validation period before full commitment.

4. **Backwards compatibility is explicit work.** Every Wix service rewrite included a dedicated phase for backwards API compatibility — not as an afterthought but as a first-class deliverable.

### The Wix CI Migration

Wix migrated a CI system running ~10,000 builds/day with zero downtime using a "parallel dry-run" strategy:
- **Legacy mode**: builds run only on the old system
- **New system mode**: builds run only on the new system
- **Parallel mode**: builds run on both, with new system in dry-run (side effects disabled)

This worked because there was a clean `dry_run` flag that could disable side effects in the new system. **This specific technique does not apply cleanly to this backend** because the side effects (MongoDB writes, SSE events, task state transitions) are interleaved throughout execution, not cleanly separable from computation.

### When Rewrites Have Worked

Rewrites succeed when:
1. The existing system is small enough to fit in one team's head
2. The behavior surface is well-documented or simple enough to enumerate
3. The new system can receive a subset of traffic in parallel (shadow mode) for validation
4. The cutover can be performed per-tenant or per-workflow, not all-at-once

This backend meets condition 1 marginally (it's large but owned by a small team), does not meet condition 2 (behavior is embedded in code), partially meets condition 3 (with investment in shadow mode tooling), and does not meet condition 4 naturally (no clean per-tenant routing today).

---

## 8. Recommendation

### The Right Path Given Current Context

**Path C (Greenfield New Build, Intentional Redesign) — scoped aggressively — is the recommended path, with a disciplined MVP first.**

This recommendation depends on maintaining the following non-negotiable constraints:

#### Constraint 1: Keep the existing backend running until cutover

The old system must not be touched or degraded during the build. It remains the production system. This ensures there is always a rollback option.

#### Constraint 2: Scope the MVP to the simplest workflows first

The first production deployment of the new backend covers only:
- Direct/passthrough workflow (single agent, non-supervisor)
- Core SSE events (task_submitted, artifact_update, task_update, processing_status)
- Basic room and message CRUD

**Not in MVP:**
- Supervisor V2 (3 resume paths, saved continuations)
- Debate mode (multi-agent sequencing)
- HITL (interruption/resume state machine)
- Hub relay federation
- Memory system integration

Route only "new rooms" or a specific room type to the new backend initially. Legacy workflows continue on the old backend.

#### Constraint 3: Start with an explicit behavioral decision record

Before writing production code, spend 1–2 weeks producing a `BEHAVIORAL_DECISIONS.md` that enumerates every known behavior and makes an explicit decision:
- **Keep**: reproduce exactly in the new system
- **Improve**: specify the new behavior explicitly
- **Drop**: accept this edge case won't be handled in v1 of the new system

This document is the risk mitigation for the "parity illusion" problem. It turns unknown unknowns into known trade-offs.

#### Constraint 4: Frontend changes are coordinated, not reactive

If any SSE event shapes, timing, or API response schemas change in the new backend, those frontend changes are done in the same deployment window — not discovered post-launch.

#### Constraint 5: Cutover before the marketing push succeeds

The low user count advantage expires. The goal is to be on the new system while the risk of cutover is still low, not after acquiring 10K users.

### Why Not Path A (Strangler)?

Path A is the safer choice for a system with many users and cannot afford disruption. Given <100 users, the safety it provides is not necessary, and its costs are real:
- 24–36 weeks to full completion
- Months of dual-system complexity (projection layers, migration shims)
- Risk of 80% stall where the hardest paths never migrate
- The new architecture stays compromised until Phase 3

The current window — small user base, clear architectural vision, motivated team — is exactly when Path C is most defensible.

### Why Not Path B (Parallel Rebuild with Full Parity)?

Path B has the same timeline as Path A without its incremental value delivery. The behavioral audit required for full parity (2–3 weeks) slows the start without producing a better system — it just produces a copy of the current system in the new architecture. Starting with intentional design decisions (Path C) is faster and produces a better outcome.

### The Hybrid Execution Strategy

In practice, Path C should be executed as a modified Wix-style rollout:

```
Week 0–2:    Behavioral decision audit (BEHAVIORAL_DECISIONS.md)
             Frontend SSE contract tests (regression baseline)

Week 2–14:   New backend MVP (direct workflow, core SSE, API surface)
             New rooms routed to new backend (shadowed: same DB, separate execution)
             Old backend handles all existing rooms
             Target: RECOMMENDED_ARCHITECTURE (DBOS + AG-UI) — NOT PR #127 as written

Week 14–18:  Extended workflows (supervisor, debate, HITL)
             Parallel shadow mode on new backend for validation
             (Hub/relay is already fully implemented — only DBOS integration gap remains)

Week 18–20:  Data migration tooling + rehearsal
             Frontend alignment for any intentional contract changes
             (Coordinate AG-UI SSE event shape changes with frontend team)

Week 20–22:  Cutover: new rooms → new backend (permanent)
             Old rooms migrate over 2–4 weeks
             Old backend decommissioned

Total:       ~22 weeks to full cutover
```

This captures the design cleanliness of Path C with the per-workflow rollout safety of Path A.

---

## 9. Decision Criteria Summary

| Criterion | Path A (Strangler) | Path B (Parallel Rebuild) | Path C (Greenfield) |
|---|---|---|---|
| **Total LOE** | 24–36 weeks | 22–32 weeks | 16–27 weeks |
| **Design cleanliness** | Compromised during migration | Full (from day 1) | Full (from day 1) |
| **Production risk during build** | Low | Low | Low |
| **Cutover risk (large user base)** | Very low | High | High |
| **Cutover risk (<100 users)** | Very low | **Manageable** | **Manageable** |
| **Moving target problem** | None | Severe | Moderate (bounded by decision record) |
| **Second system effect risk** | Low | Medium | Medium (mitigated by scope discipline) |
| **Incremental value delivery** | Yes — weeks | No — months | Partial — months |
| **Stall risk** | High (80% problem) | Low | Low |
| **Data migration complexity** | Incremental | One-shot | One-shot |
| **Recommended for <100 users** | Over-cautious | Viable | **Preferred** |
| **Recommended for >10K users** | **Preferred** | Risky | Very risky |

### Bottom Line

The low user count is a strategic window. Use it. Path C with aggressive MVP scoping and a behavioral decision audit is the right choice now. It would not be the right choice in 6 months after the marketing push succeeds.

The single most important execution constraint is **keeping the old system running** until the new one is proven. Do not treat the old backend as the source of code to modify. Treat it as the reference spec and the production safety net.

---

## Appendix: Complexity Inventory

### High-Risk Behaviors (Must Be in Behavioral Decision Record)

These behaviors are embedded in code, not documented, and have material user-facing impact:

| Behavior | Location | Decision Needed |
|---|---|---|
| Text artifact backfill from `message_text` when artifacts empty | `room_services.py` | Keep / Improve |
| Terminal status dedup via `_terminal_status_sent` TTLCache (now Redis) | `sse_services.py` | Keep |
| `notify_task_update` bypass of broadcaster for terminal events | `task_notification_service.py` | Improve (broadcaster path) |
| Supervisor V2 three distinct resume paths | `RoomMessageCenter._resume_supervisor_v2` | Keep / Document |
| Debate mode multi-agent sequencing and result aggregation | `DebationCenter`, `WorkflowCenter` | Keep / Improve |
| HITL interrupt/resume state with artifact and task state preservation | `hitl_service.py`, `SupervisorExecutor.py` | Keep |
| Compaction trigger conditions and context window heuristics | `compaction_service.py`, `room_services.py` | Keep / Improve |
| SSE event ordering: `task_submitted` before `artifact_update` before `task_update(completed)` | `direct.py`, `AgentResponseHandler` | Keep (frontend depends on this) |
| `processing_status` side effects: dedup + DB persistence alongside broadcast | `sse_services.py` | Improve (separate concerns) |
| Continuation blob storage variants (message-level vs. user-message-level) | `SupervisorExecutor.py`, `RoomMessageCenter.py` | Replace with DBOS `send/recv` — continuation blob storage disappears entirely when DBOS handles resume state |
| Hub relay offline queue and reconnection semantics | `relay_service.py` | Keep (upgrade offline queue to DBOS `@DBOS.workflow()` — Phase 2 of RECOMMENDED_ARCHITECTURE) |
| A2A agent health check and capability issue detection | `agent_health_service.py`, `agent_capability_issue_service.py` | Keep |

### SSE Events Requiring Frontend Contract Tests

> **Note**: This list reflects the **current** (pre-AG-UI) SSE event schema. Once `RECOMMENDED_ARCHITECTURE.md` Phase 3 is complete, these custom events are **replaced** by AG-UI protocol events (`RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `TOOL_CALL_START`, `RUN_FINISHED`, etc.). The contract tests listed here serve as the **baseline** that AG-UI adoption must match or supersede; they are still required to gate Phase 1 → Phase 2 migration but become obsolete after Phase 3.

Before any cutover, these events must have automated contract tests validating shape and timing:

- `task_submitted` (field: `task_id`, `agent_name`, `agent_id`, `step_number`, `total_steps`, `status`)
- `task_update` (field: `status` values, `content`, `error`, `requires_input`, `requires_auth`, `parts`)
- `artifact_update` (field: `artifact`, `append`, `last_chunk`, `agent_id`)
- `processing_status` (field: `status`, `details`, `message_id`, `client_request_id`)
- `hitl_input_requested` (field: `request_id`, `message_id`, `question`)
- `hitl_status_update` (field: `request_id`, `status`)
- `agent_response` (field: `content`, `parts`)
- `error` (field: `message`)

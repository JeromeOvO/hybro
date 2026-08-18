# HITL lifecycle

## R0 contract foundation

`common/dto/hitl.py` defines the dependency-neutral contracts for the next HITL
lifecycle without changing current routes or payloads. It separates questionnaire,
authorization, and policy interactions; models answers as a strict discriminated
union; treats cancellation as a version-fenced command; and captures immutable,
route-validated application snapshots with canonical fingerprints. Raw agent input
observations remain private runtime data on
`execution.dispatch.agent_event.AgentEvent` and are not fields on public delivery
DTOs.

## Continuation and failure invariants

Hybro treats the remote A2A `Task.id` and `Task.contextId` returned by the agent as
the authoritative continuation identity. Provisional `pending-*` identifiers are
local dispatch placeholders only and must be replaced when a remote task is
acknowledged. Interactive task projections persist the authoritative identifiers
and the concrete, sanitized question before creating a HITL request.

A2A JSON-RPC, protocol, and transport errors are task/routing failures. They are
never converted to `input-required`. Likewise, an interactive response without a
concrete public question or valid remote continuation identifiers fails with a
typed internal error instead of exposing a generic answer form.

Canceling or expiring required HITL terminalizes its owning orchestration run and
projects the same terminal outcome to the public run. Stale-agent failure uses a
failed outcome; explicit user cancellation uses canceled. Terminal reconciliation
clears pending HITL IDs and open continuation state, and remains retryable through
the HITL request's `cancellation_reconciled` marker.

The stale task checker first attempts to recover authoritative remote identifiers
from trusted HITL metadata or the saved continuation before classifying a
provisional task as never acknowledged.

## Milestone 2 durable interaction/application lifecycle

`hitl_requests` remains the compatibility projection for individual questions. The
`hitl_interactions` aggregate owns whether the complete questionnaire is visible,
which required answers are recorded, the shared earliest deadline, and whether its
single application revision is pending or complete. Requests are not emitted until
the aggregate is `open`; grouped requests stay `materializing` until all expected
request IDs are durably attached.

| Interaction state | Meaning | Next states |
| --- | --- | --- |
| `materializing` | Questions are still being attached; not user-visible | `open`, `failed` |
| `open` | Awaiting required answers | `partially_answered`, `answers_recorded`, `expired`, `canceled` |
| `partially_answered` | At least one immutable answer is recorded | `answers_recorded`, `expired`, `canceled` |
| `answers_recorded` | All required answers are durable | `applying` |
| `applying` | One fenced application revision owns a lease | `applied`, `answers_recorded`, `delivery_uncertain`, `failed` |
| `delivery_uncertain` | A2A delivery may have occurred; automatic resend is forbidden | operator/GetTask reconciliation |
| `applied` | Run/remote effects are durable; request/UI finalization is retryable | terminal |
| `expired`, `canceled`, `failed` | Terminal interaction outcomes | terminal reconciliation |

Remote A2A continuation is journaled in `hitl_resume_commands`. Each
`(interaction_id, application_revision, kind)` has one stable outbound A2A message
ID. A definite connection failure becomes `retryable_error`; a timeout or lost
worker after delivery begins becomes `delivery_uncertain` and is inspected with
GetTask instead of resent. Command documents reference answer request IDs and a
digest; plaintext answers remain only on the access-controlled request documents.

Deadlines are authoritative in Mongo claim and pending-read queries. Legacy missing
or null deadlines remain non-expiring. The leader-gated stale checker runs bounded
passes in this order: cancellation, interaction deadline/application/command
reconciliation, then generic stale-agent processing. Lazy synthesis makes startup
non-blocking; `scripts/backfill_hitl_interactions.py` is dry-run unless `--apply` is
explicitly supplied and reports conflicting legacy groups rather than guessing.

### Recovery invariants added after Milestone 2 review

Application and A2A command leases are fenced and renewed during blocking remote
calls. An `applying` retry retains its claim and expires the lease instead of
removing ownership. `acknowledged` and `projected` command rows remain recoverable
until their interaction is durably `applied`; a confirmed uncertain delivery takes
the same projection/application path without resending the A2A message.

Run-answer projection has its own idempotent claim/status journal on the interaction
and is required for both Agent and Supervisor sources before `applied`. Reconciliation
replays incomplete run projection and request/UI projection. Required request IDs,
ordered group indices, plaintext-answer digests, and aggregate answer references are
verified before any external effect. Group-open projection is ordered and uses
per-request durable claim/completion markers so a crash can replay every member.

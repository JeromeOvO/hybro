# TODOS

## Timeline Redesign — Deferred Items

### TODO: Backend persistent event stream for timeline
- **What:** Replace frontend-only event-log.ts with backend-persisted event stream
- **Why:** Current event-log is in-memory append-only. Page refresh loses all event history. Old turns collapse and don't show events, so this is acceptable for v1, but users who expand old turns after refresh will see no event rail.
- **Pros:** Full event history survives page refresh. Enables event rail for all turns, not just active.
- **Cons:** Requires backend SSE protocol changes. Adds persistence and query complexity.
- **Context:** Codex review identified that the spec designs a UI wanting durable event history while constraining to a snapshot store. The event-log.ts accumulator is the v1 compromise. When backend provides persistent events (likely part of the new hybro-backend Interaction module), replace event-log.ts with backend-sourced events.
- **Depends on:** hybro-backend Phase 7 (Interaction Layer) providing typed domain events.

### TODO: Artifact event normalization — preserve emission history
- **What:** Prevent SSE ingest from destroying artifact emission history by promoting text artifacts into content
- **Why:** In sse-handlers/index.ts, text-only artifacts are merged into message content during ingest. After this merge, the builder cannot reconstruct that an artifact_emitted event occurred. The event-log accumulator captures events before merge, but this is lost on refresh.
- **Pros:** Accurate artifact timeline even after page refresh. Cleaner separation of content vs artifacts.
- **Cons:** Changing SSE ingest normalization could affect existing message-bubble rendering that relies on the current merge behavior.
- **Context:** Identified by Codex outside voice during eng review. The event-log.ts partially mitigates this for active sessions. Full fix requires either preserving artifact metadata separately or changing the ingest pipeline.
- **Depends on:** Backend persistent event stream (above) would make this unnecessary.

### TODO: Turn navigation sidebar for long rooms
- **What:** Add a sidebar or floating navigator for rooms with 50+ turns
- **Why:** With many turns, scrolling to find a specific conversation becomes tedious. A compact turn navigator (showing user prompt previews) would enable quick jumping.
- **Pros:** Fast navigation in long conversations. Better UX for power users.
- **Cons:** Additional UI complexity. Needs responsive behavior for mobile.
- **Context:** Explicitly deferred in design review (Section 23.1). Validate the core timeline works first.
- **Depends on:** Timeline redesign v1 completion.

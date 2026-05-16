# Phase 7a Delivery Extraction Handoff

Phase 7a removed the run-lifecycle write and legacy `run_event` broadcast from
`SSEManager.send_processing_status()`. Production run-lifecycle owners now record
before emitting `processing_status`, and `SSEManager` is a transport-only sender
for processing-status frames.

This does not prove every non-run business side effect is ordered before
Delivery. Phase 6 must clear the remaining post-emit side-effect audit before
extracting Delivery.

## Remaining Audit Items

- `modules/RoomMessageCenter.py`: the queue failure path in
  `_process_room_user_message_locked` emits `FAILED` `processing_status` before
  the `turn_failed` append and before
  `_notify_all_non_terminal_tasks_failed(...)`.
- `modules/RoomMessageCenter.py`: root queue completion emits `COMPLETED`
  `processing_status` before `turn_event_appender.append("turn_completed", ...)`.
- `modules/RoomMessageCenter.py`: supervisor V2 execution failure emits
  `FAILED` `processing_status` before
  `_notify_all_non_terminal_tasks_failed(...)`.
- `modules/RoomMessageCenter.py`: supervisor V2 resume failure branches emit
  `FAILED` `processing_status` before
  `_notify_all_non_terminal_tasks_failed(...)` when trajectory deserialization
  fails, the room lookup fails, or resumed executor execution fails.
- `modules/RoomMessageCenter.py`: V2 `RunStatus.COMPLETED` emits `COMPLETED`
  `processing_status` before `turn_event_appender.append("turn_completed", ...)`.
- `modules/RoomMessageCenter.py`: V2 `RunStatus.CANCELED` and
  `RunStatus.FAILED` emit terminal `processing_status` before terminal
  `turn_event_appender.append(...)` calls and before
  `_notify_all_non_terminal_tasks_failed(...)`.
- `modules/RoomMessageCenter.py`: terminal V2 post-loop integration still runs
  after terminal `processing_status` emit, including synthesis history writes,
  room summary update, and compaction.
- `modules/RoomMessageCenter.py`: the `RunStatus.CLARIFYING` soft-complete path
  emits frontend `COMPLETED` before `turn_event_appender.append("turn_completed",
  ...)`. This is not run-lifecycle terminalization, but it is still a post-emit
  business side effect.

## Phase 6 Gate

Before Delivery extraction proceeds, each listed side effect must either:

- move before the corresponding `processing_status` emit, or
- be explicitly classified as best-effort/non-blocking with tests proving
  Delivery extraction does not require callbacks into business modules.

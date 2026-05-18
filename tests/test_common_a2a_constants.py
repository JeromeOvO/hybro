from a2a.types import TaskState

from common.a2a_constants import (
    CommonTaskState,
    SSEProcessingStatus,
    get_retry_after_seconds,
    is_failure_state,
    is_interactive_state,
    is_pending_state,
    is_terminal_state,
    normalize_task_state_value,
)


def test_common_a2a_constants_are_sdk_free_but_accept_enum_like_values():
    assert normalize_task_state_value(TaskState.completed) == "completed"
    assert normalize_task_state_value("completed") == "completed"
    assert is_terminal_state(TaskState.completed)
    assert is_terminal_state("completed")
    assert is_interactive_state(TaskState.input_required)
    assert is_interactive_state("input-required")
    assert is_pending_state(TaskState.working)
    assert is_pending_state("working")
    assert is_failure_state(TaskState.rejected)
    assert is_failure_state("rejected")
    assert not is_terminal_state("not-a-state")


def test_common_a2a_retry_policy_matches_legacy_contract():
    assert get_retry_after_seconds(TaskState.completed) is None
    assert get_retry_after_seconds(CommonTaskState.INPUT_REQUIRED) == 60
    assert get_retry_after_seconds(TaskState.working) == 30


def test_processing_status_values_are_shared_strings():
    assert SSEProcessingStatus.COMPLETED.value == "completed"
    assert SSEProcessingStatus.AWAITING_INPUT.value == "awaiting_input"

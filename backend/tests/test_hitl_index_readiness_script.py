from scripts.check_pending_hitl_unique_index_readiness import (
    EXIT_DUPLICATES_FOUND,
    EXIT_SUCCESS,
    build_duplicate_pipeline,
    exit_code_for_report,
)


def test_duplicate_pipeline_scopes_to_pending_agent_hitl_strings():
    pipeline = build_duplicate_pipeline("display_message_id")

    assert pipeline[0] == {
        "$match": {
            "status": "pending",
            "source": "agent",
            "display_message_id": {"$type": "string"},
        }
    }
    assert pipeline[1]["$group"]["_id"] == {
        "room_id": "$room_id",
        "display_message_id": "$display_message_id",
    }
    assert pipeline[2] == {"$match": {"count": {"$gt": 1}}}
    assert pipeline[3]["$project"]["request_ids"] == 1


def test_duplicate_report_exit_code_flags_any_identity_duplicates():
    assert (
        exit_code_for_report(
            {
                "display_message_id": [],
                "continuation_message_id": [],
            }
        )
        == EXIT_SUCCESS
    )

    assert (
        exit_code_for_report(
            {
                "display_message_id": [{"room_id": "room-1", "count": 2}],
                "continuation_message_id": [],
            }
        )
        == EXIT_DUPLICATES_FOUND
    )

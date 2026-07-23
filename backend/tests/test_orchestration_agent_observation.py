from execution.orchestration.agent_observation import extract_agent_observation


def test_extracts_missing_fields_as_unknowns_and_candidate_blockers():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Submission is partial; see structured artifact details.",
        status_message=None,
        artifact_records=[
            {
                "artifact_key": "agent-msg-1:artifact_id:submission",
                "source_agent_message_id": "agent-msg-1",
                "source_agent_id": "agent-1",
                "name": "submission",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "client": {
                                "name": "Example Inc",
                                "industry": None,
                            },
                            "requested_coverage": {
                                "limit": None,
                                "retention": 25000,
                            },
                            "missing_fields": [
                                "client.industry",
                                "requested_coverage.limit",
                            ],
                        },
                    }
                ],
            }
        ],
    )

    assert {item.key for item in observation.unknowns} == {
        "agent_missing:agent-1:client.industry",
        "agent_missing:agent-1:requested_coverage.limit",
    }
    assert [item.key for item in observation.blocker_candidates] == [
        "agent_blocker:agent-1:client.industry",
        "agent_blocker:agent-1:requested_coverage.limit",
    ]
    assert all(item.source == "agent" for item in observation.blocker_candidates)
    assert all(
        item.validation_status == "candidate" for item in observation.blocker_candidates
    )
    assert all(
        item.validated_user_only is False for item in observation.blocker_candidates
    )
    assert {fact["semantic_key"]: fact["value"] for fact in observation.facts} == {
        "agent_observation:agent-msg-1:submission:client.name": "Example Inc",
        "agent_observation:agent-msg-1:submission:requested_coverage.retention": 25000,
    }


def test_extracts_awaiting_input_status_as_candidate_blocker():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-2",
        agent_id="agent-1",
        status="awaiting_input",
        text=None,
        status_message="Need the requested limit before continuing.",
        artifact_records=[],
    )

    assert [item.key for item in observation.unknowns] == [
        "agent_missing:agent-1:agent_input_required"
    ]
    assert [item.description for item in observation.blocker_candidates] == [
        "Need the requested limit before continuing."
    ]
    assert observation.blocker_candidates[0].claimed_user_only is False
    assert observation.blocker_candidates[0].validation_status == "candidate"


def test_does_not_create_missing_unknown_for_false_zero_or_empty_list():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-3",
        agent_id="agent-1",
        status="completed",
        text=None,
        status_message=None,
        artifact_records=[
            {
                "artifact_key": "agent-msg-3:artifact_id:submission",
                "name": "submission",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "has_claims": False,
                            "claim_count": 0,
                            "operating_countries": [],
                        },
                    }
                ],
            }
        ],
    )

    assert observation.unknowns == []
    assert observation.blocker_candidates == []
    assert {fact["semantic_key"]: fact["value"] for fact in observation.facts} == {
        "agent_observation:agent-msg-3:submission:claim_count": 0,
        "agent_observation:agent-msg-3:submission:has_claims": False,
        "agent_observation:agent-msg-3:submission:operating_countries": [],
    }


def test_ignores_null_values_without_an_explicit_missing_signal():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-4",
        agent_id="agent-1",
        status="completed",
        text=None,
        status_message=None,
        artifact_records=[
            {
                "artifact_key": "agent-msg-4:artifact_id:quote",
                "name": "quote",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "optional_note": None,
                            "optional_endorsement": None,
                            "optional_reference": None,
                        },
                    }
                ],
            }
        ],
    )

    assert observation.facts == []
    assert observation.unknowns == []
    assert observation.blocker_candidates == []


def test_records_completed_text_as_untrusted_evidence_only():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-4",
        agent_id="agent-1",
        status="completed",
        text="Submission is partial. Missing fields: industry, requested limit.",
        status_message=None,
        artifact_records=[],
    )

    assert observation.unknowns == []
    assert observation.blocker_candidates == []
    assert observation.facts == [
        {
            "fact_id": "agent-msg-4:text_evidence",
            "kind": "agent_text_evidence",
            "semantic_key": "agent_text_evidence:agent-msg-4",
            "value": "Submission is partial. Missing fields: industry, requested limit.",
            "source_agent_message_id": "agent-msg-4",
            "source_agent_id": "agent-1",
            "evidence_refs": ["agent-msg-4", "agent-msg-4:text_or_status"],
            "trusted_for_blocker_keys": False,
        }
    ]


def test_records_text_evidence_when_artifacts_have_no_structured_data():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-text-artifact",
        agent_id="agent-1",
        status="completed",
        text="The attached narrative explains the partial result.",
        status_message=None,
        artifact_records=[
            {
                "artifact_key": "agent-msg-text-artifact:artifact_id:narrative",
                "name": "narrative",
                "parts": [{"kind": "text", "text": "Narrative only."}],
            }
        ],
    )

    assert [fact["kind"] for fact in observation.facts] == ["agent_text_evidence"]
    assert observation.facts[0]["value"] == (
        "The attached narrative explains the partial result."
    )


def test_extracts_status_message_need_as_candidate_only():
    observation = extract_agent_observation(
        agent_message_id="agent-msg-5",
        agent_id="agent-1",
        status="completed",
        text=None,
        status_message="Need requested limit before continuing.",
        artifact_records=[],
    )

    assert [item.key for item in observation.unknowns] == [
        "agent_missing:agent-1:requested_limit"
    ]
    assert observation.blocker_candidates[0].description == (
        "Agent text indicates missing input: requested limit"
    )

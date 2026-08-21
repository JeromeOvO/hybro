from __future__ import annotations

import inspect

from dal.orchestrator.artifacts import (
    EpochFencedRoomArtifactOwner,
    RoomFileArtifactOwner,
)
from execution.orchestrator.a2a_runtime.dispatch import (
    DirectA2AClient,
    DirectA2AStream,
    RelayCommandJournal,
    RelayCommandSender,
)
from execution.orchestrator.a2a_runtime.persistence import A2A_RUNTIME_COLLECTIONS
from execution.orchestrator.a2a_runtime.ports import (
    A2ADispatchPort,
    AgentCallLedgerStore,
    AgentToolBindingStore,
    AgentToolCandidateSource,
    AuthorizationRefreshPort,
    AuthReferenceVerificationPort,
    HITLApplicationPort,
    NormalizedObservationRecorder,
    ObservationConflictStore,
    ObservationInboxStore,
    ObservationIngressAuthenticator,
    PreparedInvocationSnapshotReader,
    ResourceMaterializerPort,
    RoomEpochStore,
    ToolObservationSink,
)
from execution.orchestrator.ports import (
    InvocationCheckpointReader,
    InvocationOutcomeCheckpointReader,
)


def _index(index):
    return (
        index.keys,
        index.unique,
        dict(index.partial_filter) if index.partial_filter is not None else None,
    )


def test_plan3_collection_and_exact_index_inventory_matches_contract():
    collections = {
        collection.name: collection for collection in A2A_RUNTIME_COLLECTIONS
    }
    assert set(collections) == {
        "orchestrator_agent_tool_bindings",
        "orchestrator_agent_calls",
        "orchestrator_a2a_observations",
        "orchestrator_a2a_observation_conflicts",
        "orchestrator_room_epochs",
    }
    actual = {
        collection_name: {index.name: _index(index) for index in definition.indexes}
        for collection_name, definition in collections.items()
    }
    assert {
        collection_name: {name: details[0] for name, details in indexes.items()}
        for collection_name, indexes in actual.items()
    } == {
        "orchestrator_agent_tool_bindings": {
            "binding_id_unique": (("binding_id", 1),),
            "run_tool_unique": (("run_id", 1), ("tool_name", 1)),
            "binding_epoch_cleanup": (("room_id", 1), ("room_epoch", 1)),
        },
        "orchestrator_agent_calls": {
            "call_record_id_unique": (("call_record_id", 1),),
            "run_invocation_unique": (("run_id", 1), ("invocation_id", 1)),
            "acceptance_id_unique": (("acceptance_id", 1),),
            "call_idempotency_unique": (("idempotency_key", 1),),
            "run_source_unique": (
                ("run_id", 1),
                ("assistant_message_id", 1),
                ("source_index", 1),
            ),
            "call_recovery_due": (
                ("state", 1),
                ("next_attempt_at", 1),
                ("claim_expires_at", 1),
            ),
            "ownership_alias_unique": (("ownership_alias_keys", 1),),
            "pending_interaction_unique": (("pending_interaction_id", 1),),
            "call_epoch_cleanup": (("room_id", 1), ("room_epoch", 1)),
        },
        "orchestrator_a2a_observations": {
            "observation_id_unique": (("observation_id", 1),),
            "observation_source_unique": (("source_identity", 1),),
            "observation_recovery_due": (
                ("state", 1),
                ("next_attempt_at", 1),
                ("claim_expires_at", 1),
            ),
            "observation_binding_cleanup": (("binding_scope", 1),),
            "observation_epoch_cleanup": (("room_id", 1), ("room_epoch", 1)),
        },
        "orchestrator_a2a_observation_conflicts": {
            "observation_conflict_id_unique": (("conflict_id", 1),),
            "observation_conflict_source": (("source_identity", 1),),
            "observation_conflict_epoch_cleanup": (
                ("room_id", 1),
                ("room_epoch", 1),
            ),
        },
        "orchestrator_room_epochs": {
            "room_epoch_room_unique": (("room_id", 1),),
            "room_epoch_high_water": (("room_id", 1), ("high_water_mark", -1)),
        },
    }

    unique_indexes = {
        "binding_id_unique",
        "run_tool_unique",
        "call_record_id_unique",
        "run_invocation_unique",
        "acceptance_id_unique",
        "call_idempotency_unique",
        "run_source_unique",
        "ownership_alias_unique",
        "pending_interaction_unique",
        "observation_id_unique",
        "observation_source_unique",
        "observation_conflict_id_unique",
        "room_epoch_room_unique",
    }
    for indexes in actual.values():
        for name, (_, unique, _) in indexes.items():
            assert unique is (name in unique_indexes), name
    assert actual["orchestrator_agent_calls"]["ownership_alias_unique"][2] == {
        "ownership_alias_keys.0": {"$exists": True}
    }
    assert actual["orchestrator_agent_calls"]["pending_interaction_unique"][2] == {
        "pending_interaction_id": {"$type": "string"}
    }


def test_plan3_protocol_method_and_signature_inventory_is_exact():
    expected = {
        RoomFileArtifactOwner: {
            "write_lease": "(self, room_id: 'str', owner: 'str') -> 'AbstractAsyncContextManager[str | None]'",
            "store_agent_artifact": "(self, *, room_id: 'str', source_message_id: 'str', origin_key: 'str', content: 'bytes', file_name: 'str', mime_type: 'str', max_bytes: 'int', content_sha256: 'str | None' = None) -> 'dict[str, Any]'",
            "content_url": "(self, file_id: 'str') -> 'str'",
        },
        EpochFencedRoomArtifactOwner: {
            "commit": "(self, *, room_id: 'str', room_epoch: 'int', source_message_id: 'str', origin_key: 'str', content: 'bytes', content_sha256: 'str', file_name: 'str', mime_type: 'str', max_bytes: 'int') -> 'str'",
        },
        DirectA2AClient: {
            "send": "(self, command: 'A2ADispatchCommand') -> 'A2ADispatchReceipt'",
            "start_poll": "(self, command: 'A2ADispatchCommand') -> 'A2ADispatchReceipt'",
            "open_stream": "(self, command: 'A2ADispatchCommand') -> 'DirectA2AStream'",
            "inspect": "(self, command: 'A2ADispatchCommand') -> 'A2ADispatchReceipt'",
            "continue_task": "(self, command: 'A2AContinuationCommand') -> 'A2ADispatchReceipt'",
            "inspect_continuation": "(self, command: 'A2AContinuationCommand') -> 'A2ADispatchReceipt'",
            "cancel": "(self, command: 'A2ACancellationCommand') -> 'A2ADispatchReceipt'",
            "inspect_cancellation": "(self, command: 'A2ACancellationCommand') -> 'A2ADispatchReceipt'",
        },
        DirectA2AStream: {
            "__aiter__": "(self) -> 'AsyncIterator[NormalizedA2AObservation]'",
            "close": "(self, *, reason: 'str') -> 'None'",
        },
        RelayCommandJournal: {
            "persist_dispatch": "(self, command: 'A2ADispatchCommand') -> 'str'",
            "persist_continuation": "(self, command: 'A2AContinuationCommand') -> 'str'",
            "persist_cancellation": "(self, command: 'A2ACancellationCommand') -> 'str'",
            "inspect": "(self, command_id: 'str') -> 'A2ADispatchReceipt'",
        },
        RelayCommandSender: {
            "send_dispatch": "(self, command: 'A2ADispatchCommand') -> 'A2ADispatchReceipt'",
            "send_continuation": "(self, command: 'A2AContinuationCommand') -> 'A2ADispatchReceipt'",
            "send_cancellation": "(self, command: 'A2ACancellationCommand') -> 'A2ADispatchReceipt'",
        },
        AgentToolCandidateSource: {
            "list_candidates": "(self, *, run_id: 'str', room_id: 'str', room_epoch: 'int', requesting_subject_id: 'str', candidate_agent_ids: 'list[str]') -> 'list[AgentToolCandidate]'",
        },
        AuthorizationRefreshPort: {
            "authorize": "(self, *, binding: 'AgentToolBindingRecord', requesting_subject_id: 'str', room_id: 'str', room_epoch: 'int', resource_refs: 'list[str]') -> \"Literal['authorized', 'denied', 'transient_failure']\"",
        },
        AuthReferenceVerificationPort: {
            "verify": "(self, authorization_reference: 'str', *, authenticated_answerer_id: 'str', call_record_id: 'str', binding_id: 'str', binding_digest: 'str', room_id: 'str', room_epoch: 'int', interaction_id: 'str', interaction_revision: 'int', route_fingerprint: 'str', interaction_fingerprint: 'str', question_id: 'str', challenge_digest: 'str', answer_digest: 'str') -> 'str'",
        },
        AgentToolBindingStore: {
            "insert": "(self, record: 'AgentToolBindingRecord') -> 'StoreOutcome'",
            "load": "(self, binding_id: 'str') -> 'AgentToolBindingRecord | None'",
            "list_for_run": "(self, run_id: 'str') -> 'list[AgentToolBindingRecord]'",
            "delete_by_epoch": "(self, room_id: 'str', room_epoch: 'int') -> 'int'",
        },
        PreparedInvocationSnapshotReader: {
            "read_prepared": "(self, invocation: 'ToolInvocation') -> 'PreparedInvocationSnapshot | None'",
        },
        AgentCallLedgerStore: {
            "insert": "(self, record: 'AgentCallLedgerRecord') -> 'StoreOutcome'",
            "load": "(self, run_id: 'str', invocation_id: 'str') -> 'AgentCallLedgerRecord | None'",
            "load_by_record_id": "(self, call_record_id: 'str') -> 'AgentCallLedgerRecord | None'",
            "find_by_alias": "(self, binding_scope: 'str', *, task_id: 'str | None', context_id: 'str | None') -> 'AgentCallLedgerRecord | None'",
            "cas": "(self, record: 'AgentCallLedgerRecord', *, expected_state_version: 'int') -> 'StoreOutcome'",
            "claim": "(self, call_record_id: 'str', *, expected_state_version: 'int', owner_id: 'str', lease_expires_at: 'datetime', claimed_at: 'datetime') -> 'AgentCallLedgerRecord | None'",
            "renew": "(self, call_record_id: 'str', *, expected_state_version: 'int', owner_id: 'str', lease_expires_at: 'datetime', renewed_at: 'datetime') -> 'AgentCallLedgerRecord | None'",
            "release": "(self, call_record_id: 'str', *, expected_state_version: 'int', owner_id: 'str', next_attempt_at: 'datetime | None', released_at: 'datetime') -> 'AgentCallLedgerRecord | None'",
            "list_due": "(self, *, due_at: 'datetime', limit: 'int') -> 'list[AgentCallLedgerRecord]'",
            "list_for_run": "(self, run_id: 'str') -> 'list[AgentCallLedgerRecord]'",
            "delete_by_epoch": "(self, room_id: 'str', room_epoch: 'int') -> 'int'",
        },
        ObservationInboxStore: {
            "insert": "(self, record: 'A2AObservationInboxRecord') -> 'StoreOutcome'",
            "load": "(self, observation_id: 'str') -> 'A2AObservationInboxRecord | None'",
            "load_by_source_identity": "(self, source_identity: 'str') -> 'A2AObservationInboxRecord | None'",
            "cas": "(self, record: 'A2AObservationInboxRecord', *, expected_state_version: 'int', owner_id: 'str | None' = None, claim_token: 'str | None' = None) -> 'StoreOutcome'",
            "claim": "(self, observation_id: 'str', *, expected_state_version: 'int', owner_id: 'str', claim_token: 'str', lease_expires_at: 'datetime', claimed_at: 'datetime') -> 'A2AObservationInboxRecord | None'",
            "renew": "(self, observation_id: 'str', *, expected_state_version: 'int', owner_id: 'str', claim_token: 'str', lease_expires_at: 'datetime', renewed_at: 'datetime') -> 'A2AObservationInboxRecord | None'",
            "list_due": "(self, *, due_at: 'datetime', limit: 'int') -> 'list[A2AObservationInboxRecord]'",
            "delete_by_binding_scope": "(self, binding_scope: 'str') -> 'int'",
            "delete_by_epoch": "(self, room_id: 'str', room_epoch: 'int') -> 'int'",
        },
        NormalizedObservationRecorder: {
            "record": "(self, observation: 'NormalizedA2AObservation') -> 'tuple[StoreOutcome, A2AObservationInboxRecord]'",
            "mark_executor_outcome": "(self, observation_id: 'str', *, outcome_digest: 'str') -> 'None'",
        },
        ObservationConflictStore: {
            "insert": "(self, record: 'A2AObservationConflictRecord') -> 'StoreOutcome'",
            "list_for_source": "(self, source_identity: 'str') -> 'list[A2AObservationConflictRecord]'",
            "delete_by_epoch": "(self, room_id: 'str', room_epoch: 'int') -> 'int'",
        },
        A2ADispatchPort: {
            "dispatch": "(self, command: 'A2ADispatchCommand') -> 'A2ADispatchReceipt'",
            "inspect": "(self, command: 'A2ADispatchCommand') -> 'A2ADispatchReceipt'",
            "continue_task": "(self, command: 'A2AContinuationCommand') -> 'A2ADispatchReceipt'",
            "inspect_continuation": "(self, command: 'A2AContinuationCommand') -> 'A2ADispatchReceipt'",
            "cancel": "(self, command: 'A2ACancellationCommand') -> 'A2ADispatchReceipt'",
            "inspect_cancellation": "(self, command: 'A2ACancellationCommand') -> 'A2ADispatchReceipt'",
            "is_command_retry_safe": "(self, transport_kind: 'str') -> 'bool'",
        },
        ResourceMaterializerPort: {
            "materialize": "(self, manifest: 'FrozenCallResourceManifest', *, room_id: 'str', room_epoch: 'int', allowed_input_modes: 'list[str]', deadline_at: 'datetime') -> 'list[MaterializedResourcePart]'",
            "materialize_inbound_artifacts": "(self, *, call: 'AgentCallLedgerRecord', artifact_refs: 'list[str]', observation_id: 'str') -> 'list[str]'",
        },
        HITLApplicationPort: {
            "create_or_replay": "(self, *, call: 'AgentCallLedgerRecord', interaction: 'A2AInteractionSpec', interaction_fingerprint: 'str') -> 'str'",
            "activate": "(self, interaction_id: 'str', *, call_record_id: 'str', interaction_fingerprint: 'str') -> 'StoreOutcome'",
            "abandon": "(self, interaction_id: 'str', *, call_record_id: 'str', reason: 'str') -> 'HITLAbandonOutcome'",
            "read_interaction": "(self, interaction_id: 'str') -> 'tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str] | None'",
            "read_answers": "(self, interaction_id: 'str', interaction_revision: 'int') -> 'list[HITLQuestionAnswer] | None'",
            "read_answer_record": "(self, interaction_id: 'str', interaction_revision: 'int') -> 'DurableHITLAnswerRecord | None'",
            "answer": "(self, *, interaction_id: 'str', interaction_revision: 'int', route_fingerprint: 'str', answers: 'list[HITLQuestionAnswer]', authenticated_answerer_id: 'str', verified_auth_reference_digests: 'list[str]', verified_auth_references: 'list[VerifiedAuthReferenceBinding]') -> 'str'",
        },
        ToolObservationSink: {
            "deliver": "(self, run_id: 'str', observation: 'ToolObservation') -> 'None'",
        },
        ObservationIngressAuthenticator: {
            "authenticate": "(self, *, source_kind: 'str', headers: 'dict[str, str]', body: 'bytes') -> 'str'",
        },
        RoomEpochStore: {
            "read_active": "(self, room_id: 'str') -> 'RoomEpoch | None'",
            "activate": "(self, room_id: 'str', creation_id: 'str', *, activated_at: 'datetime') -> 'tuple[StoreOutcome, RoomEpoch | None]'",
            "deactivate": "(self, room_id: 'str', epoch: 'int', deletion_id: 'str', *, deactivated_at: 'datetime') -> 'tuple[StoreOutcome, RoomEpoch | None]'",
            "verify_active": "(self, room_id: 'str', epoch: 'int') -> 'bool'",
            "verify_cleanup_epoch": "(self, room_id: 'str', epoch: 'int', deletion_id: 'str') -> 'bool'",
        },
        InvocationCheckpointReader: {
            "is_acceptance_checkpointed": "(self, run_id: 'str', invocation_id: 'str', acceptance_id: 'str', idempotency_key: 'str', binding_digest: 'str') -> 'bool'",
            "is_suspension_checkpointed": "(self, run_id: 'str', invocation_id: 'str', status: 'str') -> 'bool'",
        },
        InvocationOutcomeCheckpointReader: {
            "is_outcome_checkpointed": "(self, run_id: 'str', invocation_id: 'str', outcome_digest: 'str') -> 'bool'",
            "has_processed_observation": "(self, run_id: 'str', invocation_id: 'str', observation_id: 'str') -> 'bool'",
        },
    }
    for protocol, signatures in expected.items():
        actual = {
            name: str(inspect.signature(value))
            for name, value in protocol.__dict__.items()
            if inspect.isfunction(value)
            and (not name.startswith("_") or name in {"__aiter__", "__anext__"})
        }
        assert actual == signatures, protocol.__name__

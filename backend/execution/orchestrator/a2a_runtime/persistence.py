"""Mongo collection and index metadata for private A2A runtime state."""

from __future__ import annotations

from ..persistence import MongoCollectionDefinition, MongoIndexDefinition

AGENT_TOOL_BINDINGS_COLLECTION = "orchestrator_agent_tool_bindings"
AGENT_CALLS_COLLECTION = "orchestrator_agent_calls"
A2A_OBSERVATIONS_COLLECTION = "orchestrator_a2a_observations"
A2A_OBSERVATION_CONFLICTS_COLLECTION = "orchestrator_a2a_observation_conflicts"
ROOM_EPOCHS_COLLECTION = "orchestrator_room_epochs"
HITL_INTERACTIONS_COLLECTION = "orchestrator_hitl_interactions"

AGENT_TOOL_BINDING_INDEXES = (
    MongoIndexDefinition("binding_id_unique", (("binding_id", 1),), unique=True),
    MongoIndexDefinition(
        "run_tool_unique", (("run_id", 1), ("tool_name", 1)), unique=True
    ),
    MongoIndexDefinition("binding_epoch_cleanup", (("room_id", 1), ("room_epoch", 1))),
)
AGENT_CALL_INDEXES = (
    MongoIndexDefinition(
        "call_record_id_unique", (("call_record_id", 1),), unique=True
    ),
    MongoIndexDefinition(
        "run_invocation_unique", (("run_id", 1), ("invocation_id", 1)), unique=True
    ),
    MongoIndexDefinition("acceptance_id_unique", (("acceptance_id", 1),), unique=True),
    MongoIndexDefinition(
        "call_idempotency_unique", (("idempotency_key", 1),), unique=True
    ),
    MongoIndexDefinition(
        "run_source_unique",
        (("run_id", 1), ("assistant_message_id", 1), ("source_index", 1)),
        unique=True,
    ),
    MongoIndexDefinition(
        "call_recovery_due",
        (("state", 1), ("next_attempt_at", 1), ("claim_expires_at", 1)),
    ),
    MongoIndexDefinition(
        "ownership_alias_unique",
        (("ownership_alias_keys", 1),),
        unique=True,
        partial_filter={"ownership_alias_keys.0": {"$exists": True}},
    ),
    MongoIndexDefinition(
        "pending_interaction_unique",
        (("pending_interaction_id", 1),),
        unique=True,
        partial_filter={"pending_interaction_id": {"$type": "string"}},
    ),
    MongoIndexDefinition("call_epoch_cleanup", (("room_id", 1), ("room_epoch", 1))),
)
OBSERVATION_INDEXES = (
    MongoIndexDefinition(
        "observation_id_unique", (("observation_id", 1),), unique=True
    ),
    MongoIndexDefinition(
        "observation_source_unique", (("source_identity", 1),), unique=True
    ),
    MongoIndexDefinition(
        "observation_recovery_due",
        (("state", 1), ("next_attempt_at", 1), ("claim_expires_at", 1)),
    ),
    MongoIndexDefinition("observation_binding_cleanup", (("binding_scope", 1),)),
    MongoIndexDefinition(
        "observation_epoch_cleanup", (("room_id", 1), ("room_epoch", 1))
    ),
)
OBSERVATION_CONFLICT_INDEXES = (
    MongoIndexDefinition(
        "observation_conflict_id_unique", (("conflict_id", 1),), unique=True
    ),
    MongoIndexDefinition("observation_conflict_source", (("source_identity", 1),)),
    MongoIndexDefinition(
        "observation_conflict_epoch_cleanup", (("room_id", 1), ("room_epoch", 1))
    ),
)
ROOM_EPOCH_INDEXES = (
    MongoIndexDefinition("room_epoch_room_unique", (("room_id", 1),), unique=True),
    MongoIndexDefinition(
        "room_epoch_high_water", (("room_id", 1), ("high_water_mark", -1))
    ),
)
HITL_INTERACTION_INDEXES = (
    MongoIndexDefinition(
        "hitl_interaction_id_unique", (("interaction_id", 1),), unique=True
    ),
)
A2A_RUNTIME_COLLECTIONS = (
    MongoCollectionDefinition(
        AGENT_TOOL_BINDINGS_COLLECTION, AGENT_TOOL_BINDING_INDEXES
    ),
    MongoCollectionDefinition(AGENT_CALLS_COLLECTION, AGENT_CALL_INDEXES),
    MongoCollectionDefinition(A2A_OBSERVATIONS_COLLECTION, OBSERVATION_INDEXES),
    MongoCollectionDefinition(
        A2A_OBSERVATION_CONFLICTS_COLLECTION, OBSERVATION_CONFLICT_INDEXES
    ),
    MongoCollectionDefinition(ROOM_EPOCHS_COLLECTION, ROOM_EPOCH_INDEXES),
    MongoCollectionDefinition(HITL_INTERACTIONS_COLLECTION, HITL_INTERACTION_INDEXES),
)

"""Shared persistence identifiers for the orchestrator transport boundary.

``hub_runtime_bridge`` owns the outbound relay command journal but must not
import ``execution.orchestrator`` (enforced by
``test_hub_runtime_bridge_import_boundaries``). The collection name therefore
lives here, in ``common``, so both the transport store and the orchestrator
index metadata in ``execution/orchestrator/persistence.py`` can reference a
single source of truth.
"""

from __future__ import annotations

RELAY_COMMAND_JOURNAL_COLLECTION = "relay_command_journal"

__all__ = ["RELAY_COMMAND_JOURNAL_COLLECTION"]

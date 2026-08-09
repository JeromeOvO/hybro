from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from context_memory.config import ContextMemoryLLMConfig
from context_memory.summary import update_room_summary

NOW = datetime(2026, 5, 13, tzinfo=UTC)


class SummaryRepository:
    def __init__(self, projection: dict | None):
        self.projection = projection
        self.updates: list[tuple[str, dict, dict]] = []

    async def get_room_summary_projection(self, room_id: str) -> dict | None:
        return self.projection

    async def update_room_summary_atomic(
        self, room_id: str, room_summary: dict, **kwargs
    ) -> bool:
        self.updates.append((room_id, room_summary, kwargs))
        return True


class RecordingLLM:
    def __init__(self, data: dict):
        self.data = data
        self.calls: list[tuple[list[dict], dict, str | None]] = []

    async def generate_structured(self, messages, schema, model=None):
        self.calls.append((messages, schema, model))
        return SimpleNamespace(data=self.data)


def extraction(**overrides) -> dict:
    data = {
        "current_goal": None,
        "key_decisions": [],
        "open_questions": [],
        "recent_agent_contributions": [],
        "important_constraints": [],
        "room_facts": [],
    }
    data.update(overrides)
    return data


async def run_update(repository: SummaryRepository, llm: RecordingLLM) -> bool:
    return await update_room_summary(
        repository=repository,
        llm_provider=llm,
        llm_config=ContextMemoryLLMConfig(),
        room_id="room-1",
        synthesis_text="New synthesis",
        synthesis_turn_id="turn-2",
        id_factory=lambda: "fact-new",
        now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_summary_prompt_includes_existing_projection_and_merge_rules():
    repository = SummaryRepository(
        {
            "room_summary": {
                "current_goal": "Ship release",
                "key_decisions": ["Use pytest"],
                "open_questions": ["When to deploy?"],
                "recent_agent_contributions": ["Builder added tests"],
                "important_constraints": ["No downtime"],
            },
            "room_facts": [{"content": "Deadline is Friday"}],
        }
    )
    llm = RecordingLLM(extraction())

    assert await run_update(repository, llm) is True

    prompt = llm.calls[0][0][1]["content"]
    for existing_value in (
        "Ship release",
        "Use pytest",
        "When to deploy?",
        "Builder added tests",
        "No downtime",
        "Deadline is Friday",
    ):
        assert existing_value in prompt
    assert "new non-empty string replaces" in prompt
    assert "case-insensitive deduplication" in prompt
    assert "a non-empty list replaces" in prompt
    assert "No empty array clears an existing list" in prompt


@pytest.mark.asyncio
async def test_empty_extraction_preserves_all_existing_summary_fields():
    existing_summary = {
        "current_goal": "Original goal",
        "key_decisions": ["Original decision"],
        "open_questions": ["Original question"],
        "recent_agent_contributions": ["Original contribution"],
        "important_constraints": ["Original constraint"],
        "updated_after_turn_id": "turn-1",
    }
    repository = SummaryRepository({"room_summary": existing_summary, "room_facts": []})
    llm = RecordingLLM(extraction(current_goal="  "))

    assert await run_update(repository, llm) is True

    saved = repository.updates[0][1]
    for field in (
        "current_goal",
        "key_decisions",
        "open_questions",
        "recent_agent_contributions",
        "important_constraints",
    ):
        assert saved[field] == existing_summary[field]


@pytest.mark.asyncio
async def test_summary_merges_durable_lists_and_replaces_non_empty_recent_lists():
    repository = SummaryRepository(
        {
            "room_summary": {
                "current_goal": "Original goal",
                "key_decisions": ["Use pytest", "KEEP LOGS"],
                "open_questions": ["Old question"],
                "recent_agent_contributions": ["Old contribution"],
                "important_constraints": ["No downtime"],
            },
            "room_facts": [{"content": "Python 3.12"}],
        }
    )
    llm = RecordingLLM(
        extraction(
            current_goal="New goal",
            key_decisions=["use PYTEST", "Ship Friday", "ship friday"],
            open_questions=["New question"],
            recent_agent_contributions=["Reviewer approved"],
            important_constraints=["NO DOWNTIME", "Keep API stable"],
            room_facts=["python 3.12", "Uses MongoDB", "uses mongodb"],
        )
    )

    assert await run_update(repository, llm) is True

    saved = repository.updates[0][1]
    assert saved["current_goal"] == "New goal"
    assert saved["key_decisions"] == ["Use pytest", "KEEP LOGS", "Ship Friday"]
    assert saved["important_constraints"] == ["No downtime", "Keep API stable"]
    assert saved["open_questions"] == ["New question"]
    assert saved["recent_agent_contributions"] == ["Reviewer approved"]
    assert repository.updates[0][2]["new_facts"] == [
        {
            "fact_id": "fact-new",
            "content": "Uses MongoDB",
            "confidence": 1.0,
            "created_at": NOW,
            "source_turn_id": "turn-2",
        }
    ]


@pytest.mark.asyncio
async def test_missing_projection_does_not_call_llm():
    repository = SummaryRepository(None)
    llm = RecordingLLM(extraction(current_goal="Should not be used"))

    assert await run_update(repository, llm) is False
    assert llm.calls == []
    assert repository.updates == []

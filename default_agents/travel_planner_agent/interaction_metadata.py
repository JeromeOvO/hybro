"""Dependency-free typed interaction metadata for the travel planner."""

import hashlib
import json
import re

HYBRO_A2A_INTERACTION_METADATA_KEY = "hybro.ai/a2a/interaction"


def _question_id(header: str | None, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (header or "details").lower()).strip("-")
    base = base or "details"
    candidate, suffix = base, 2
    while candidate in taken:
        candidate, suffix = f"{base}-{suffix}", suffix + 1
    taken.add(candidate)
    return candidate


def _question_spec(question: dict, question_id: str) -> dict:
    choices = question.get("choices") or []
    spec: dict = {
        "question_id": question_id,
        "interaction_kind": "questionnaire",
    }
    if question.get("header"):
        spec["header"] = question["header"]
    spec["prompt"] = question["question"]
    if len(choices) < 2:
        spec["answer_kind"] = "text"
        spec["required"] = True
        return spec
    spec["answer_kind"] = (
        "multi_choice" if question.get("multi_select") else "single_choice"
    )
    spec["required"] = True
    spec["choices"] = [
        {"value": choice["label"]}
        | ({"description": choice["description"]} if choice.get("description") else {})
        for choice in choices
    ]
    # Suggested choices never limit the user; they can type their own answer.
    spec["allow_other"] = True
    return spec


def build_input_required_metadata(task_id: str, questions: list[dict]) -> dict:
    """Build deterministic typed interaction metadata, one question per item.

    Each item is ``{header, question, choices: [{label, description}],
    multi_select}`` as normalized by the agent.
    """

    identity = hashlib.sha256(
        f"{task_id}\0{json.dumps(questions, sort_keys=True)}".encode()
    ).hexdigest()[:24]
    taken: set[str] = set()
    return {
        HYBRO_A2A_INTERACTION_METADATA_KEY: {
            "schema_version": 1,
            "interaction_id": f"travel-planner:{identity}",
            "questions": [
                _question_spec(question, _question_id(question.get("header"), taken))
                for question in questions
            ],
        }
    }

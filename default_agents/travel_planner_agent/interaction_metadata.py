"""Dependency-free typed interaction metadata for the travel planner."""

import hashlib

HYBRO_A2A_INTERACTION_METADATA_KEY = "hybro.ai/a2a/interaction"


def build_input_required_metadata(task_id: str, prompt: str) -> dict:
    """Build deterministic typed interaction metadata for one clarification."""

    identity = hashlib.sha256(f"{task_id}\0{prompt}".encode()).hexdigest()[:24]
    return {
        HYBRO_A2A_INTERACTION_METADATA_KEY: {
            "schema_version": 1,
            "interaction_id": f"travel-planner:{identity}",
            "questions": [
                {
                    "question_id": f"travel-details:{identity}",
                    "interaction_kind": "questionnaire",
                    "prompt": prompt,
                    "answer_kind": "text",
                    "required": True,
                }
            ],
        }
    }

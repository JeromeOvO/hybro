"""The /agent/* routes return public AgentView DTOs built from AgentInfo."""

from __future__ import annotations

from agent.views import agent_card_view, agent_view
from common.dto import AgentInfo


def _raw_card(**overrides) -> dict:
    values = {
        "name": "Weather",
        "description": "Forecasts",
        "url": "https://private.example/a2a",
        "version": "2.0.0",
        "provider": {"organization": "Acme", "url": "https://acme.example"},
        "iconUrl": "https://cdn.example/icon.png",
        "documentationUrl": "https://docs.example",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extensions": [{"uri": "x"}],
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "image"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "skills": [
            {"id": "s1", "name": "Forecast", "tags": ["weather"], "examples": ["Rain?"]}
        ],
    }
    values.update(overrides)
    return values


def test_agent_view_is_flat_and_never_exposes_transport_or_security_data():
    info = AgentInfo(
        agent_id="a1",
        name="Weather",
        url="https://private.example/a2a",
        provider_id="u1",
        status="inactive",
        call_count=7,
        is_public=False,
        source="local",
        raw_card=_raw_card(),
    )

    view = agent_view(info)
    payload = view.model_dump_json()

    assert view.name == "Weather"
    assert view.status == "inactive"
    assert view.provider_organization == "Acme"
    assert view.capabilities.streaming is True
    assert view.capabilities.extensions is True
    assert view.skills[0].tags == ["weather"]
    assert view.call_count == 7
    assert view.source == "local"
    assert "private.example" not in payload
    assert "bearer" not in payload


def test_card_view_fills_legacy_defaults_for_sparse_cards():
    view = agent_card_view({"name": "Bare"})

    assert view.provider_organization is None
    assert view.skills == []
    assert view.version == "1.0.0"
    assert view.default_input_modes == ["text"]
    assert view.default_output_modes == ["text"]
    assert view.capabilities.streaming is False


def test_agent_view_falls_back_to_info_name_and_ignores_malformed_fields():
    info = AgentInfo(
        agent_id="a1",
        name="Stored name",
        raw_card={"skills": "not-a-list", "capabilities": None, "provider": "x"},
    )

    view = agent_view(info)

    assert view.name == "Stored name"
    assert view.skills == []
    assert view.provider_organization is None


def test_malformed_skills_are_skipped_and_card_view_uses_snapshot_fallbacks():
    view = agent_card_view(
        {"skills": ["oops", {"id": "s1"}, {"id": "s2", "name": "Ok"}]},
        name="Snapshot name",
        description="Snapshot description",
    )

    assert [skill.id for skill in view.skills] == ["s2"]
    assert view.name == "Snapshot name"
    assert view.description == "Snapshot description"

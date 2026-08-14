from pathlib import Path
from types import SimpleNamespace

import pytest

import container
from execution.orchestration.room_message_center import room_message_center
from room.compat.runtime import RoomServices

_REQUIRED_BINDINGS = [
    "runtime_store",
    "facade",
    "cancellation_control",
    "message_parser_service",
    "user_message_commit",
    "timeline_projector",
    "room_deletion",
    "agent_message_preparation",
]


def test_fresh_room_services_reports_all_required_bindings():
    assert RoomServices().missing_required_bindings() == _REQUIRED_BINDINGS


def test_facade_binding_does_not_attest_other_room_dependencies():
    service = RoomServices()

    service.bind_facade(object())

    assert service.missing_required_bindings() == [
        name for name in _REQUIRED_BINDINGS if name != "facade"
    ]


def _bind_required_dependencies(service: RoomServices) -> None:
    service.bind_store(object())
    service.bind_facade(object())
    service.bind_cancellation_control(cancellation_control=object())
    service.bind_message_parser_service(object())
    service.bind_user_message_commit(object())
    service.bind_timeline_projector(object())
    service.bind_room_deletion(object())
    service.bind_agent_message_preparation(object())


def test_complete_core_binding_set_allows_optional_capabilities_to_remain_absent():
    service = RoomServices()
    _bind_required_dependencies(service)

    assert service.missing_required_bindings() == []
    assert service._room_files is None
    assert service._context_assembly is None
    assert service._attachment_metadata_reader is None
    assert service._attachment_content_reader is None
    assert service._capability_issue_reader is None


def test_binding_inventory_tolerates_partially_constructed_test_instance():
    service = object.__new__(RoomServices)

    assert service.missing_required_bindings() == _REQUIRED_BINDINGS


def test_reset_bindings_prevents_previous_lifespan_from_attesting_readiness():
    service = RoomServices()
    _bind_required_dependencies(service)
    assert service.missing_required_bindings() == []

    service.reset_bindings()

    assert service.missing_required_bindings() == _REQUIRED_BINDINGS


def test_container_resets_global_room_runtime_before_first_binding():
    source = (Path(__file__).resolve().parents[1] / "container.py").read_text()

    assert source.index("room_runtime.reset_bindings()") < source.index(
        "room_runtime.bind_message_parser_service"
    )


def test_container_validation_aggregates_room_binding_failures(monkeypatch):
    app = SimpleNamespace(
        state=SimpleNamespace(
            delivery_facade=object(),
            execution_deps=object(),
            api_gateway_deps=object(),
        )
    )
    monkeypatch.setattr(room_message_center, "_runtime", object())
    monkeypatch.setattr(container, "missing_required_deps", lambda _deps: [])
    monkeypatch.setattr(
        RoomServices,
        "missing_required_bindings",
        lambda _self: ["runtime_store", "room_deletion"],
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"Startup binding incomplete - missing: "
            r"room\.runtime_store, room\.room_deletion\. Cannot serve traffic\."
        ),
    ):
        container.validate_runtime_bindings(app)

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints

from common.protocols import (
    RuntimeAgentRoomStore,
    RuntimeHITLStore,
    RuntimeMemoryStore,
    RuntimeMessageStore,
    RuntimeTaskLifecycleStore,
)


def test_runtime_store_protocols_are_exported():
    import common.protocols as protocols

    assert protocols.RuntimeAgentRoomStore is RuntimeAgentRoomStore
    assert protocols.RuntimeMessageStore is RuntimeMessageStore
    assert protocols.RuntimeTaskLifecycleStore is RuntimeTaskLifecycleStore
    assert protocols.RuntimeHITLStore is RuntimeHITLStore
    assert protocols.RuntimeMemoryStore is RuntimeMemoryStore


def test_runtime_repository_store_declares_runtime_protocol_surface():
    from dal.runtime_store import AppShellRepositoryStore

    store = object.__new__(AppShellRepositoryStore)

    assert isinstance(store, RuntimeAgentRoomStore)
    assert isinstance(store, RuntimeMessageStore)
    assert isinstance(store, RuntimeTaskLifecycleStore)
    assert isinstance(store, RuntimeHITLStore)
    assert isinstance(store, RuntimeMemoryStore)


def _signature_text(obj, method_name: str) -> str:
    return str(inspect.signature(getattr(obj, method_name)))


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=path.as_posix())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_runtime_store_protocols_do_not_import_legacy_models():
    imports = _imports_for(Path("common/protocols/runtime_store_protocols.py"))

    assert not {
        module
        for module in imports
        if module == "models" or module.startswith("models.")
    }


def test_runtime_store_protocol_signatures_match_current_store_surface():
    from common.protocols.runtime_store_protocols import (
        RuntimeAgentRoomStore,
        RuntimeHITLStore,
        RuntimeMemoryStore,
        RuntimeMessageStore,
        RuntimeTaskLifecycleStore,
    )
    from dal.runtime_store import AppShellRepositoryStore

    protocol_types = [
        RuntimeAgentRoomStore,
        RuntimeMessageStore,
        RuntimeTaskLifecycleStore,
        RuntimeHITLStore,
        RuntimeMemoryStore,
    ]

    for protocol_type in protocol_types:
        for method_name, method in protocol_type.__dict__.items():
            if method_name.startswith("_") or not inspect.isfunction(method):
                continue
            assert _signature_text(AppShellRepositoryStore, method_name) == (
                _signature_text(protocol_type, method_name)
            )


def test_runtime_store_protocol_type_hints_are_resolvable():
    from common.dto import RuntimeAgentGroup, RuntimeRoomAgentMessage

    agent_group_hints = get_type_hints(RuntimeAgentRoomStore.add_agent_group)
    task_hints = get_type_hints(
        RuntimeTaskLifecycleStore.resolve_client_request_id_for_agent_message
    )

    assert agent_group_hints["agent_group"] is RuntimeAgentGroup
    assert task_hints["room_agent_message"] is RuntimeRoomAgentMessage


def test_protocol_import_tolerates_json_log_format_environment():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from common.protocols import RuntimeAgentRoomStore, "
                "APIKeyAuthenticator; "
                "print(RuntimeAgentRoomStore.__name__, APIKeyAuthenticator.__name__)"
            ),
        ],
        check=False,
        capture_output=True,
        env={"LOG_FORMAT": "json", "LOG_PATH": "/tmp/hybro-protocol-import.log"},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "RuntimeAgentRoomStore APIKeyAuthenticator" in result.stdout


class _FakeMongo:
    def __init__(self) -> None:
        self.collections: dict[str, object] = {}

    def collection(self, name: str) -> object:
        return self.collections.setdefault(name, object())


def _make_runtime_store():
    from dal.runtime_store import AppShellRepositoryStore

    return AppShellRepositoryStore(
        mongo=_FakeMongo(),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )


def test_container_runtime_repository_store_factory_resolves_runtime_export():
    from container import create_runtime_repository_store
    from dal.runtime_store import RuntimeRepositoryStore

    store = create_runtime_repository_store(
        mongo=_FakeMongo(),
        room_deps=SimpleNamespace(
            room_repository=object(),
            message_repository=object(),
        ),
        agent_deps=SimpleNamespace(agent_repository=object()),
    )

    assert isinstance(store, RuntimeRepositoryStore)


def test_runtime_store_wires_agent_room_part():
    from dal.runtime_store.parts.agent_room_store import AppShellAgentRoomStore

    store = _make_runtime_store()

    assert isinstance(store.agent_room, AppShellAgentRoomStore)


def test_runtime_store_wires_message_part():
    from dal.runtime_store.parts.message_store import AppShellMessageStore

    store = _make_runtime_store()

    assert isinstance(store.messages, AppShellMessageStore)


def test_runtime_store_wires_task_lifecycle_part():
    from dal.runtime_store.parts.task_lifecycle_store import (
        AppShellTaskLifecycleStore,
    )

    store = _make_runtime_store()

    assert isinstance(store.tasks, AppShellTaskLifecycleStore)


def test_runtime_store_wires_hitl_part():
    from dal.runtime_store.parts.hitl_store import AppShellHITLStore

    store = _make_runtime_store()

    assert isinstance(store.hitl, AppShellHITLStore)


def test_runtime_store_wires_memory_part():
    from dal.runtime_store.parts.memory_store import AppShellMemoryStore

    store = _make_runtime_store()

    assert isinstance(store.memory, AppShellMemoryStore)


def test_runtime_store_wires_all_focused_parts():
    from dal.runtime_store.parts import (
        AppShellAgentRoomStore,
        AppShellHITLStore,
        AppShellMemoryStore,
        AppShellMessageStore,
        AppShellTaskLifecycleStore,
    )

    store = _make_runtime_store()

    assert isinstance(store.agent_room, AppShellAgentRoomStore)
    assert isinstance(store.messages, AppShellMessageStore)
    assert isinstance(store.tasks, AppShellTaskLifecycleStore)
    assert isinstance(store.hitl, AppShellHITLStore)
    assert isinstance(store.memory, AppShellMemoryStore)


def test_legacy_repository_part_shims_export_dal_owner_objects():
    from app_shell.repository_parts import (
        AppShellAgentRoomStore as LegacyAgentRoomStore,
    )
    from app_shell.repository_parts import AppShellHITLStore as LegacyHITLStore
    from app_shell.repository_parts import AppShellMemoryStore as LegacyMemoryStore
    from app_shell.repository_parts import AppShellMessageStore as LegacyMessageStore
    from app_shell.repository_parts import (
        AppShellTaskLifecycleStore as LegacyTaskLifecycleStore,
    )
    from app_shell.repository_parts.hitl_store import (
        AppShellHITLStore as LegacyHITLModuleStore,
    )
    from app_shell.repository_parts.memory_store import (
        AppShellMemoryStore as LegacyMemoryModuleStore,
    )
    from app_shell.repository_parts.message_store import (
        AppShellMessageStore as LegacyMessageModuleStore,
    )
    from app_shell.repository_parts.task_lifecycle_store import (
        AppShellTaskLifecycleStore as LegacyTaskLifecycleModuleStore,
    )
    from dal.runtime_store.parts import (
        AppShellAgentRoomStore,
        AppShellHITLStore,
        AppShellMemoryStore,
        AppShellMessageStore,
        AppShellTaskLifecycleStore,
    )

    legacy_agent_room_module = importlib.import_module(
        "app_shell.repository_parts." + "agent_room_store"
    )
    LegacyAgentRoomModuleStore = legacy_agent_room_module.AppShellAgentRoomStore

    assert LegacyAgentRoomStore is AppShellAgentRoomStore
    assert LegacyAgentRoomModuleStore is AppShellAgentRoomStore
    assert LegacyHITLStore is AppShellHITLStore
    assert LegacyHITLModuleStore is AppShellHITLStore
    assert LegacyMemoryStore is AppShellMemoryStore
    assert LegacyMemoryModuleStore is AppShellMemoryStore
    assert LegacyMessageStore is AppShellMessageStore
    assert LegacyMessageModuleStore is AppShellMessageStore
    assert LegacyTaskLifecycleStore is AppShellTaskLifecycleStore
    assert LegacyTaskLifecycleModuleStore is AppShellTaskLifecycleStore


def test_legacy_repository_part_parsing_shim_exports_dal_owner_helpers():
    import app_shell.repository_parts.parsing as legacy_parsing
    import dal.runtime_store.parts.parsing as owner_parsing

    helper_names = [
        "_extract_text_from_artifact_parts",
        "_modified_count",
        "_mongo_update_succeeded",
        "_safe_parse_agent",
        "_safe_parse_agent_group",
        "_safe_parse_agent_message",
        "_safe_parse_chat_context",
        "_safe_parse_room",
        "_safe_parse_room_memory",
        "_safe_parse_user_message",
        "_strip_file_urls",
        "_strip_unset_task_tracking_fields",
        "_task_tracking_matches",
    ]

    assert legacy_parsing.__all__ == helper_names
    for helper_name in helper_names:
        assert getattr(legacy_parsing, helper_name) is getattr(
            owner_parsing,
            helper_name,
        )


def test_legacy_repository_part_webhook_shim_exports_dal_owner_helpers():
    import app_shell.repository_parts.webhook_tokens as legacy_webhook_tokens
    import dal.runtime_store.parts.webhook_tokens as owner_webhook_tokens

    helper_names = [
        "generate_webhook_token",
        "get_webhook_signing_key",
        "hash_webhook_token",
        "verify_webhook_token",
    ]

    assert legacy_webhook_tokens.__all__ == helper_names
    for helper_name in helper_names:
        assert getattr(legacy_webhook_tokens, helper_name) is getattr(
            owner_webhook_tokens,
            helper_name,
        )


def test_runtime_store_part_properties_do_not_recreate_missing_parts():
    store = _make_runtime_store()

    del store._agent_room_part
    del store._message_part
    del store._task_lifecycle_part
    del store._hitl_part
    del store._memory_part

    for attribute in ("agent_room", "messages", "tasks", "hitl", "memory"):
        try:
            getattr(store, attribute)
        except AttributeError:
            continue
        raise AssertionError(f"{attribute} should expose missing store wiring")


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _references_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def test_container_binds_focused_runtime_store_parts_before_aggregate_shims():
    container_source = Path("container.py").read_text()
    tree = ast.parse(container_source)
    expected_assignments = {
        "agent_room_store": "agent_room",
        "message_store": "messages",
        "task_store": "tasks",
        "hitl_store": "hitl",
        "memory_store": "memory",
    }

    assignments: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "runtime_store"
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value.attr

    assert expected_assignments.items() <= assignments.items()
    assert "create_runtime_repository_store" in container_source
    assert "create_app_" + "shell_repository_store" not in container_source


def test_main_keeps_broad_repository_store_only_for_documented_compatibility_points():
    tree = ast.parse(Path("main.py").read_text())
    allowed_broad_calls: set[str] = set()
    broad_calls: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _dotted_name(node.func) or "<unknown>"
        call_refs_broad_store = any(
            _references_name(arg, "app_shell_store") for arg in node.args
        ) or any(
            keyword.value is not None
            and _references_name(keyword.value, "app_shell_store")
            for keyword in node.keywords
        )
        if call_refs_broad_store and call_name not in allowed_broad_calls:
            broad_calls.append(f"{call_name}:{node.lineno}")

    assert broad_calls == []


def _simple_namespace_keywords(tree: ast.AST, assignment_name: str) -> set[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == assignment_name
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if _dotted_name(node.value.func) != "SimpleNamespace":
            continue
        return {keyword.arg for keyword in node.value.keywords if keyword.arg}
    return set()


def test_container_binds_debate_and_coordinator_to_focused_message_adapters():
    tree = ast.parse(Path("container.py").read_text())
    bound_adapters: dict[str, str] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _dotted_name(node.func)
        if call_name not in {
            "debate_prompt_injector.bind_store",
            "synthesis_coordinator.bind_store",
        }:
            continue
        assert len(node.args) == 1
        assert isinstance(node.args[0], ast.Name)
        bound_adapters[call_name] = node.args[0].id

    assert bound_adapters == {
        "debate_prompt_injector.bind_store": "debate_message_store",
        "synthesis_coordinator.bind_store": "room_coordinator_message_store",
    }
    assert _simple_namespace_keywords(tree, "debate_message_store") == {
        "get_agent_name_by_agent_id",
        "get_room_agent_message_by_message_id",
        "update_room_agent_message_with_new_message_content_by_message_id",
    }
    assert _simple_namespace_keywords(tree, "room_coordinator_message_store") == {
        "add_room_agent_message",
        "get_agent_name_by_agent_id",
        "get_room_agent_messages_by_related_message_id",
        "get_room_by_room_id",
        "get_room_user_message_by_message_id",
    }


def test_container_binds_room_runtime_to_focused_room_store_adapter():
    tree = ast.parse(Path("container.py").read_text())
    bound_store_name = None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted_name(node.func) != "room_runtime.bind_store":
            continue
        assert len(node.args) == 1
        assert isinstance(node.args[0], ast.Name)
        bound_store_name = node.args[0].id

    assert bound_store_name == "room_runtime_store"
    assert _simple_namespace_keywords(tree, "room_runtime_store") == {
        "add_room_agent_message",
        "get_agent_by_agent_id",
        "get_agent_group_by_id",
        "get_agents_with_conditions",
        "get_all_active_agents",
        "get_room_by_room_id",
        "get_room_memory_by_room_id",
        "get_room_user_message_by_message_id",
        "update_room_by_room_id",
        "update_room_user_message_by_message_id",
    }


def test_container_binds_relay_to_focused_runtime_store_adapter():
    tree = ast.parse(Path("container.py").read_text())
    bound_store_name = None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted_name(node.func) != "init_relay_service":
            continue
        for keyword in node.keywords:
            if keyword.arg == "db":
                assert isinstance(keyword.value, ast.Name)
                bound_store_name = keyword.value.id

    assert bound_store_name == "relay_runtime_store"
    assert _simple_namespace_keywords(tree, "relay_runtime_store") == {
        "get_agent_by_agent_id",
        "get_room_agent_message_by_message_id",
        "get_room_by_room_id",
        "get_room_user_message_by_message_id",
        "increment_agent_call_count",
        "is_message_cancelled",
    }

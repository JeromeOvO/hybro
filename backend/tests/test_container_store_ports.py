"""Container binds module-owned stores through focused ports."""

import ast
import re
import subprocess
import sys
from pathlib import Path


def test_protocol_import_tolerates_json_log_format_without_creating_log_file(
    tmp_path,
):
    legacy_log_path = tmp_path / "legacy-app.log"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("from common.protocols import MongoDAL; print(MongoDAL.__name__)"),
        ],
        check=False,
        capture_output=True,
        env={"LOG_FORMAT": "json", "LOG_PATH": str(legacy_log_path)},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "MongoDAL" in result.stdout
    assert not legacy_log_path.exists()


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _references_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


def test_container_builds_module_owned_stores_directly():
    source = Path("container.py").read_text()

    for constructor in (
        "AgentRuntimeStore(",
        "ConversationMessageStore(",
        "ConversationRoomStore(",
        "ClientRequestIdStore(",
        "HITLRequestStore(",
        "HITLLifecycleStore(",
    ):
        assert constructor in source, constructor
    assert not re.search(r"\bruntime_store\.", source)
    assert "create_app_" + "shell_repository_store" not in source


def test_main_keeps_broad_repository_store_only_for_documented_compatibility_points():
    tree = ast.parse(Path("main.py").read_text())
    allowed_broad_calls: set[str] = set()
    broad_calls: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _dotted_name(node.func) or "<unknown>"
        call_refs_broad_store = any(
            _references_name(arg, "repository_store") for arg in node.args
        ) or any(
            keyword.value is not None
            and _references_name(keyword.value, "repository_store")
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


def test_container_binds_hitl_runtime_store_lifecycle_and_client_request_hooks():
    tree = ast.parse(Path("container.py").read_text())
    bound = _simple_namespace_keywords(tree, "hitl_runtime_store")

    assert {
        "find_pending_hitl_request_for_agent_message",
        "create_or_reuse_pending_hitl_request",
        "persist_pending_hitl_on_agent_message",
        "persist_hitl_request_id_on_message",
        "get_room_user_message_by_message_id",
        "resolve_client_request_id_for_message_id",
    } <= bound


def test_container_binds_room_runtime_to_focused_room_store_adapter():
    tree = ast.parse(Path("container.py").read_text())
    bound_store_name = None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted_name(node.func) != "conversation_runtime.bind_store":
            continue
        assert len(node.args) == 1
        assert isinstance(node.args[0], ast.Name)
        bound_store_name = node.args[0].id

    assert bound_store_name == "room_runtime_store"
    assert _simple_namespace_keywords(tree, "room_runtime_store") == {
        "add_room_agent_message",
        "get_agent_by_agent_id",
        "get_agent_group_by_id",
        "get_all_active_agents",
        "get_room_by_room_id",
        "get_room_user_message_by_message_id",
        "update_room_user_message_by_message_id",
    }

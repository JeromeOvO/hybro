import ast
import json
from pathlib import Path

APP_SHELL_TARGETS = {
    "app_shell/room_runtime.py",
    "app_shell/a2a_runtime.py",
    "app_shell/relay_service.py",
    "app_shell/context_assembly_service.py",
    "app_shell/repository_store.py",
}

FORBIDDEN_APP_SHELL_IMPORT_PREFIXES = (
    "a2a",
    "aioboto3",
    "botocore",
    "common.config.settings",
    "database.mongodb",
)

EXPECTED_APP_SHELL_BASELINE = {
    "app_shell/room_runtime.py": {"lines": 3819, "public_business_methods": 52},
    "app_shell/a2a_runtime.py": {"lines": 613, "public_business_methods": 16},
    "app_shell/relay_service.py": {"lines": 403, "public_business_methods": 27},
    "app_shell/context_assembly_service.py": {
        "lines": 164,
        "public_business_methods": 4,
    },
    "app_shell/repository_store.py": {"lines": 759, "public_business_methods": 93},
}

EXPECTED_APP_SHELL_PUBLIC_METHODS = {
    "app_shell/room_runtime.py": [
        "RoomServices.bind_object_storage",
        "RoomServices.bind_store",
        "RoomServices.bind_facade",
        "RoomServices.bind_context_memory",
        "RoomServices.bind_message_parser_service",
        "RoomServices.bind_debate_rounds",
        "RoomServices.bind_active_run_reader",
        "RoomServices.bind_hitl_pending_checker",
        "RoomServices.bind_execution_event_deps",
        "RoomServices.bind_attachment_metadata_reader",
        "RoomServices.bind_attachment_cleanup",
        "RoomServices.bind_quote_writer",
        "RoomServices.create_new_room",
        "RoomServices.inquiry_room_setting",
        "RoomServices.inquiry_active_runs",
        "RoomServices.inquiry_rooms_by_room_owner_id",
        "RoomServices.update_room_agent_set",
        "RoomServices.update_room_name",
        "RoomServices.update_room_extend_info",
        "RoomServices.delete_room_by_room_id",
        "RoomServices.parse_agent_mentions",
        "RoomServices.extract_agent_message_content",
        "RoomServices.group_mentions_by_context",
        "RoomServices.create_shared_message_content",
        "RoomServices.create_task_for_agent",
        "RoomServices.create_task_for_agents_group",
        "RoomServices.create_agent_message",
        "RoomServices.parse_user_message",
        "RoomServices.send_message_to_room",
        "RoomServices.parse_user_message_with_mentions",
        "RoomServices.process_agent_message",
        "RoomServices.update_agent_message_by_message_id",
        "RoomServices.inquiry_user_messages_by_room_id",
        "RoomServices.inquiry_agent_messages_by_room_id",
        "RoomServices.inquiry_agent_message_by_message_id",
        "RoomServices.inquiry_user_message_by_message_id",
        "RoomServices.inquiry_agent_messages_by_related_message_id",
        "RoomServices.inquiry_room_messages_by_room_id",
        "RoomServices.handle_a2a_response_for_room",
        "AppShellRoomCenter.bind_facade",
        "AppShellRoomCenter.bind_room_services",
        "AppShellRoomCenter.create_new_room",
        "AppShellRoomCenter.inquiry_room_setting",
        "AppShellRoomCenter.inquiry_active_runs",
        "AppShellRoomCenter.delete_room_by_room_id",
        "AppShellRoomCenter.inquiry_rooms_by_room_owner_id",
        "AppShellRoomCenter.update_room_agent_set",
        "AppShellRoomCenter.update_room_name",
        "AppShellRoomCenter.update_room_extend_info",
        "AppShellRoomCenter.inquiry_room_messages_by_room_id",
        "AppShellRoomCenter.inquiry_agent_messages_by_related_message_id",
        "AppShellRoomCenter.send_message_to_room",
    ],
    "app_shell/a2a_runtime.py": [
        "A2AService.bind_runtime_config",
        "A2AService.bind_task_db",
        "A2AService.get_agent_card_from_url",
        "A2AService.has_streaming_capability",
        "A2AService.has_push_notification_capability",
        "A2AService.create_task_for_tracking",
        "A2AService.send_message_to_tracked_agent",
        "A2AService.send_message_sync",
        "A2AService.send_message_streaming",
        "A2AService.send_message",
        "A2AService.dry_send_message",
        "A2AService.validate_a2a_response",
        "A2AService.validate_message",
        "A2AService.process_a2a_response",
        "A2AService.cancel_remote_task",
        "A2AService.reply_to_task",
    ],
    "app_shell/relay_service.py": [
        "RelayService.set_relay_transport",
        "RelayService.bind_response_handler",
        "RelayService.set_stream_service",
        "RelayService.set_leader_election",
        "RelayService.bind_agent_registry_writer",
        "RelayService.start",
        "RelayService.stop",
        "RelayService.register_hub",
        "RelayService.get_hub_owner_id",
        "RelayService.connect_hub",
        "RelayService.record_hub_heartbeat",
        "RelayService.is_hub_alive",
        "RelayService.is_hub_alive_cached",
        "RelayService.mark_hub_agents_offline",
        "RelayService.sync_agents",
        "RelayService.push_to_hub",
        "RelayService.process_publish",
        "RelayService.cancel_relay_task",
        "RelayService.cancel_hub_task",
        "RelayService.reply_to_relay_task",
        "RelayService.reply_to_hub_task",
        "RelayService.send_to_hub",
        "RelayService.get_hub_status",
        "RelayService.sweep_offline_queues",
        "RelayHubLivenessReader.is_hub_online",
        "RelayHubLivenessReader.get_hub_owner_id",
        "init_relay_service",
    ],
    "app_shell/context_assembly_service.py": [
        "ContextAssemblyService.bind_facade",
        "ContextAssemblyService.build_supervisor_context",
        "ContextAssemblyService.build_agent_execution_context",
        "ContextAssemblyService.get_budget_summary",
    ],
    "app_shell/repository_store.py": [
        "AppShellRepositoryStore.add_agent_group",
        "AppShellRepositoryStore.get_agent_groups_by_owner",
        "AppShellRepositoryStore.get_agent_group_by_id",
        "AppShellRepositoryStore.update_agent_group",
        "AppShellRepositoryStore.delete_agent_group",
        "AppShellRepositoryStore.get_all_active_agents",
        "AppShellRepositoryStore.get_agent_name_by_agent_id",
        "AppShellRepositoryStore.get_agent_by_agent_id",
        "AppShellRepositoryStore.get_agents_with_conditions",
        "AppShellRepositoryStore.increment_agent_call_count",
        "AppShellRepositoryStore.get_room_by_room_id",
        "AppShellRepositoryStore.get_rooms_by_room_owner_id",
        "AppShellRepositoryStore.update_room_by_room_id",
        "AppShellRepositoryStore.get_room_user_message_by_message_id",
        "AppShellRepositoryStore.get_room_user_messages_by_room_id",
        "AppShellRepositoryStore.get_room_agent_message_by_message_id",
        "AppShellRepositoryStore.get_room_agent_messages_by_room_id",
        "AppShellRepositoryStore.get_room_agent_messages_by_related_message_id",
        "AppShellRepositoryStore.add_room_agent_message",
        "AppShellRepositoryStore.add_room_user_message",
        "AppShellRepositoryStore.update_room_user_message_by_message_id",
        "AppShellRepositoryStore.upsert_room_agent_message",
        "AppShellRepositoryStore.delete_room_agent_message_by_message_id",
        "AppShellRepositoryStore.update_room_agent_message_by_message_id",
        "AppShellRepositoryStore.get_active_runs_by_room_id",
        "AppShellRepositoryStore.save_continuation_on_message",
        "AppShellRepositoryStore.resolve_client_request_id_for_agent_message",
        "AppShellRepositoryStore.resolve_client_request_id_for_message_id",
        "AppShellRepositoryStore.get_task_messages_for_room",
        "AppShellRepositoryStore.get_pending_task_messages_for_user",
        "AppShellRepositoryStore.hash_webhook_token",
        "AppShellRepositoryStore.verify_webhook_token",
        "AppShellRepositoryStore.generate_webhook_token",
        "AppShellRepositoryStore.check_task_limits",
        "AppShellRepositoryStore.enable_task_tracking_on_message",
        "AppShellRepositoryStore.update_task_on_message",
        "AppShellRepositoryStore.update_webhook_token_hash_on_message",
        "AppShellRepositoryStore.verify_webhook_token_on_message",
        "AppShellRepositoryStore.verify_webhook_token_for_task",
        "AppShellRepositoryStore.is_message_cancelled",
        "AppShellRepositoryStore.cancel_message",
        "AppShellRepositoryStore.get_room_ids_with_non_terminal_runs",
        "AppShellRepositoryStore.find_stale_non_terminal_runs",
        "AppShellRepositoryStore.get_stale_task_messages",
        "AppShellRepositoryStore.get_expired_task_messages",
        "AppShellRepositoryStore.get_non_tracked_stale_task_messages",
        "AppShellRepositoryStore.get_orphaned_agent_messages",
        "AppShellRepositoryStore.touch_task_message",
        "AppShellRepositoryStore.get_and_clear_continuation_on_message",
        "AppShellRepositoryStore.get_pending_continuation_on_message",
        "AppShellRepositoryStore.get_and_clear_continuation_on_user_message",
        "AppShellRepositoryStore.save_continuation_on_user_message",
        "AppShellRepositoryStore.get_stuck_supervisor_trajectory_messages",
        "AppShellRepositoryStore.claim_stuck_supervisor_trajectory",
        "AppShellRepositoryStore.get_room_memory_by_room_id",
        "AppShellRepositoryStore.get_pending_hitl_requests_for_message",
        "AppShellRepositoryStore.create_hitl_request",
        "AppShellRepositoryStore.get_hitl_request",
        "AppShellRepositoryStore.update_hitl_request",
        "AppShellRepositoryStore.cas_update_hitl_request",
        "AppShellRepositoryStore.fenced_update_hitl_request",
        "AppShellRepositoryStore.claim_hitl_request",
        "AppShellRepositoryStore.get_pending_hitl_requests",
        "AppShellRepositoryStore.get_hitl_group_requests",
        "AppShellRepositoryStore.count_pending_in_hitl_group",
        "AppShellRepositoryStore.claim_hitl_group_routing",
        "AppShellRepositoryStore.release_hitl_group_routing",
        "AppShellRepositoryStore.count_hitl_requests_for_message",
        "AppShellRepositoryStore.update_agent_message_task_state",
        "AppShellRepositoryStore.persist_hitl_user_answer",
        "AppShellRepositoryStore.persist_hitl_group_metadata",
        "AppShellRepositoryStore.iter_stale_processing_hitl_requests",
        "AppShellRepositoryStore.ensure_hitl_indexes",
        "AppShellRepositoryStore.add_chat_context",
        "AppShellRepositoryStore.get_chat_context_by_session_id",
        "AppShellRepositoryStore.update_chat_context_by_session_id",
        "AppShellRepositoryStore.delete_chat_context_by_session_id",
        "AppShellRepositoryStore.increment_user_interactions",
        "AppShellRepositoryStore.record_agent_call",
        "AppShellRepositoryStore.update_turn_notes",
        "AppShellRepositoryStore.claim_user_message_for_processing",
        "AppShellRepositoryStore.unclaim_user_message",
        "AppShellRepositoryStore.claim_or_reclaim_user_message",
        "AppShellRepositoryStore.refresh_processing_claim",
        "AppShellRepositoryStore.turn_exists",
        "AppShellRepositoryStore.cancel_descendants",
        "AppShellRepositoryStore.cancel_agent_messages_by_ids",
        "AppShellRepositoryStore.update_room_agent_message_with_new_message_content_by_message_id",
        "AppShellRepositoryStore.update_last_notified_state",
        "AppShellRepositoryStore.reset_last_notified_state",
        "AppShellRepositoryStore.update_task_state_on_message",
        "AppShellRepositoryStore.accumulate_artifact_on_message",
        "AppShellRepositoryStore.update_task_state_on_message_if_not_terminal",
    ],
}


def _manifest() -> dict:
    return json.loads(Path("tests/fixtures/phase9_cleanup_manifest.json").read_text())


def _forbidden_prefix(module: str) -> str | None:
    for prefix in FORBIDDEN_APP_SHELL_IMPORT_PREFIXES:
        if module == prefix or module.startswith(f"{prefix}."):
            return prefix
    return None


def _legacy_import_blockers() -> set[tuple[str, str]]:
    blockers: set[tuple[str, str]] = set()
    for entry in _manifest().get("blocked_cleanup", []):
        if entry.get("contract") != "legacy_import_boundary":
            continue
        path = entry.get("path")
        prefix = entry.get("forbidden_prefix")
        if isinstance(path, str) and isinstance(prefix, str):
            blockers.add((path, prefix))
    return blockers


def _import_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append((node.lineno, node.module))
    return modules


def _is_property_like(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "property":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in {
            "setter",
            "deleter",
        }:
            return True
    return False


def _public_business_method_count(path: Path) -> int:
    return len(_public_business_methods(path))


def _public_business_methods(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    methods: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                methods.append(node.name)
            continue
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_") or _is_property_like(item):
                continue
            methods.append(f"{node.name}.{item.name}")
    return methods


def test_forbidden_prefix_matching_is_segment_aware():
    assert _forbidden_prefix("a2a") == "a2a"
    assert _forbidden_prefix("a2a.client") == "a2a"
    assert _forbidden_prefix("a2a_adapter.client_facade") is None


def test_app_shell_forbidden_imports_are_manifest_blocked_by_exact_prefix():
    blockers = _legacy_import_blockers()
    violations: list[str] = []

    for target in sorted(APP_SHELL_TARGETS):
        path = Path(target)
        for lineno, module in _import_modules(path):
            prefix = _forbidden_prefix(module)
            if prefix is None:
                continue
            if (target, prefix) in blockers:
                continue
            violations.append(f"{target}:{lineno}: {module}")

    assert not violations, "Forbidden app-shell imports remain:\n" + "\n".join(
        violations
    )


def test_legacy_import_boundary_blockers_are_exact_current_files():
    blockers = _legacy_import_blockers()
    bad: list[str] = []

    for target, prefix in sorted(blockers):
        if target not in APP_SHELL_TARGETS:
            continue
        path = Path(target)
        if not any(
            module == prefix or module.startswith(f"{prefix}.")
            for _, module in _import_modules(path)
        ):
            bad.append(f"{target}: missing live import for {prefix}")

    assert not bad, "App-shell thinning blockers are stale:\n" + "\n".join(bad)


def test_app_shell_focus_file_baseline_sizes_are_recorded():
    actual = {
        target: {
            "lines": sum(1 for _ in Path(target).open()),
            "public_business_methods": _public_business_method_count(Path(target)),
        }
        for target in sorted(APP_SHELL_TARGETS)
    }

    assert actual == EXPECTED_APP_SHELL_BASELINE


def test_app_shell_public_surface_is_explicitly_allowlisted():
    actual = {
        target: _public_business_methods(Path(target))
        for target in sorted(APP_SHELL_TARGETS)
    }

    assert actual == EXPECTED_APP_SHELL_PUBLIC_METHODS


def test_context_memory_runtime_wiring_avoids_app_shell_singletons():
    forbidden = {
        "app_shell.context_assembly_service",
        "app_shell.memory_search_service",
    }
    targets = [
        Path("app_shell/room_runtime.py"),
        Path("execution/orchestration/room_message_center.py"),
        Path("execution/orchestration/factory.py"),
        Path("main.py"),
    ]
    violations: list[str] = []

    for path in targets:
        for lineno, module in _import_modules(path):
            if module in forbidden:
                violations.append(f"{path}:{lineno}: {module}")

    assert not violations, "App-shell context singleton imports remain:\n" + "\n".join(
        violations
    )

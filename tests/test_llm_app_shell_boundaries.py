import ast
import sys
from pathlib import Path

LLM_COMPATIBILITY_SERVICES = [
    Path("app_shell/openai_service.py"),
    Path("app_shell/gemini_service.py"),
    Path("app_shell/bedrock_service.py"),
]

FOCUSED_LLM_CONSUMERS = [
    Path("agent/resolver.py"),
    Path("app_shell/memory_search_service.py"),
    Path("app_shell/memory_service.py"),
    Path("app_shell/room_coordinator_service.py"),
    Path("room/compat/runtime.py"),
    Path("execution/orchestration/room_supervisor_service.py"),
]

FORBIDDEN_IMPORTS = {
    "openai",
    "google.genai",
    "aioboto3",
    "botocore",
    "dotenv",
}

LLM_SETTINGS_FIELDS = {
    "lead_ai_model",
    "classifier_ai_model",
    "embedding_model",
    "gemini_model_name",
    "gemini_embedding_model_name",
    "supervisor_model",
    "use_bedrock_supervisor",
    "bedrock_supervisor_model",
    "openai_api_key",
    "google_api_key",
    "bedrock_region",
    "debate_rounds",
}


def test_app_shell_llm_compatibility_services_do_not_import_provider_sdks():
    for path in LLM_COMPATIBILITY_SERVICES:
        tree = ast.parse(path.read_text(), filename=str(path))
        forbidden = _forbidden_imports(tree)
        assert not forbidden, f"{path} imports forbidden LLM modules: {forbidden}"


def test_app_shell_llm_compatibility_services_do_not_read_llm_env_vars():
    for path in LLM_COMPATIBILITY_SERVICES:
        tree = ast.parse(path.read_text(), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_os_getenv_call(node.func)
        ]
        assert not calls, f"{path} calls os.getenv for LLM configuration"


def test_bedrock_compatibility_adapter_does_not_own_provider_transport():
    source = Path("app_shell/bedrock_service.py").read_text()
    forbidden = [
        "aioboto3",
        "_legacy_setting",
        "_invoke_model",
        "bedrock-runtime",
        "invoke_model(",
        "invoke_model_with_response_stream",
        'globals()["settings"]',
    ]
    present = [snippet for snippet in forbidden if snippet in source]
    assert not present, (
        "app_shell/bedrock_service.py must delegate to llm_gateway, not own "
        f"Bedrock transport/config: {present}"
    )


def test_llm_gateway_services_import_boundary():
    services_path = Path("llm_gateway/services")
    assert services_path.exists(), "llm_gateway/services package must exist"
    allowed_roots = set(sys.stdlib_module_names) | {
        "__future__",
        "common",
        "llm_gateway",
    }
    forbidden_roots = {
        "models",
        "app_shell",
        "execution",
        "room",
        "agent",
        "platform_module",
        "api",
        "api_gateway",
    }
    forbidden_provider_names = {
        "OpenAIProvider",
        "GeminiProvider",
        "BedrockProvider",
    }
    for path in services_path.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported_roots = _imported_roots(node)
            assert imported_roots.isdisjoint(forbidden_roots), (
                f"{path} imports forbidden domain root "
                f"{imported_roots & forbidden_roots}"
            )
            assert "llm_gateway.providers" not in _imported_modules(node), (
                f"{path} must depend on gateway protocols, not raw providers"
            )
            unexpected = imported_roots - allowed_roots
            assert not unexpected, f"{path} imports unexpected root {unexpected}"
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_provider_names, (
                    f"{path} references raw provider type {node.id}"
                )


def test_provider_hint_is_not_public_protocol_or_service_api():
    paths = [Path("common/protocols/llm_protocols.py")]
    paths.extend(Path("llm_gateway/services").rglob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                arg_names = [arg.arg for arg in node.args.args + node.args.kwonlyargs]
                assert "provider_hint" not in arg_names, (
                    f"{path}:{node.name} exposes provider_hint publicly"
                )


def test_container_binds_focused_llm_services_to_production_consumers():
    source = Path("container.py").read_text()
    main_source = Path("main.py").read_text()
    expected_snippets = [
        "agent_selection_service=agent_selection_llm_service,",
        "room_runtime.bind_message_parser_service(",
        "room_runtime.bind_debate_rounds(runtime.settings.debate_rounds)",
        "context_memory_facade = create_context_memory_facade(",
        "llm_provider=llm_provider,",
        "room_coordinator_service.bind_summary_service(",
        "chat_memory_service.bind_room_memory_llm_service(",
        "room_memory_service.bind_turn_notes_llm_provider(llm_provider)",
        "openai_service.bind_debate_service(",
    ]
    missing = [snippet for snippet in expected_snippets if snippet not in source]
    leaked = [snippet for snippet in expected_snippets if snippet in main_source]
    assert not missing, f"container.py missing focused LLM bindings: {missing}"
    assert not leaked, f"main.py still owns focused LLM bindings: {leaked}"


def test_focused_llm_consumers_do_not_import_openai_compatibility_adapter():
    violations: list[str] = []
    for path in FOCUSED_LLM_CONSUMERS:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if "app_shell.openai_service" in _imported_modules(node):
                violations.append(str(path))
    assert not violations


def test_focused_llm_binding_targets_expose_startup_methods():
    from app_shell.memory_search_service import memory_search_service
    from app_shell.memory_service import chat_memory_service, room_memory_service
    from app_shell.openai_service import openai_service
    from app_shell.room_coordinator_service import room_coordinator_service
    from app_shell.room_runtime import room_runtime

    bindings = [
        (room_runtime, "bind_message_parser_service"),
        (room_runtime, "bind_debate_rounds"),
        (memory_search_service, "bind_embedding_service"),
        (room_coordinator_service, "bind_summary_service"),
        (chat_memory_service, "bind_room_memory_llm_service"),
        (room_memory_service, "bind_turn_notes_llm_provider"),
        (openai_service, "bind_debate_service"),
    ]
    missing = [
        f"{target.__class__.__name__}.{method}"
        for target, method in bindings
        if not callable(getattr(target, method, None))
    ]
    assert not missing


def test_llm_settings_are_not_read_by_feature_or_app_shell_modules():
    allowed_prefixes = (
        "common/config/",
        "llm_gateway/config.py",
        "llm_gateway/model_registry.py",
        "llm_gateway/providers/",
    )
    scan_roots = [
        Path("app_shell"),
        Path("execution"),
        Path("api"),
        Path("agent"),
        Path("room"),
        Path("context_memory"),
        Path("llm_gateway/services"),
    ]
    violations: list[str] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            normalized = path.as_posix()
            if normalized.startswith(allowed_prefixes):
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id.endswith("settings")
                    and node.attr in LLM_SETTINGS_FIELDS
                ):
                    violations.append(f"{path}:{node.attr}")
                if _is_settings_getattr(node):
                    violations.append(f"{path}:{node.args[1].value}")
    assert not violations


def _forbidden_imports(tree: ast.AST) -> set[str]:
    forbidden: set[str] = set()
    for node in ast.walk(tree):
        modules = _imported_modules(node)
        for module in modules:
            if module in FORBIDDEN_IMPORTS or module.split(".")[0] in FORBIDDEN_IMPORTS:
                forbidden.add(module)
            if module == "google" and isinstance(node, ast.ImportFrom):
                names = {alias.name for alias in node.names}
                if "genai" in names:
                    forbidden.add("from google import genai")
    return forbidden


def _imported_modules(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom) and node.module:
        return {node.module}
    return set()


def _imported_roots(node: ast.AST) -> set[str]:
    return {module.split(".")[0] for module in _imported_modules(node)}


def _is_os_getenv_call(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "getenv"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


def _is_settings_getattr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id.endswith("settings")
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in LLM_SETTINGS_FIELDS
    )

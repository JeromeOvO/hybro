import ast
import inspect
from pathlib import Path

from common.protocols import SSETransport
from delivery.facade import DeliveryCompatibility, DeliveryFacade
from execution.cancellation import CancellationRuntime

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DELIVERY_ROOT = BACKEND_ROOT / "delivery"


def test_cancellation_persistence_and_reconciliation_are_execution_owned():
    orchestration_root = BACKEND_ROOT / "execution" / "orchestration"
    assert not (orchestration_root / "cancellation_finalizer.py").exists()

    jobs_source = (BACKEND_ROOT / "jobs" / "stale_task_checker.py").read_text()
    assert "list_pending_cancellation" not in jobs_source
    assert "mark_cancellation_reconciled" not in jobs_source
    assert ".mark_reconciled(" not in jobs_source

    runtime_protocols = (
        BACKEND_ROOT / "common" / "protocols" / "runtime_store_protocols.py"
    ).read_text()
    for mutation in (
        "cancel_message",
        "list_pending_cancellation_markers",
        "mark_cancellation_reconciled",
    ):
        assert mutation not in runtime_protocols

    assert (BACKEND_ROOT / "execution" / "cancellation" / "finalizer.py").exists()
    assert (BACKEND_ROOT / "execution" / "cancellation" / "service.py").exists()


def test_delivery_runtime_has_no_cancellation_ownership():
    forbidden = {
        "CancellationToken",
        "CancellationWatcher",
        "cancellation_watcher",
        "create_token",
        "get_token",
        "release_token",
        "release_active_token",
        "remove_token",
        "clear_cancellation",
        "publish_cancellation",
        "redis_cancel_channel",
        "redis_cancel_key_prefix",
    }
    for path in DELIVERY_ROOT.rglob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        identifiers = names | attrs
        assert not (forbidden & identifiers), path.relative_to(BACKEND_ROOT)


def test_delivery_public_surfaces_do_not_expose_cancellation_methods():
    forbidden = {
        "is_cancelled",
        "cancel_message",
        "cancel_message_and_broadcast",
        "check_cancelled",
        "clear_cancellation",
        "create_token",
        "get_token",
        "release_active_token",
        "remove_token",
        "start_change_stream_watcher",
        "stop_change_stream_watcher",
    }
    for surface in (DeliveryFacade, DeliveryCompatibility, SSETransport):
        assert not (forbidden & set(dir(surface)))


def test_common_eventing_has_no_cancellation_semantics():
    eventing_root = BACKEND_ROOT / "common" / "eventing"
    for path in eventing_root.rglob("*.py"):
        source = path.read_text().lower()
        assert "cancellationtoken" not in source
        assert '"cancel:global"' not in source
        assert '"cancelled:"' not in source


def test_orchestration_does_not_call_cancellation_through_delivery():
    for relative in ("room/compat/runtime.py",):
        source = (BACKEND_ROOT / relative).read_text()
        assert "self.delivery.create_token" not in source
        assert "self.delivery.get_token" not in source
        assert "self.delivery.remove_token" not in source
        assert "self.delivery.clear_cancellation" not in source


def test_container_wires_one_cancellation_collection_to_execution_service():
    source = inspect.getsource(__import__("container")._runtime_lifespan)
    assert "MongoCancellationMarkerRepository" in source
    assert 'mongo_dal.collection("cancelled_messages")' in source
    assert "cancellation_message_reader" in source
    assert "execution_facade.cancellation_service" in source
    assert "set_cancellation_reconciliation_deps" in source


def test_container_starts_cancellation_before_delivery_and_stops_before_mongo():
    source = inspect.getsource(__import__("container")._runtime_lifespan)
    assert source.index("await _cancellation_runtime.start()") < source.index(
        "await _delivery_facade.start()"
    )
    assert source.rindex('("cancellation", _cancellation_runtime.stop)') < (
        source.rindex('("mongo", _mongo_dal.close)')
    )


def test_execution_owns_cancellation_runtime_registry():
    source = inspect.getsource(CancellationRuntime)
    assert "self._tokens" in source
    assert "self._tombstones" in source
    assert "CancellationWatcher" in source

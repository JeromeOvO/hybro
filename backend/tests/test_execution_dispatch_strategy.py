from execution.orchestration.dispatch_strategy import DispatchStrategy, resolve_strategy


def test_resolve_strategy_is_owned_by_execution_orchestration() -> None:
    assert resolve_strategy(use_supervisor=True, agent_count=3) == DispatchStrategy.SUPERVISOR
    assert resolve_strategy(use_supervisor=False, agent_count=3) == DispatchStrategy.SEQUENTIAL
    assert resolve_strategy(use_supervisor=False, agent_count=1) == DispatchStrategy.SINGLE

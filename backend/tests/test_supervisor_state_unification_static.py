from pathlib import Path


def test_supervisor_executor_has_no_run_v2_business_path():
    source = Path("execution/orchestration/supervisor_executor.py").read_text()

    assert "async def run_v2(" not in source
    assert "return await self.run_v2(" not in source
    assert "self.run_v2(" not in source


def test_room_message_center_does_not_route_to_run_v2():
    source = Path("execution/orchestration/room_message_center.py").read_text()

    assert "self.supervisor_executor.run_v2" not in source
    assert "if is_v2_supervisor" not in source
    assert "if is_v2_resume" not in source

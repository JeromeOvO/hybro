from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = [
    ROOT / "api_gateway",
    ROOT / "delivery",
    ROOT / "execution",
    ROOT / "jobs",
]
PRODUCTION_FILES = [ROOT / "main.py"]


def test_no_production_legacy_sse_frame_emitters():
    offenders = []
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            if (
                "emit_legacy_frame" in text
                or "_emit_legacy_frame" in text
                or "_should_deliver_legacy" in text
            ):
                offenders.append(str(path.relative_to(ROOT)))
    for path in PRODUCTION_FILES:
        text = path.read_text()
        if (
            "emit_legacy_frame" in text
            or "_emit_legacy_frame" in text
            or "_should_deliver_legacy" in text
        ):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_no_production_room_raw_broadcasts():
    offenders = []
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            if "broadcast_to_room(" in text:
                offenders.append(str(path.relative_to(ROOT)))
    for path in PRODUCTION_FILES:
        text = path.read_text()
        if "broadcast_to_room(" in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []

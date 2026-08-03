from __future__ import annotations

from typing import Any

from room.timeline import SOURCE_RANK, timeline_sort_us_from_value


def normalize_history_rows(
    user_rows: list[dict[str, Any]],
    agent_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in user_rows:
        item = dict(row)
        item.setdefault("message_type", "user")
        rows.append(item)
    for row in agent_rows:
        item = dict(row)
        item.setdefault("message_type", "agent")
        rows.append(item)
    return sort_messages(rows)


def sort_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_message_sort_key)


def status_update_payload(status: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_content.message_task.status.state": status,
        **fields,
    }


def select_thread(
    rows: list[dict[str, Any]],
    parent_message_id: str,
) -> list[dict[str, Any]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parents = {
            str(parent)
            for parent in (row.get("parent_message_id"), row.get("related_message_id"))
            if parent is not None
        }
        for parent in parents:
            by_parent.setdefault(parent, []).append(row)

    selected: list[dict[str, Any]] = []
    seen = {parent_message_id}
    frontier = [parent_message_id]
    while frontier:
        next_frontier: list[str] = []
        for parent in frontier:
            for row in sort_messages(by_parent.get(parent, [])):
                message_id = row.get("message_id")
                if not message_id or str(message_id) in seen:
                    continue
                seen.add(str(message_id))
                selected.append(row)
                next_frontier.append(str(message_id))
        frontier = next_frontier
    return selected


def _message_sort_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    sort_us = row.get("timeline_sort_us")
    missing = 0
    if isinstance(sort_us, bool) or not isinstance(sort_us, int):
        try:
            sort_us = timeline_sort_us_from_value(row.get("message_created_at"))
        except ValueError:
            sort_us = 0
            missing = 1
    source = str(row.get("message_type") or "agent")
    return (
        missing,
        sort_us,
        SOURCE_RANK.get(source, SOURCE_RANK["agent"]),
        str(row.get("message_id") or ""),
    )

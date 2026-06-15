from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """
    Ensure a datetime is timezone-aware with UTC.

    If the datetime is naive (no timezone info), assume it's UTC and add the timezone.
    If it already has timezone info, return as-is.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt

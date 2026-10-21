"""IANA timezone and quiet-hour calculations for alert scheduling."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def checked_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc


def in_quiet_hours(instant: datetime, timezone_name: str, start: time, end: time) -> bool:
    """Whether ``instant`` falls in local wall-clock quiet hours.

    A quiet interval can cross midnight. Equal bounds disable quiet hours.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    if start == end:
        return False
    local = instant.astimezone(checked_zone(timezone_name)).timetz().replace(tzinfo=None)
    if start < end:
        return start <= local < end
    return local >= start or local < end


def next_quiet_hours_end(
    instant: datetime, timezone_name: str, start: time, end: time
) -> datetime:
    """Return the next UTC quiet-hours end at or after ``instant``.

    DST transitions are handled by constructing the endpoint in the user's
    local zone then converting back to UTC.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    zone = checked_zone(timezone_name)
    local = instant.astimezone(zone)
    if not in_quiet_hours(instant, timezone_name, start, end):
        return instant.astimezone(timezone.utc)
    end_date = local.date()
    if start > end and local.timetz().replace(tzinfo=None) >= start:
        end_date += timedelta(days=1)
    endpoint = datetime.combine(end_date, end, tzinfo=zone)
    if endpoint <= local:
        endpoint = datetime.combine(end_date + timedelta(days=1), end, tzinfo=zone)
    return endpoint.astimezone(timezone.utc)


def previous_local_date(instant: datetime, timezone_name: str):
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    return instant.astimezone(checked_zone(timezone_name)).date() - timedelta(days=1)


def morning_due_at(local_date, timezone_name: str) -> datetime:
    """07:00 local on ``local_date``, returned as aware UTC datetime."""
    return datetime.combine(local_date, time(7, 0), checked_zone(timezone_name)).astimezone(
        timezone.utc
    )

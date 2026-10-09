"""Parsing and validation of request parameters. Everything here runs before any query."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .errors import invalid

MAX_RANGE = timedelta(days=366)

# Names that may reach the catalogue lookup. Anything else is rejected as malformed (422)
# before it is compared with the catalogue, so an unknown but well-formed name is a 404.
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
LOCATION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Station ids come from the manifest catalogue and are upper case (for example BG0051A).
STATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|\+00:00)$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_identifier(value: str, name: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise invalid(f"'{name}' must be a lowercase identifier (letters, digits, underscore).")
    return value


def check_location(value: str) -> str:
    if not LOCATION_ID.fullmatch(value):
        raise invalid("'location' must be a lowercase identifier (letters, digits, underscore, hyphen).")
    return value


def check_station(value: str) -> str:
    if not STATION_ID.fullmatch(value):
        raise invalid("'station_id' must be a station identifier (letters, digits, underscore, hyphen).")
    return value


def parse_instant(value: str, name: str) -> datetime:
    """ISO-8601 timestamp in UTC. Naive values and other offsets are rejected."""
    if not _INSTANT.fullmatch(value):
        raise invalid(f"'{name}' must be an ISO-8601 UTC timestamp such as 2026-10-14T00:00:00Z.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise invalid(f"'{name}' is not a valid date and time.") from None
    return parsed.astimezone(timezone.utc)


def parse_day(value: str, name: str) -> date:
    """A calendar date (YYYY-MM-DD) or a UTC timestamp, reduced to its date."""
    if _DAY.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise invalid(f"'{name}' is not a valid date.") from None
    return parse_instant(value, name).date()


def check_instant_range(start: datetime, end: datetime) -> None:
    if end < start:
        raise invalid("'to' must not be before 'from'.")
    if end - start > MAX_RANGE:
        raise invalid("The requested range is longer than 366 days.")


def parse_calendar_date(value: str, name: str) -> date:
    """A calendar date in the form YYYY-MM-DD. Timestamps are not accepted."""
    if not _DAY.fullmatch(value):
        raise invalid(f"'{name}' must be a date in the form YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise invalid(f"'{name}' is not a valid date.") from None


def check_day_range(start: date, end: date, max_days: int = 366) -> None:
    if end < start:
        raise invalid("'to' must not be before 'from'.")
    if end - start > timedelta(days=max_days):
        raise invalid(f"The requested range is longer than {max_days} days.")

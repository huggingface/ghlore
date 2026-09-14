"""Reading and writing GitHub's timestamps, in one place.

Both directions matter and both have a trap. Parsing: ``fromisoformat`` does not accept a
trailing ``Z`` before Python 3.11 and this project's floor is 3.10, so the substitution is
required rather than defensive. Formatting: GitHub wants ``Z``, not ``+00:00``.

Everything downstream is aware UTC -- section 5.2 compares timestamps, and a naive/aware
mix there silently loses threads.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

UTC = dt.timezone.utc


def parse_timestamp(value: Any) -> dt.datetime | None:
    """Parse a GitHub timestamp as aware UTC. ``None`` and ``""`` pass through."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso_utc(moment: dt.datetime) -> str:
    """Format for a ``since=`` parameter, in the spelling GitHub returns."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

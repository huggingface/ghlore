"""``5m`` -> 300.0, for anything that takes an interval.

Its own module because two loops need it and they must not import each other: the poll
runs in the indexer, the clone refresh (:mod:`relore.code.refresh`) runs inside `serve`,
and `relore.code` is on the client's import graph, where a GitHub client may not appear
(AGENTS.md, invariant 1). Stdlib only, so it is safe from either side.
"""

from __future__ import annotations

import re

_INTERVAL = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(value: str) -> float:
    """``5m`` -> 300.0. Accepts a bare number of seconds too."""
    match = _INTERVAL.match(value.strip())
    if match:
        return float(match.group(1)) * _UNITS[match.group(2)]
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"cannot read {value!r} as an interval; try 30s, 5m, 2h") from exc

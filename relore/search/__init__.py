"""Retrieval. Section 6 for the design, section 4.1 for why there is a backend per dialect.

Full text, filters, the section 6.2 trust floor, section 6's query expansion, and now its
**weighted score with per-kind decay** (``ranking.py``).

The order that happened here is the order section 10 asked for and is worth keeping
visible. Expansion went first, because section 10.3 showed the rationale slice failing by
returning *nothing* rather than by ranking badly and a weight set cannot reorder an empty
result. The weights went second, after the evaluation set was **frozen** -- a weight fitted
against a set that can still grow is a weight nobody can argue with later. Their target is
narrow: section 10.4 #1 left ``precedent`` recall up a tenth and its MRR flat, because a
textless expansion leg scored every row 0.
"""

from __future__ import annotations

from relore.search.backends import SearchBackend, open_backend
from relore.search.expansion import Leg, expand, search_expanded
from relore.search.queries import (
    MAX_HITS,
    MAX_SNIPPET_CHARS,
    QUERY_KINDS,
    BackendInfo,
    Hit,
    QueryError,
    SearchQuery,
    ThreadView,
    render_age,
)
from relore.search.ranking import HALF_LIFE_DAYS, WEIGHTS, RankSpec, Weights, decay, half_life

__all__ = [
    "HALF_LIFE_DAYS",
    "MAX_HITS",
    "MAX_SNIPPET_CHARS",
    "QUERY_KINDS",
    "BackendInfo",
    "Hit",
    "Leg",
    "QueryError",
    "RankSpec",
    "SearchBackend",
    "SearchQuery",
    "ThreadView",
    "WEIGHTS",
    "Weights",
    "decay",
    "expand",
    "half_life",
    "open_backend",
    "render_age",
    "search_expanded",
]

"""The SQLite backend: FTS5, ranked by ``bm25``. A development affordance, not a
deployment (section 4.1).

Two things about it are load-bearing, and both are traps rather than preferences.

**The score is negated.** FTS5's ``bm25()`` returns a value where *more negative* is more
relevant. :meth:`SqliteBackend.fts_score` flips it so "higher is better" holds on both
backends and the caps in ``queries.py`` need no dialect knowledge.

**It cannot rank text it did not MATCH.** ``bm25()`` is an auxiliary function of the FTS5
index, so it is an error anywhere but a query that joined and matched it -- which means
:meth:`SqliteBackend.unfiltered_fts_score` has to return ``None`` and section 6's lexical
term is simply absent from a textless expansion leg here. The signal overlaps and the decay
still score, so those legs are ordered rather than tied; they are ordered by less. Section
4.1 already refuses to compare a number across backends, and this is one of the reasons.

**User text never reaches ``MATCH`` as written.** ``MATCH`` takes a query *language* --
``"``, ``*``, ``:``, ``^``, ``-``, ``(`` and the bare words ``AND``/``OR``/``NOT`` all
mean something in it, and an unbalanced quote is a hard error rather than an empty result.
A pasted traceback contains most of those characters. So the text is tokenized and
re-emitted as quoted terms, which is also what makes this an AND of the terms and
therefore the same question ``plainto_tsquery`` answers on the other side.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, literal, literal_column, table
from sqlalchemy import text as sa_text

from relore.search.backends.base import SearchBackend
from relore.search.queries import BackendInfo, tokenize
from relore.store import schema as s

#: Created by migration 3, external-content over ``documents``, and deliberately not in
#: ``store/schema.py``: it is not part of the portable core, and ``metadata.create_all``
#: must never try to make it on Postgres.
documents_fts = table("documents_fts")

_MATCH = sa_text("documents_fts MATCH :fts_query")
_BM25 = literal_column("bm25(documents_fts)")


class SqliteBackend(SearchBackend):
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="sqlite", ranking="bm25", capabilities=frozenset({"fulltext", "weighted"})
        )

    def fts_score(self, text: str) -> Any:
        if not _match_expression(text):
            return literal(0.0)
        return -_BM25

    def fts_filter(self, stmt: Select, text: str) -> Select:
        expression = _match_expression(text)
        if not expression:
            # Every term was punctuation. An empty MATCH is a syntax error in FTS5, and
            # "no results" is the honest answer, so ask for a contradiction rather than
            # for everything.
            return stmt.where(literal(False))
        return stmt.join(
            documents_fts, literal_column("documents_fts.rowid") == s.documents.c.id
        ).where(_MATCH.bindparams(fts_query=expression))

    def unfiltered_fts_score(self, text: str) -> Any | None:
        """Nothing. See the module docstring: FTS5 refuses ``bm25()`` outside a MATCH."""
        return None

    def age_days(self, column: Any) -> Any:
        """``julianday`` is in days already, and ``'now'`` is UTC -- which is what the
        column holds, since ``store/dialect.UTCDateTime`` normalizes on the way in."""
        return func.julianday(literal("now")) - func.julianday(column)


def _match_expression(query: str) -> str:
    """User text as an FTS5 ``MATCH`` expression: every term quoted, implicitly ANDed.

    Quoting is what neutralizes the query language, so a pasted traceback searches for its
    own words instead of erroring on its own punctuation.
    """
    return " ".join(f'"{term}"' for term in tokenize(query))

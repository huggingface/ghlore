"""The Postgres backend: ``tsvector`` + GIN, ranked by ``ts_rank_cd``.

The only supported deployment (section 4.1), and the engine section 6's weights are being
fitted to. ``documents.fts`` is a generated column created by migration 2 and deliberately
absent from ``store/schema.py``, so it is named here as a column expression rather than
read off the table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, literal, literal_column

from relore.search.backends.base import SearchBackend
from relore.search.queries import BackendInfo, tokenize

_FTS = literal_column("documents.fts")

#: How many terms a *scoring* query may carry. A pasted traceback tokenizes into hundreds,
#: and a disjunction over all of them is both slow and undiscriminating -- every document
#: in the corpus matches `self` or `line`. The caller's first two dozen words are what the
#: question is about; section 6's signal legs carry the precise terms.
MAX_SCORE_TERMS = 24


class PostgresBackend(SearchBackend):
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="postgresql",
            ranking="ts_rank_cd",
            capabilities=frozenset({"fulltext", "weighted"}),
        )

    def _tsquery(self, text: str) -> Any:
        """Not ``websearch_to_tsquery``, whose quoted phrases, ``OR`` and ``-negation``
        have no SQLite counterpart. ``plainto_tsquery`` is a plain AND of the terms, which
        is what the FTS5 backend can also express -- so the two engines answer the same
        *question* and differ only in how they score it. Both take arbitrary user text
        without raising, which the raw ``to_tsquery`` does not.
        """
        return func.plainto_tsquery("english", text)

    def fts_score(self, text: str) -> Any:
        return func.ts_rank_cd(_FTS, self._tsquery(text))

    def fts_filter(self, stmt: Select, text: str) -> Select:
        return stmt.where(_FTS.op("@@")(self._tsquery(text)))

    def unfiltered_fts_score(self, text: str) -> Any | None:
        """Relevance to text this query is not filtering on -- and it **ORs** the terms.

        ``ts_rank_cd`` is an ordinary function of a stored ``tsvector``, so it needs no join
        and no ``@@``: a row matching nothing simply scores 0. That is what lets a signal
        leg of an expanded call prefer the rows the caller's words are about
        (``search/ranking.py``), and it is a capability the FTS5 backend does not have.

        **Filtering ANDs; scoring ORs, and conflating the two is a real bug that shipped.**
        :meth:`_tsquery` is a conjunction, which is right where it decides *admission* --
        section 10.4: it is what stops a pasted sentence matching everything. Used to
        *order* a set the text did not select, the same conjunction scores 0 for almost
        every row, so the whole page ties and falls through to the recency tie-break.

        Measured on the query that exposed it -- `--file src/transformers/masking_utils.py`,
        `kind=rationale`, 861 admissible documents on the matching threads: the conjunction
        ranks **1** of them above zero, the disjunction 126. The other 860 tied, and the
        recency fallback then reliably picked each thread's *last* document, which on a
        merged pull request is the approval. Nine of ten slots came back ``LGTM``, ``Thx``,
        ``Yep`` -- retrieval that is not wrong so much as not ranked, and invisible to
        Recall@k because the right thread was still on the page.

        A disjunction is the correct scoring question anyway: "how much of what they asked
        about does this document carry" is graded, and section 6 already spends the
        conjunction on the ``text`` leg where every surviving row satisfies it.
        """
        terms = tokenize(text)[:MAX_SCORE_TERMS]
        if not terms:
            return None
        # `tokenize` yields `[A-Za-z0-9_]+` only, so this cannot reach `to_tsquery` as
        # syntax -- which is the reason the raw form is usable here at all (see
        # `_tsquery`). An all-stopword query lexes to the empty tsquery and ranks 0, which
        # is the right answer and not an error.
        disjunction = func.to_tsquery("english", " | ".join(terms))
        # Both, not one: a document carrying *every* term really is stronger evidence than
        # one carrying some, and the conjunction is what says so. The disjunction then
        # orders everything underneath it instead of leaving it tied at zero. Measured, the
        # sum and the disjunction alone are within a thousandth of each other on every
        # slice -- the conjunction is nonzero too rarely to reorder much -- so this is
        # chosen for using both signals rather than for the number (section 10.6).
        return func.ts_rank_cd(_FTS, self._tsquery(text)) + func.ts_rank_cd(_FTS, disjunction)

    def age_days(self, column: Any) -> Any:
        return func.extract("epoch", func.now() - column) / literal(86400.0)

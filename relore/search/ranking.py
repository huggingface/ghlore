"""Section 6's weighted score: the terms, the weights, and the per-kind decay.

Nothing here touches a database. It is the *specification* of the score -- what the terms
are, what they are worth, and how age discounts them -- and the backends render it into
SQL. That split is the same one section 4.1 makes for the rest of retrieval, and it is
what lets a weight be read, argued with and changed without opening a query.

**What this is aimed at, and it is narrow.** Section 10.4 measured query expansion and
found `precedent` recall moving a tenth while its MRR stayed flat -- 0.375 -> 0.475 against
0.182 -> 0.191. The cause is written down there: a textless leg scores every row 0, so the
tie breaks arbitrarily and expansion *finds* the precedent thread without being able to
order it. Finding it was solved; ordering it was not. So the job of this module is to give
every row a score even when no text matched, which is why the terms below are the signal
overlaps rather than the full-text rank.

**Filter by the leg, score by the whole question.** A signal leg carries one file or one
symbol and no text (``expansion.py``), so within that leg every row overlaps exactly one
value and a score computed from the leg alone is still a constant. :class:`RankSpec` is
what fixes it: the leg *filters* on its own term and *scores* against everything the call
contained -- the caller's filters, the caller's text, and every signal the expansion
derived from it. A thread carrying three of the derived symbols then outranks one carrying
one, on every leg, with no text involved anywhere.

**No weight here is fitted.** Section 10.4 set its three constants from first principles,
wrote them down before the first benchmark run, and did not touch them afterwards; the same
discipline applies to every weight in this file, and for a stronger reason -- these are the
thing section 10 says to freeze the evaluation set before moving. The set was frozen first,
the weights were written second, and the run came third; none of them moved afterwards. The
reasoning for each is recorded beside it so a later reader can disagree with the argument
rather than guess at the intent.

**One thing here *was* decided by the measurement, and it is flagged rather than folded in:**
:data:`DECAY_ENABLED`. Section 10 freezes the set precisely so that a later decision may be
made against it, so this is the sanctioned direction -- but a reader should be able to see
which numbers are arguments and which are results.

**Two things section 6 names that are deliberately not here.**

*``author_prior``.* Section 6.2 is explicit that it "breaks ties **within** a trust tier and
never across one", and the column that is actually populated today is ``trust`` -- the tier
itself. A prior keyed on that would break ties *across* tiers, which is the one thing that
sentence forbids, and it would quietly turn section 6.2's filter into the weight it exists
to not be. Keying it on ``author_assoc`` instead is possible and unmeasured: ``MEMBER``
appears in both tiers (section 6.2), so the ladder is not the tier's. It waits for a
measurement that asks for it.

*``relationship_bonus``.* ``thread_links`` is created and empty until milestone 4, so the
term is declared with a weight of 0 and emits no SQL at all -- a zero-weight term costs
nothing and turns on by changing a default rather than by adding a query.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - a type, not a dependency
    from relore.search.queries import SearchQuery


@dataclass(frozen=True)
class Weights:
    """Section 6's ``w_*``, and the argument for each.

    They are ordered by how *specific* the evidence is, because that is the only ordering
    that can be defended before a measurement exists. Specificity here means: how many
    threads in a corpus could carry this term by coincidence.
    """

    #: A normalized exception and message (section 5.3) is minted by a machine and is
    #: nearly unique. Section 10.2's finding 2 says the failure slice is close to circular
    #: because ``--error`` matches exactly the ground truth -- which is the same fact from
    #: the other side: this is the most discriminating term in the corpus.
    error: float = 3.0
    #: A pytest node id is machine-minted too, and unique by construction. Same tier.
    test: float = 3.0
    #: Specific, but ambiguous until milestone 4 resolves it: section 1 notes ``forward``
    #: appears in thousands of comments, and that symbol disambiguation is what makes
    #: ``thread_symbols`` discriminate at all. Well above a file, well below an error.
    symbol: float = 1.5
    #: The unit, and deliberately not more. Section 13's caveat records that file overlap
    #: has a known *recall floor* until rename chains exist (milestone 4), because a thread
    #: predating a file's last move cannot match on path. Raising this to compensate would
    #: be tuning against a missing input.
    file: float = 1.0
    #: One full-text match is worth one file overlap. This is the only term whose scale is
    #: the engine's rather than ours -- ``ts_rank_cd`` and ``bm25`` do not agree on what 1.0
    #: means -- which is a second reason, after section 4.1, that a weight set describes one
    #: backend and section 10 refuses to compare across them.
    lexical: float = 1.0
    #: Milestone 4. Zero emits no SQL; see the module docstring.
    relationship: float = 0.0


WEIGHTS = Weights()


#: Section 6's decay table, as half-lives in days. ``None`` is *no decay*, which is a
#: position and not a missing entry: for a rationale query the oldest thread is often the
#: answer, and recency is actively misleading.
#:
#: An unknown kind decays not at all, on purpose. Decay is the only factor here that can
#: *demote* a correct old answer, so the safe default when nobody said what kind of question
#: this is is to leave the order alone.
HALF_LIFE_DAYS: dict[str, float | None] = {
    "failure": 548.0,  # ~18 months: a five-year-old runtime error is rarely today's bug
    "precedent": 1461.0,  # ~4 years: conventions drift slowly
    "rationale": None,  # the oldest thread *is* the answer
}


#: **Decay is built, wired, tested -- and off, because the only corpora that exist cannot
#: test it.** Both indexes are section 5.5 *samples* with a 2026-03-01 floor, so they span
#: about six months; the shortest half-life above is eighteen. Over a window a quarter of
#: its own characteristic time a half-life cannot express "old is stale", only "slightly
#: older is slightly worse" -- which on this corpus is an age-proportional noise term, and
#: it measures as one. On the frozen set, turning it on costs MRR on both slices that have
#: a half-life and gains nothing anywhere: ``failure`` 0.927 -> 0.917 and ``precedent``
#: 0.286 -> 0.259, with ``precedent`` R@10 also falling 0.525 -> 0.500.
#:
#: So the table above stays exactly as section 6 wrote it -- it is a claim about an
#: eight-year history and this is not one -- and the gate records that the claim is
#: untested rather than disproved. **Turn it on with the first full-history backfill**,
#: which is the run that can actually say whether a five-year-old traceback should be
#: discounted. Leaving it on now would ship a measured regression on the strength of a
#: hypothesis this corpus is structurally unable to check.
DECAY_ENABLED = False


def half_life(kind: str | None) -> float | None:
    """The half-life the *ranking* uses: section 6's table, or nothing while it is gated.

    Separate from :data:`HALF_LIFE_DAYS` on purpose. The table is the design; this is what
    the backend asks, so that turning decay on is one constant rather than an edit spread
    across two dialects' SQL.
    """
    return HALF_LIFE_DAYS.get(kind or "") if DECAY_ENABLED else None


def decay_curve(age_days: float, life: float | None) -> float:
    """Section 6's decay, as a *rational* half-life rather than an exponential one.

    ``1 / (1 + age / H)`` is exactly 0.5 at the half-life, monotone, and bounded in
    ``(0, 1]`` -- so it is a half-life decay in the sense the table means. It is not
    ``2 ** (-age/H)``, and the reason is portability rather than taste: ``exp`` and
    ``power`` are SQL math functions SQLite only has when it was compiled with them, and
    section 4.1 will not have a ranking that silently errors on the dialect the test suite
    runs on. The rational form needs nothing but arithmetic.

    The consequence is a heavier tail: at four half-lives the exponential is at 0.06 and
    this is at 0.20. For this corpus that is the safer direction -- section 6's whole point
    about age is that a four-year-old comment may be exactly right about intent, and the
    rendered age is what tells the model to check.

    The curve, unlike :func:`decay`, is not gated: it is the shape being specified, and a
    test of the shape should not silently pass because the feature is off.
    """
    if life is None or age_days <= 0:
        return 1.0
    return 1.0 / (1.0 + age_days / life)


def decay(age_days: float, kind: str | None) -> float:
    """What the ranking actually applies -- :func:`decay_curve` under the gate above."""
    return decay_curve(age_days, half_life(kind))


@dataclass(frozen=True)
class RankSpec:
    """What a row is scored *against*, which is not always what it was filtered by.

    See the module docstring: a signal leg filters on one term and has no text, so scoring
    it against itself yields a constant and the tie breaks arbitrarily. The spec is the
    whole question -- and on an expanded call, the whole question includes what expansion
    derived from the caller's text, since those terms are evidence the caller supplied
    without typing them into a filter.
    """

    text: str = ""
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    kind: str | None = None

    @classmethod
    def of(cls, query: SearchQuery) -> RankSpec:
        """The spec a plain, unexpanded call is scored by: itself."""
        return cls(
            text=query.text,
            files=query.files,
            symbols=query.symbols,
            errors=query.errors,
            tests=query.tests,
            kind=query.kind,
        )

    def with_signals(self, other: RankSpec) -> RankSpec:
        """Union the signal terms, keeping this spec's text and kind.

        Used by expansion to fold every leg's derived term back into one scoring question.
        Order is preserved and duplicates dropped, because the terms become SQL predicates
        and asking the same one twice would count one piece of evidence as two -- the
        argument section 10.4 already makes for not asking a path as a symbol.
        """
        return replace(
            self,
            files=_union(self.files, other.files),
            symbols=_union(self.symbols, other.symbols),
            errors=_union(self.errors, other.errors),
            tests=_union(self.tests, other.tests),
        )

    @property
    def has_signals(self) -> bool:
        return bool(self.files or self.symbols or self.errors or self.tests)

    @property
    def has_terms(self) -> bool:
        return bool(self.text.strip()) or self.has_signals


def _union(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))

"""Section 6's weights and decay as pure functions (section 12).

Nothing here touches a database, which is the point of ``search/ranking.py`` being a
specification rather than a query: the shape of the decay curve and the union rule for a
scoring question can be argued with from a fixture string.
"""

from __future__ import annotations

import pytest

from relore.search import ranking
from relore.search.queries import QUERY_KINDS, SearchQuery
from relore.search.ranking import (
    HALF_LIFE_DAYS,
    WEIGHTS,
    RankSpec,
    Weights,
    decay,
    decay_curve,
    half_life,
)


@pytest.fixture
def decaying(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 6's decay, turned on.

    It ships gated (``ranking.DECAY_ENABLED``) because the only corpora that exist are
    six-month samples and the shortest half-life is eighteen months, so the measurement
    that would justify it cannot be taken yet. The behaviour is still tested -- a feature
    whose tests are skipped while it is off is a feature that will not work when it is
    turned on.
    """
    monkeypatch.setattr(ranking, "DECAY_ENABLED", True)


REPO = "owner/name"


# -- decay -----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["failure", "precedent"])
def test_decay_is_exactly_a_half_at_the_half_life(kind: str) -> None:
    """The rational form is not an approximation of the table, it *meets* it at the one
    point the table names. That is what makes "half-life ~18 months" a true description of
    ``1 / (1 + age / H)`` and not a borrowed word (``ranking.decay``)."""
    life = HALF_LIFE_DAYS[kind]
    assert life is not None
    assert decay_curve(life, life) == pytest.approx(0.5)


def test_decay_has_a_heavier_tail_than_an_exponential_would() -> None:
    """Recorded rather than merely allowed: at four half-lives an exponential is at 0.06
    and this is at 0.20. Section 6's point about age is that an old comment may be exactly
    right about intent, so the heavier tail is the safer direction -- but a later reader
    comparing this to a published exponential curve should find the difference asserted."""
    life = HALF_LIFE_DAYS["failure"]
    assert decay_curve(4 * life, life) == pytest.approx(0.2)


def test_a_rationale_query_does_not_decay_at_all(decaying: None) -> None:
    """Section 6: the oldest thread *is* the answer, and recency is actively misleading."""
    assert HALF_LIFE_DAYS["rationale"] is None
    assert decay(40 * 365, "rationale") == 1.0


def test_an_unknown_kind_does_not_decay(decaying: None) -> None:
    """Decay is the only factor that can demote a *correct* old result, so a question
    nobody labelled must not be quietly aged. Fail safe, not fail fast."""
    assert half_life(None) is None
    assert decay(9999.0, None) == 1.0
    assert decay(9999.0, "not-a-kind") == 1.0


def test_a_future_timestamp_cannot_buy_rank(decaying: None) -> None:
    """GitHub's clock is not ours. A negative age would push the denominator below 1 and
    score *above* the undecayed maximum, so it is clamped rather than trusted -- the same
    guard the backend applies in SQL."""
    assert decay(-500.0, "failure") == 1.0
    assert decay(0.0, "failure") == 1.0


def test_decay_is_monotone(decaying: None) -> None:
    ages = [0.0, 30.0, 365.0, 1000.0, 5000.0]
    scores = [decay(age, "failure") for age in ages]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 < score <= 1.0 for score in scores)


def test_every_query_kind_has_a_decay_position() -> None:
    """A missing entry and a deliberate ``None`` are the same value, so the *keys* are what
    prove the table was filled in for all three slices."""
    assert set(HALF_LIFE_DAYS) == set(QUERY_KINDS)


def test_decay_is_off_until_a_corpus_can_test_it() -> None:
    """The gate, asserted so it cannot be flipped by accident.

    Section 6's table is a claim about an eight-year history; both indexes are six-month
    samples. Measured on the frozen set, turning it on costs MRR on both slices that have a
    half-life (``failure`` 0.927 -> 0.917, ``precedent`` 0.286 -> 0.259) and gains nothing,
    which is what a half-life applied over a quarter of its own characteristic time does.
    The table stays; the application waits for the first full-history backfill.
    """
    assert ranking.DECAY_ENABLED is False
    assert half_life("failure") is None
    assert decay(9999.0, "failure") == 1.0
    # ...and the shape is still the shape, gate or no gate.
    assert decay_curve(548.0, 548.0) == pytest.approx(0.5)


# -- the weights -----------------------------------------------------------


def test_the_weights_are_ordered_by_specificity() -> None:
    """The one property the defaults claim before any measurement exists: a term is worth
    more when fewer threads could carry it by coincidence (``ranking.Weights``)."""
    assert WEIGHTS.error == WEIGHTS.test
    assert WEIGHTS.error > WEIGHTS.symbol > WEIGHTS.file
    assert WEIGHTS.lexical == WEIGHTS.file


def test_the_relationship_term_is_declared_and_off() -> None:
    """``thread_links`` is empty until milestone 4. Zero is what keeps the term out of the
    SQL entirely rather than adding a subquery over an empty table to every query."""
    assert WEIGHTS.relationship == 0.0


def test_weights_are_data_and_can_be_replaced_without_touching_a_query() -> None:
    """Section 6: weights live in config, not in code, because what discriminates in one
    repository does not in another."""
    assert Weights(file=9.0).file == 9.0
    assert WEIGHTS.file == 1.0  # the module default is not shared mutable state


# -- the scoring question --------------------------------------------------


def test_a_plain_call_is_scored_against_itself() -> None:
    query = SearchQuery(repos=(REPO,), text="mask", files=("a.py",), kind="precedent")
    spec = RankSpec.of(query)

    assert (spec.text, spec.files, spec.kind) == ("mask", ("a.py",), "precedent")


def test_unioning_a_leg_keeps_the_caller_s_text_and_kind() -> None:
    """ "Filter by the leg, score by the whole question": the leg contributes *evidence*,
    never scope, so its empty text must not erase the caller's."""
    caller = RankSpec.of(SearchQuery(repos=(REPO,), text="why is this here", kind="rationale"))
    leg = RankSpec.of(SearchQuery(repos=(REPO,), text="", files=("src/mod.py",)))

    merged = caller.with_signals(leg)

    assert merged.text == "why is this here"
    assert merged.kind == "rationale"
    assert merged.files == ("src/mod.py",)


def test_the_same_term_from_two_legs_is_counted_once() -> None:
    """Section 10.4's rule, applied to scoring: a term asked twice fuses one piece of
    evidence with itself. The overlap term is a *fraction* of the values asked, so a
    duplicate would also silently change the denominator."""
    spec = RankSpec(files=("a.py", "b.py"))

    merged = spec.with_signals(RankSpec(files=("b.py", "c.py")))

    assert merged.files == ("a.py", "b.py", "c.py")


def test_a_question_with_no_terms_says_so() -> None:
    """``has_terms`` is what the backend uses to decide whether there is a score at all --
    a query with nothing to rank comes back newest-first rather than in a tie."""
    assert not RankSpec().has_terms
    assert not RankSpec(text="   ").has_terms
    assert RankSpec(files=("a.py",)).has_terms
    assert RankSpec(text="mask").has_terms

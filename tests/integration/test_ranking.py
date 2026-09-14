"""Section 6's weighted score, on both dialects (section 12).

``test_search.py`` deliberately asserts no order, because the two engines do not agree on
what a full-text score means (section 4.1). Everything asserted here is ordered by terms
that **do** port -- signal overlap and decay are counted with the same predicates the
filters use, on ordinary SQL -- so a claim that holds on Postgres holds on SQLite. The one
place the engines differ is called out by name in the test that covers it.

What these are actually about is section 10.4 #1: expansion raised ``precedent`` recall by
a tenth and left its MRR flat, because a textless leg scored every row 0 and the tie broke
arbitrarily. Finding the thread was solved; ordering it was not. So the load-bearing test
here is the first one.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine

from relore.ingest.index_thread import index_thread
from relore.search import RankSpec, SearchQuery, open_backend, ranking, search_expanded

REPO = "owner/name"
ALPHA = "src/alpha.py"
BETA = "src/beta.py"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


@pytest.fixture
def decaying(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 6's decay, turned on for the two tests that are about it.

    It ships gated (``ranking.DECAY_ENABLED``): both corpora are six-month samples and the
    shortest half-life is eighteen months, so the measurement that would justify it cannot
    be taken yet -- and taken anyway it costs MRR. The wiring is still asserted on both
    dialects, because a feature whose tests are skipped while it is off is one that will
    not work when it is turned on.
    """
    monkeypatch.setattr(ranking, "DECAY_ENABLED", True)


def _index(engine: Engine, fake: FakeGitHub, *numbers: int) -> None:
    with fake.client() as client:
        for number in numbers:
            index_thread(engine, client, fake.repo, number)


def _best_per_thread(hits: list) -> list:
    """One hit per thread, in the order the page put them.

    A page is documents, not threads -- section 6 caps at ``MAX_HITS_PER_THREAD`` per
    thread precisely because a thread has several source types to spend slots on. Every
    term scored here is thread-level, so those siblings tie with each other and only the
    *first* of each carries information about the order.
    """
    seen: dict[int, object] = {}
    for hit in hits:
        seen.setdefault(hit.number, hit)
    return list(seen.values())


def _two_threads(fake: FakeGitHub) -> None:
    """Thread 1 touches both files; thread 2 touches one. Nothing else distinguishes them.

    A review comment's own ``path`` is *definitive* (section 13.2 #2), so this fixes the
    file overlap exactly rather than depending on how prose is read.
    """
    both = fake.add_pr(1, title="one", body="a body", merged_at="2026-02-01T00:00:00Z")
    fake.add_review_comment(both, 300, "the mask is built here", path=ALPHA)
    fake.add_review_comment(both, 301, "and consumed here", path=BETA)

    one = fake.add_pr(2, title="two", body="a body", merged_at="2026-02-01T00:00:00Z")
    fake.add_review_comment(one, 400, "the mask is built here", path=ALPHA)


# -- the point of the change -----------------------------------------------


def test_a_textless_leg_is_ordered_by_how_much_of_the_question_a_thread_carries(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Section 10.4 #1, fixed. The query filters on one path and carries no text at all --
    which before the weights meant every row scored 0 and the order was whatever the tie
    broke to. Scored against the whole question, the thread holding both files wins.
    """
    _two_threads(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(
        SearchQuery(repos=(REPO,), text="", files=(ALPHA,)),
        rank_as=RankSpec(files=(ALPHA, BETA)),
    )

    threads = _best_per_thread(hits)
    assert [hit.number for hit in threads] == [1, 2]
    assert threads[0].score > threads[1].score
    assert threads[0].breakdown["file"] == pytest.approx(1.0)
    assert threads[1].breakdown["file"] == pytest.approx(0.5)


def test_without_the_wider_question_the_same_call_is_a_tie(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The control for the test above, and the reason ``rank_as`` exists rather than a
    wider filter: scored against itself, a one-path leg gives both threads the same score.
    That tie is exactly what section 10.4 measured as flat MRR."""
    _two_threads(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(SearchQuery(repos=(REPO,), text="", files=(ALPHA,)))

    assert {hit.number for hit in hits} == {1, 2}
    assert len({hit.score for hit in hits}) == 1


def test_the_scoring_question_cannot_admit_a_row_the_filter_excluded(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Terms are evidence, never scope. Widening what a row is *scored* against must not
    widen what comes back, or "filter by the leg" would quietly stop being true and a leg
    would return the whole corpus."""
    _two_threads(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(
        SearchQuery(repos=(REPO,), text="", files=(BETA,)),
        rank_as=RankSpec(files=(ALPHA, BETA)),
    )

    assert {hit.number for hit in hits} == {1}


def test_expansion_orders_the_threads_it_finds(engine: Engine, fake: FakeGitHub) -> None:
    """End to end, through the path an API caller actually takes.

    Expansion derives a leg per path in the query text; the weights are what let those
    legs order their rows. Both threads are reachable -- that was already true after
    section 10.4 -- and the one carrying both paths now comes first.
    """
    _two_threads(fake)
    _index(engine, fake, 1, 2)

    hits = search_expanded(
        open_backend(engine),
        SearchQuery(repos=(REPO,), text=f"is the mask in {ALPHA} or {BETA}"),
    )

    assert [hit.number for hit in _best_per_thread(hits)] == [1, 2]


# -- decay -----------------------------------------------------------------


def _old_and_new(fake: FakeGitHub) -> None:
    """Two threads with identical evidence and six years between them."""
    old = fake.add_pr(1, title="old", body="a body", merged_at="2020-02-01T00:00:00Z")
    fake.add_review_comment(
        old, 300, "the mask is built here", path=ALPHA, created_at="2020-01-01T00:00:00Z"
    )
    new = fake.add_pr(2, title="new", body="a body", merged_at="2026-02-01T00:00:00Z")
    fake.add_review_comment(
        new, 400, "the mask is built here", path=ALPHA, created_at="2026-01-01T00:00:00Z"
    )


def test_a_failure_query_discounts_the_older_of_two_equal_answers(
    engine: Engine, fake: FakeGitHub, decaying: None
) -> None:
    """Section 6's decay table: a five-year-old runtime error is rarely today's bug."""
    _old_and_new(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(
        SearchQuery(repos=(REPO,), text="", files=(ALPHA,), kind="failure")
    )

    threads = _best_per_thread(hits)
    assert [hit.number for hit in threads] == [2, 1]
    assert threads[0].score > threads[1].score


def test_a_rationale_query_scores_them_equally(
    engine: Engine, fake: FakeGitHub, decaying: None
) -> None:
    """The same corpus, the same evidence, no decay -- because for a rationale question the
    oldest thread is often the answer.

    This asserts the *scores*, not the order: with nothing to separate them the order falls
    through to newest-first either way, so an order assertion would pass whether decay ran
    or not. That is the shape of test this section is easiest to get wrong.
    """
    _old_and_new(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(
        SearchQuery(repos=(REPO,), text="", files=(ALPHA,), kind="rationale")
    )

    assert {hit.number for hit in hits} == {1, 2}
    assert len({hit.score for hit in hits}) == 1


# -- what the breakdown says -----------------------------------------------


def test_the_breakdown_carries_only_the_terms_the_question_asked(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Section 8 gives the person tuning the weights every term as a number, and gives
    them nothing else: a breakdown padded with zeros for what the caller never asked says
    less than one that lists what was weighed. ``relationship`` is absent because its
    weight is 0 -- milestone 4 turns it on by changing a default, not by adding a query.
    """
    _two_threads(fake)
    _index(engine, fake, 1, 2)

    (hit, *_) = open_backend(engine).search(SearchQuery(repos=(REPO,), text="", files=(ALPHA,)))

    assert set(hit.breakdown) == {"score", "file"}


def test_a_query_with_nothing_to_rank_still_answers(engine: Engine, fake: FakeGitHub) -> None:
    """No terms is not a tie at zero, it is an unranked question: section 6 says recency is
    the honest order for one, and an empty result would be a lie about the corpus."""
    _two_threads(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(SearchQuery(repos=(REPO,), text="", labels=()))

    assert hits
    assert all(hit.score == 0.0 for hit in hits)


def test_the_gate_is_what_keeps_decay_out_of_the_shipped_order(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The same corpus and the same ``failure`` query as the test above, ungated.

    Without the fixture the six-year gap must not change a score, which is the assertion
    that the gate reaches the SQL rather than only the Python helper -- the two are
    separate code paths and only this one is what a caller gets.
    """
    _old_and_new(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(
        SearchQuery(repos=(REPO,), text="", files=(ALPHA,), kind="failure")
    )

    assert len({hit.score for hit in hits}) == 1


def test_a_partial_text_match_outranks_no_match_on_a_textless_leg(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Scoring text is a *graded* question, and asking it as a conjunction was a real bug.

    A signal leg does not filter on text, so the caller's words are only there to order
    what the filter returned. Asked as `plainto_tsquery` -- an AND -- almost nothing scores
    above zero, the page ties, and the recency tie-break then reliably picks each thread's
    *last* document, which on a merged pull request is the approval. Measured on the live
    index at the time: of 861 admissible documents on the matching threads, the conjunction
    ranked **1** above zero and the disjunction 126, and nine of ten slots came back
    ``LGTM``, ``Thx``, ``Yep``.

    Recall@k cannot see that -- the right thread was still on the page -- so this is the
    test that has to.

    The newer thread is the empty one on purpose: under the old scoring both tie at zero
    and recency hands it first place, so a test built the other way round would pass
    against the bug.
    """
    backend = open_backend(engine)
    if backend.unfiltered_fts_score("mask") is None:
        pytest.skip("bm25 cannot rank text it did not MATCH; see sqlite.py")

    partial = fake.add_pr(1, title="one", body="a body", merged_at="2026-02-01T00:00:00Z")
    fake.add_review_comment(
        partial, 300, "the mask helper lives here", path=ALPHA, created_at="2026-01-01T00:00:00Z"
    )
    nothing = fake.add_pr(2, title="two", body="a body", merged_at="2026-02-01T00:00:00Z")
    fake.add_review_comment(nothing, 400, "LGTM", path=ALPHA, created_at="2026-06-01T00:00:00Z")
    _index(engine, fake, 1, 2)

    hits = backend.search(
        SearchQuery(repos=(REPO,), text="", files=(ALPHA,)),
        rank_as=RankSpec(text="why is the attention mask cast here", files=(ALPHA,)),
    )

    threads = _best_per_thread(hits)
    assert [hit.number for hit in threads] == [1, 2]
    assert threads[0].breakdown["lexical"] > 0.0
    assert threads[1].breakdown["lexical"] == 0.0

"""The widening fallback: a query with terms in it never dead-ends (section 6).

Expansion fixed the pasted *traceback* -- a signal leg reaches the thread the conjunction
could not. It does nothing for pasted *prose*, which derives no signals, and prose is what
every measured zero-result page was made of: six to nine ordinary words, and a reformulation
costing a whole turn (``relore/docs/gh-vs-relore-2026-09-14.md``).

So the contract asserted here is two-sided, and the second half matters as much as the
first. **A query that had an answer is never widened**, because the strict conjunction runs
first and the fallback is reachable only from an empty page. **A widened page always says
so**, because it is not the page the caller asked for.

Ordering is asserted only where it is a property of the design rather than of the engine
(section 4.1): coverage-before-density is Postgres', and the test that pins it says so.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine

from relore.ingest.index_thread import index_thread
from relore.search import SearchQuery, open_backend, search_best
from relore.search.expansion import MIN_WIDEN_TERMS, WIDENED_ANY_TERM
from relore.search.queries import MATCH_ALL, MATCH_ANY, QueryError

REPO = "owner/name"

#: Seven ordinary words, no error, no path, no identifier -- the shape of every zero-result
#: page in the three head-to-head runs. Nothing in the corpus carries all of them.
PROSE = "FA2 static cache fullgraph compile generate override"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _index(engine: Engine, fake: FakeGitHub, *numbers: int) -> None:
    with fake.client() as client:
        for number in numbers:
            index_thread(engine, client, fake.repo, number)


def _query(**kwargs: object) -> SearchQuery:
    return SearchQuery(repos=(REPO,), **kwargs)  # type: ignore[arg-type]


# -- the vocabulary ---------------------------------------------------------


def test_match_is_all_unless_something_widens_it() -> None:
    """There is no flag and no request field for it: the default is the conjunction every
    corpus-wide search has always been, and the fallback is the only thing that changes it."""
    assert _query(text=PROSE).match == MATCH_ALL


def test_an_unknown_match_is_refused_rather_than_guessed() -> None:
    with pytest.raises(QueryError, match="unknown match"):
        _query(text=PROSE, match="some")


# -- the invariant: a page that answered is never widened -------------------


def test_a_query_with_hits_is_returned_untouched(engine: Engine, fake: FakeGitHub) -> None:
    """The half of the contract that protects every query that already worked. Widening is
    reached only from an empty page, so there is nothing for it to take away."""
    fake.add_issue(1, body="the build_mask helper is wrong")
    fake.add_issue(2, body="unrelated entirely")
    _index(engine, fake, 1, 2)
    backend = open_backend(engine)
    query = _query(text="build_mask helper")

    strict = backend.search(query)
    hits, widened = search_best(backend, query)

    assert strict
    assert [(hit.repo, hit.number) for hit in hits] == [(hit.repo, hit.number) for hit in strict]
    assert widened == ""


def test_widening_cannot_admit_a_thread_the_filters_excluded(
    engine: Engine, fake: FakeGitHub
) -> None:
    """It widens the *text* and nothing else. A filter is scope the caller chose, and a
    fallback that quietly dropped one would answer a question nobody asked."""
    fake.add_issue(1, body="static cache fullgraph override on the compile path")
    _index(engine, fake, 1)
    backend = open_backend(engine)

    hits, widened = search_best(backend, _query(text=PROSE, files=("src/absent.py",)))

    assert hits == []
    assert widened == WIDENED_ANY_TERM


# -- the fallback -----------------------------------------------------------


def test_prose_that_ands_to_nothing_still_answers(engine: Engine, fake: FakeGitHub) -> None:
    """The measured case. No document carries all seven terms; one carries most of them,
    and before this it was unreachable behind an exit-0 empty page."""
    fake.add_issue(1, body="static cache is not compatible with fullgraph compile here")
    fake.add_issue(2, body="a thread about tokenizers and nothing else")
    _index(engine, fake, 1, 2)
    backend = open_backend(engine)

    assert backend.search(_query(text=PROSE)) == []
    hits, widened = search_best(backend, _query(text=PROSE))

    assert widened == WIDENED_ANY_TERM
    assert [hit.number for hit in hits][:1] == [1]


def test_a_widened_page_that_is_also_empty_still_says_it_widened(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The more useful of the two answers: the caller's words are not the problem, so the
    next turn should not be another reformulation of them."""
    fake.add_issue(1, body="a thread about tokenizers and nothing else")
    _index(engine, fake, 1)
    backend = open_backend(engine)

    hits, widened = search_best(backend, _query(text="fullgraph fa2 staticcache"))

    assert hits == []
    assert widened == WIDENED_ANY_TERM


def test_one_term_is_not_widened(engine: Engine, fake: FakeGitHub) -> None:
    """A single term already *is* its own disjunction. Widening it would return the same
    page under a name that claims something happened."""
    fake.add_issue(1, body="a thread about tokenizers and nothing else")
    _index(engine, fake, 1)
    backend = open_backend(engine)

    assert MIN_WIDEN_TERMS == 2
    assert search_best(backend, _query(text="fullgraph")) == ([], "")


def test_a_query_with_no_text_is_not_widened(engine: Engine, fake: FakeGitHub) -> None:
    """A filter-only call has no terms to disjoin, and its empty page means what it says."""
    fake.add_issue(1, body="a thread about tokenizers")
    _index(engine, fake, 1)
    backend = open_backend(engine)

    assert search_best(backend, _query(files=("src/absent.py",))) == ([], "")


def test_an_empty_repo_scope_stays_empty_through_the_widening(engine: Engine) -> None:
    """Section 11 fails closed. The fallback is a second chance to lose that, and does not."""
    hits, widened = search_best(open_backend(engine), SearchQuery(repos=(), text=PROSE))

    assert hits == []
    # It still reports what it tried: an out-of-scope page and a quiet corpus look alike,
    # and the `repos` echo is what tells them apart (`render._filters_applied`).
    assert widened == WIDENED_ANY_TERM


def test_widening_is_a_superset_of_the_strict_page(engine: Engine, fake: FakeGitHub) -> None:
    """Asked directly rather than through the fallback: the disjunction admits every row the
    conjunction did. Nothing can be lost by widening -- only added."""
    fake.add_issue(1, body="static cache fullgraph compile override generate FA2 everything")
    fake.add_issue(2, body="static cache only")
    _index(engine, fake, 1, 2)
    backend = open_backend(engine)

    strict = {hit.number for hit in backend.search(_query(text="static cache"))}
    wide = {hit.number for hit in backend.search(_query(text="static cache", match=MATCH_ANY))}

    assert strict
    assert strict <= wide


# -- ordering, where it is ours rather than the engine's --------------------


def test_the_widened_page_leads_with_coverage_not_density(engine: Engine, fake: FakeGitHub) -> None:
    """Postgres only, and it is the bug this was written twice to avoid (section 10.6).

    ``ts_rank_cd`` measures how densely the matched lexemes sit, so a document repeating one
    term outranks a document carrying most of the question once each -- measured on the real
    corpus, the six-of-seven document came *sixth*. FTS5 has no equivalent expression and is
    ordered by ``bm25``, which section 4.1 already refuses to compare.
    """
    if engine.dialect.name != "postgresql":
        pytest.skip("coverage-before-density is the Postgres ranking (section 4.1)")
    # Six of the seven terms once each, against one term sixty times. Nothing carries all
    # seven, so the strict page is empty and the widening decides the order.
    fake.add_issue(1, body="static cache fullgraph compile generate FA2")
    fake.add_issue(2, body=" ".join(["cache"] * 60))
    _index(engine, fake, 1, 2)
    backend = open_backend(engine)

    hits, widened = search_best(backend, _query(text=PROSE))

    assert widened == WIDENED_ANY_TERM
    assert [hit.number for hit in hits][0] == 1

"""``sort`` -- how a page is ordered, on both dialects (section 12).

Distinct from section 6's decay, and the distinction is the whole design. Decay folds age
into the *score* and can demote a correct old answer; ``sort`` leaves every score alone and
reorders what relevance already chose. So the load-bearing test here is the second one:
section 10.6 is the record of what happens when recency is allowed to decide *selection*
rather than presentation -- each thread contributes its newest document, which on a merged
pull request is the approving review, and a real query came back nine-tenths ``LGTM``.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine, update

from relore.ingest.index_thread import index_thread
from relore.search import QueryError, RankSpec, SearchQuery, open_backend
from relore.store import schema as s

REPO = "owner/name"
ALPHA = "src/alpha.py"
BETA = "src/beta.py"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _index(engine: Engine, fake: FakeGitHub, *numbers: int) -> None:
    with fake.client() as client:
        for number in numbers:
            index_thread(engine, client, fake.repo, number)


def _threads(hits: list) -> list[int]:
    """The thread numbers in page order, one entry each.

    A page is documents and a thread may hold several slots, so the first appearance of
    each thread is what carries the ordering claim.
    """
    out: list[int] = []
    for hit in hits:
        if hit.number not in out:
            out.append(hit.number)
    return out


def _score_and_date_disagree(fake: FakeGitHub) -> None:
    """The only corpus that can tell the two orders apart.

    Thread 1 carries **both** files, so it outscores thread 2 on section 6's file overlap;
    thread 2 is four years newer. Equal scores would prove nothing -- ``_order``'s existing
    tie-break is already ``github_created_at DESC``, so a tie sorts the same either way.
    """
    older = fake.add_pr(1, title="older but better", body="a body")
    fake.add_review_comment(
        older, 300, "the mask is built here", path=ALPHA, created_at="2021-01-01T00:00:00Z"
    )
    fake.add_review_comment(
        older, 301, "and consumed here", path=BETA, created_at="2021-01-01T00:00:00Z"
    )

    newer = fake.add_pr(2, title="newer but thinner", body="a body")
    fake.add_review_comment(
        newer, 400, "the mask is built here", path=ALPHA, created_at="2025-01-01T00:00:00Z"
    )


def _query(**over: object) -> SearchQuery:
    return SearchQuery(repos=(REPO,), text="", files=(ALPHA,), **over)  # type: ignore[arg-type]


def test_relevance_leads_with_the_older_better_answer(engine: Engine, fake: FakeGitHub) -> None:
    """The control. Without it, the test below cannot show that ``sort`` did anything."""
    _score_and_date_disagree(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(_query(), rank_as=RankSpec(files=(ALPHA, BETA)))

    assert _threads(hits) == [1, 2]


def test_newest_reverses_it(engine: Engine, fake: FakeGitHub) -> None:
    _score_and_date_disagree(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(_query(sort="newest"), rank_as=RankSpec(files=(ALPHA, BETA)))

    assert _threads(hits) == [2, 1]


def test_sorting_by_date_does_not_change_which_documents_are_on_the_page(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Section 10.6's lesson, asserted. ``newest`` must stay out of the two ``row_number()``
    windows that choose a thread's representative document: selection is relevance's job.
    Were it otherwise, each thread would be represented by its *last* comment -- so this
    compares the identity of every hit, not merely how many came back.
    """
    _score_and_date_disagree(fake)
    _index(engine, fake, 1, 2)
    backend = open_backend(engine)
    spec = RankSpec(files=(ALPHA, BETA))

    by_score = backend.search(_query(), rank_as=spec)
    by_date = backend.search(_query(sort="newest"), rank_as=spec)

    identity = lambda hit: (hit.number, hit.source_type, hit.snippet, hit.score)  # noqa: E731
    assert sorted(map(identity, by_date)) == sorted(map(identity, by_score))
    assert [identity(h) for h in by_date] != [identity(h) for h in by_score]


def test_scores_are_untouched_by_the_order(engine: Engine, fake: FakeGitHub) -> None:
    """``sort`` is not decay: the older thread still scores higher, it is just shown second.
    A date sort that also moved the numbers would be decay with no half-life."""
    _score_and_date_disagree(fake)
    _index(engine, fake, 1, 2)

    hits = open_backend(engine).search(_query(sort="newest"), rank_as=RankSpec(files=(ALPHA, BETA)))

    first, second = _threads(hits)
    assert first == 2 and second == 1
    best = {hit.number: hit.score for hit in hits}
    assert best[1] > best[2]


def test_a_document_with_no_timestamp_does_not_lead_a_date_sort(
    engine: Engine, fake: FakeGitHub
) -> None:
    """``nulls_last``, and it is a dialect divergence rather than a nicety: on ``DESC``
    Postgres puts NULL first and SQLite puts it last. As ``_order``'s tie-break that was
    invisible; as the leading key it decides the page, so a row with no date would open
    the results on Postgres and not on SQLite.
    """
    _score_and_date_disagree(fake)
    _index(engine, fake, 1, 2)
    with engine.begin() as conn:
        conn.execute(
            update(s.documents)
            .where(s.documents.c.source_id == "400")
            .values(github_created_at=None)
        )

    hits = open_backend(engine).search(_query(sort="newest"), rank_as=RankSpec(files=(ALPHA, BETA)))

    assert hits, "the undated row must still be returned, only not first"
    assert hits[0].created_at is not None
    assert [hit.created_at for hit in hits].count(None) == 1


@pytest.mark.parametrize("bad", ["date", "newest first", "", "NEWEST"])
def test_an_unknown_sort_is_refused(bad: str) -> None:
    """Rejected, not clamped. A caller who asks for an order this cannot give and gets the
    default silently has no way to tell that from an index whose dates are all equal --
    unlike ``limit``, where clamping is the documented kindness (section 6)."""
    with pytest.raises(QueryError, match="unknown sort"):
        SearchQuery(repos=(REPO,), sort=bad)

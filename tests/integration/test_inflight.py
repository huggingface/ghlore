"""``inflight`` -- is somebody already fixing this? On both dialects (section 12).

The measured failure this answers: an agent read an issue thread, found two comments and
neither linked a fix, and was about to write a patch. An open pull request eight hours old
had `Fixes #48630` in its body. It was found by accident, through a symbol search whose
term happened to be in the issue title -- the two titles share almost no vocabulary, so
nothing built on similarity would have found it.

So the assertions here are about the *edge*, not about ranking: it is derivable from one
thread alone, it survives a target this index has never seen, and re-deriving writes
nothing.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub, FakeGraphQL
from sqlalchemy import Engine, func, select

from relore.ingest.backfill import backfill
from relore.ingest.index_thread import derive_thread, index_thread
from relore.search import open_backend
from relore.store import schema as s

REPO = "owner/name"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _index(engine: Engine, fake: FakeGitHub, *numbers: int) -> None:
    with fake.client() as client:
        for number in numbers:
            index_thread(engine, client, fake.repo, number)


def _link_rows(engine: Engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                select(
                    s.thread_links.c.relationship,
                    s.thread_links.c.target_number,
                    s.thread_links.c.target_thread_id,
                ).order_by(s.thread_links.c.target_number)
            )
        ]


def test_an_open_pull_request_claiming_to_fix_an_issue_is_found(
    engine: Engine, fake: FakeGitHub
) -> None:
    fake.add_issue(1, title="crashes for any rotary_pct != 1.0")
    fake.add_pr(2, title="fix: respect partial_rotary_factor", body="Fixes #1")
    _index(engine, fake, 1, 2)

    view = open_backend(engine).inflight(REPO, 1)

    assert [(c.number, c.state, c.relationship) for c in view.claims] == [(2, "open", "closes")]
    assert view.total == 1
    assert view.claims[0].merged is False


def test_a_claim_on_a_thread_this_index_has_never_seen_still_answers(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The claim is a fact about the *source* thread, so it does not wait for its target.

    The plan's two-ended foreign key made an edge unstorable until the target was indexed,
    which made a corpus-wide pass a prerequisite for the query -- and on a sampled index
    the target is routinely outside the window.
    """
    fake.add_pr(2, body="Fixes #999")
    _index(engine, fake, 2)

    assert _link_rows(engine) == [("closes", 999, None)]
    assert [c.number for c in open_backend(engine).inflight(REPO, 999).claims] == [2]


def test_the_target_is_resolved_once_it_is_indexed(engine: Engine, fake: FakeGitHub) -> None:
    fake.add_pr(2, body="Fixes #1")
    _index(engine, fake, 2)
    assert _link_rows(engine) == [("closes", 1, None)]

    fake.add_issue(1)
    _index(engine, fake, 1)
    with engine.begin() as conn:
        derive_thread(conn, REPO, 2)  # the poll re-derives; the resolution follows

    (relationship, target, resolved), *rest = _link_rows(engine)
    assert (relationship, target) == ("closes", 1) and rest == []
    assert resolved is not None


def test_re_deriving_an_unchanged_thread_writes_no_link_rows(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Section 5.1's idempotency, which the signal reconcile already guarantees -- asserted
    for links because a poll runs it on every pass."""
    fake.add_issue(1)
    fake.add_pr(2, body="Fixes #1")
    _index(engine, fake, 1, 2)

    with engine.begin() as conn:
        again = derive_thread(conn, REPO, 2)

    assert again.signals.rewritten == {}
    assert len(_link_rows(engine)) == 1


def test_an_open_claim_is_ranked_before_a_merged_one(engine: Engine, fake: FakeGitHub) -> None:
    """A stale draft, an approved pull request and a merged one imply different next
    actions, so the state is on every row and open leads."""
    fake.add_issue(1)
    fake.add_pr(
        2, body="Fixes #1", updated_at="2026-02-01T00:00:00Z", merged_at="2026-02-02T00:00:00Z"
    )
    fake.add_pr(3, body="Fixes #1", updated_at="2026-03-01T00:00:00Z")
    _index(engine, fake, 1, 2, 3)

    claims = open_backend(engine).inflight(REPO, 1).claims

    assert [c.number for c in claims] == [3, 2]
    assert claims[1].merged is True


def test_nothing_claiming_it_is_an_empty_answer_not_an_error(
    engine: Engine, fake: FakeGitHub
) -> None:
    fake.add_issue(1)
    fake.add_pr(2, body="no keyword here")
    _index(engine, fake, 1, 2)

    view = open_backend(engine).inflight(REPO, 1)

    assert view.claims == ()
    # Zero *rows in the repository* is what tells the caller the question was answerable.
    assert view.links_indexed == 0


def test_an_index_with_no_relationship_rows_is_distinguishable_from_a_clean_answer(
    engine: Engine, fake: FakeGitHub
) -> None:
    fake.add_issue(1)
    fake.add_pr(2, body="Fixes #1")
    fake.add_pr(3, body="Fixes #1")
    _index(engine, fake, 1, 2, 3)

    assert open_backend(engine).inflight(REPO, 1).links_indexed == 2
    with engine.begin() as conn:
        conn.execute(s.thread_links.delete())
    assert open_backend(engine).inflight(REPO, 1).links_indexed == 0


def test_githubs_own_resolved_reference_is_indexed_by_the_per_pr_pass(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The definitive source. It reaches only merged pull requests (section 3), which is
    why the body is read as well."""
    fake.add_issue(1)
    pr = fake.add_pr(2, body="no keyword", merged_at="2026-02-02T00:00:00Z")
    pr.closing_references = [1]
    gql = FakeGraphQL(fake).client()
    with fake.client() as client:
        try:
            backfill(engine, client, REPO, gql=gql)
        finally:
            gql.close()

    assert [c.number for c in open_backend(engine).inflight(REPO, 1).claims] == [2]


def test_the_link_count_is_per_repository(engine: Engine, fake: FakeGitHub) -> None:
    fake.add_pr(2, body="Fixes #1")
    _index(engine, fake, 2)
    other = FakeGitHub("owner/other")
    other.add_pr(5, body="Fixes #4")
    with other.client() as client:
        index_thread(engine, client, other.repo, 5)

    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(s.thread_links)).scalar_one() == 2
    assert open_backend(engine).inflight("owner/other", 1).claims == ()

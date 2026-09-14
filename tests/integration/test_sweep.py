"""The existence sweep -- section 5.2's residual gap.

The poll cannot see a thread whose *only* change was a deletion and which then goes
quiet: whether a deletion bumps ``updated_at`` is a GitHub detail this design does not
bet on. So the sweep reconciles existence instead of content.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine, select

from relore.ingest.poll import poll_once
from relore.ingest.sweep import SWEEP_PASS, sweep
from relore.store import schema as s

REPO = "owner/name"


@pytest.fixture
def fake() -> FakeGitHub:
    """An old, quiet thread plus a much newer one.

    The newer thread is what makes this a real test: it pushes the poll's high-water mark
    months past PR #1, so #1 falls outside the 60-second overlap window and a poll
    genuinely cannot re-read it. Without it the overlap alone would re-read #1, and the
    poll would look able to see deletions it cannot see in production.
    """
    fake = FakeGitHub(REPO)
    pr = fake.add_pr(1, updated_at="2026-01-01T00:00:00Z")
    fake.add_comment(pr, 100, "a comment", created_at="2026-01-01T00:00:00Z")
    fake.add_comment(pr, 101, "another", created_at="2026-01-01T00:00:00Z")
    fake.add_review_comment(pr, 300, "inline", created_at="2026-01-01T00:00:00Z")
    fake.add_issue(2, updated_at="2026-06-01T00:00:00Z")
    return fake


def _docs(engine: Engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        return {
            (r.source_type, r.source_id)
            for r in conn.execute(select(s.documents.c.source_type, s.documents.c.source_id))
        }


def test_a_deletion_that_never_bumped_updated_at_is_still_reconciled(
    engine: Engine, fake: FakeGitHub
) -> None:
    with fake.client() as client:
        poll_once(engine, client, REPO)
    assert ("issue_comment", "100") in _docs(engine)

    # Deleted, with the thread's updated_at deliberately untouched. #1 is months behind
    # the high-water mark now, so neither of the poll's list queries will surface it.
    fake.threads[1].issue_comments = [c for c in fake.threads[1].issue_comments if c["id"] != 100]
    with fake.client() as client:
        after_poll = poll_once(engine, client, REPO)
    assert after_poll.documents_written == 0, "the poll cannot see it -- that is the gap"

    with fake.client() as client:
        result = sweep(engine, client, REPO)

    assert result.removed["issue_comment"] == 1
    assert result.rederived == 1
    assert ("issue_comment", "100") not in _docs(engine)
    assert ("issue_comment", "101") in _docs(engine)


def test_a_deleted_review_comment_is_reconciled_too(engine: Engine, fake: FakeGitHub) -> None:
    with fake.client() as client:
        poll_once(engine, client, REPO)
    fake.threads[1].review_comments = []
    with fake.client() as client:
        assert poll_once(engine, client, REPO).documents_written == 0

    with fake.client() as client:
        result = sweep(engine, client, REPO)

    assert result.removed["review_comment"] == 1
    assert ("review_comment", "300") not in _docs(engine)


def test_a_sweep_with_nothing_deleted_changes_nothing(engine: Engine, fake: FakeGitHub) -> None:
    with fake.client() as client:
        poll_once(engine, client, REPO)
    before = _docs(engine)

    with fake.client() as client:
        result = sweep(engine, client, REPO)

    assert result.total_removed == 0
    assert result.documents_written == 0
    assert _docs(engine) == before


def test_the_sweep_records_its_own_pass(engine: Engine, fake: FakeGitHub) -> None:
    """Its own row, on its own cadence -- weekly is ample, and it must not touch the
    poll's checkpoint."""
    with fake.client() as client:
        poll_once(engine, client, REPO)
        sweep(engine, client, REPO)
    with engine.connect() as conn:
        rows = {getattr(r, "pass"): r for r in conn.execute(select(s.sync_state))}
    assert rows[SWEEP_PASS].last_ok_at is not None
    assert rows[SWEEP_PASS].high_water is None, "a sweep is not an incremental pass"

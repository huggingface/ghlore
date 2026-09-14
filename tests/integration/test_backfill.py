"""Section 3's pass structure, and milestone 1's done-when: a full history indexed, a
re-run that mutates zero documents, and an interrupted run that resumes."""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub, FakeGraphQL
from sqlalchemy import Engine, func, select

from relore.ingest.backfill import DERIVE_PASS, FILES_PASS, backfill
from relore.store import repository as repo_layer
from relore.store import schema as s

REPO = "owner/name"


@pytest.fixture
def fake() -> FakeGitHub:
    fake = FakeGitHub(REPO)
    issue = fake.add_issue(1, title="a bug", body="it crashes", updated_at="2026-01-01T00:00:00Z")
    fake.add_comment(issue, 100, "me too", created_at="2026-01-02T00:00:00Z")
    pr = fake.add_pr(
        2, title="fix it", updated_at="2026-02-01T00:00:00Z", merged_at="2026-02-02T00:00:00Z"
    )
    pr.files = ["src/mod.py"]
    fake.add_review(pr, 200, "please move this")
    fake.add_review_comment(pr, 300, "belongs in the parent", created_at="2026-02-03T00:00:00Z")
    return fake


def _run(engine: Engine, fake: FakeGitHub, *, graphql: bool = False, **kwargs):
    gql = FakeGraphQL(fake).client() if graphql else None
    with fake.client() as client:
        try:
            return backfill(engine, client, REPO, gql=gql, **kwargs)
        finally:
            if gql:
                gql.close()


def _docs(engine: Engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        return {
            (r.source_type, r.source_id)
            for r in conn.execute(select(s.documents.c.source_type, s.documents.c.source_id))
        }


def test_a_backfill_indexes_the_whole_history_from_bulk_walks(
    engine: Engine, fake: FakeGitHub
) -> None:
    """No per-thread fetches: three repo-wide walks, then derive."""
    result = _run(engine, fake)

    assert {p.name for p in result.passes} >= {"threads", "issue_comments", "pr_comments"}
    assert result.staged == 4  # 2 threads + 1 conversation comment + 1 review comment
    assert result.derived == 2
    assert _docs(engine) == {
        ("title", "1"),
        ("body", "1"),
        ("issue_comment", "100"),
        ("title", "2"),
        ("body", "2"),
        ("review_comment", "300"),
    }
    # The bulk walk never calls GET /repos/x/issues/1 -- that is the whole point.
    assert not any(p.endswith("/issues/1") for p in fake.requests)


def test_rerunning_a_backfill_mutates_zero_documents(engine: Engine, fake: FakeGitHub) -> None:
    """Milestone 1's done-when. The derive cursor is cleared on completion, so this
    really re-derives everything rather than skipping it."""
    _run(engine, fake)
    second = _run(engine, fake)

    assert second.derived == 2, "a re-run must actually redo the work"
    assert second.documents_written == 0


def test_an_interrupted_derive_resumes(engine: Engine, fake: FakeGitHub) -> None:
    _run(engine, fake, derive=False)
    with engine.begin() as conn:
        repo_layer.set_cursor(conn, REPO, DERIVE_PASS, "1")  # as if killed after thread 1

    result = _run(engine, fake)

    assert result.derived == 1, "only the threads after the cursor"
    assert ("title", "2") in _docs(engine)
    with engine.connect() as conn:
        assert repo_layer.get_cursor(conn, REPO, DERIVE_PASS) is None, "cleared on completion"


def test_each_pass_checkpoints_independently(engine: Engine, fake: FakeGitHub) -> None:
    """Section 5.2 rule 4: a stalled pass must not block another."""
    _run(engine, fake, derive=False)
    with engine.connect() as conn:
        marks = {getattr(row, "pass"): row.high_water for row in conn.execute(select(s.sync_state))}
    assert marks["threads"] is not None
    assert marks["issue_comments"] is not None
    assert marks["pr_comments"] is not None
    assert marks["threads"] != marks["pr_comments"]


def test_the_thread_walk_shares_its_mark_with_the_poll(engine: Engine, fake: FakeGitHub) -> None:
    """They are the same logical pass, so a finished backfill hands over with no gap."""
    from relore.ingest.poll import PASS

    _run(engine, fake, derive=False)
    with engine.connect() as conn:
        assert repo_layer.high_water(conn, REPO, PASS) is not None


def test_only_runs_the_named_pass(engine: Engine, fake: FakeGitHub) -> None:
    result = _run(engine, fake, only=("threads",))
    by_name = {p.name: p for p in result.passes}
    assert by_name["issue_comments"].skipped
    assert not by_name["threads"].skipped
    assert _docs(engine) == set()  # derive was not requested


def test_comments_staged_before_their_thread_are_skipped_not_fatal(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The thread walk can be interrupted after the comment walk has run."""
    _run(engine, fake, only=("issue_comments",), derive=False)
    result = _run(engine, fake, only=(DERIVE_PASS,))
    assert result.derived == 0
    assert _docs(engine) == set()


def test_the_per_pr_pass_supplies_review_bodies(engine: Engine, fake: FakeGitHub) -> None:
    """There is no repo-wide reviews endpoint, so on this path GraphQL is the only source.

    Without it a backfilled corpus has no review submissions at all -- and review threads
    are where the design decisions worth retrieving actually live.
    """
    without = _run(engine, fake)
    assert ("review", "200") not in _docs(engine), "REST walks cannot see review bodies"
    assert next(p for p in without.passes if p.name == FILES_PASS).skipped

    _run(engine, fake, graphql=True)

    assert ("review", "200") in _docs(engine)
    with engine.connect() as conn:
        row = conn.execute(
            select(s.documents.c.body_text, s.documents.c.trust, s.documents.c.commit_sha).where(
                s.documents.c.source_type == "review"
            )
        ).one()
    assert row.body_text == "please move this"
    assert row.trust == "authoritative"  # COLLABORATOR, via GraphQL authorAssociation
    assert row.commit_sha == "deadbeef"


def test_the_per_pr_pass_fills_in_merge_metadata(engine: Engine, fake: FakeGitHub) -> None:
    _run(engine, fake, graphql=True)
    with engine.connect() as conn:
        row = conn.execute(
            select(s.threads.c.merged_at, s.threads.c.metadata).where(
                s.threads.c.github_number == 2
            )
        ).one()
    assert row.merged_at is not None
    assert row.metadata["merged_by"] == "maintainer"
    assert row.metadata["changed_files"] == 1


def test_the_per_pr_pass_visits_only_merged_prs(engine: Engine, fake: FakeGitHub) -> None:
    """An unmerged PR's file list is not evidence of anything that shipped (section 3)."""
    unmerged = fake.add_pr(3, updated_at="2026-03-01T00:00:00Z", merged_at=None)
    fake.add_review(unmerged, 201, "not merged, not precedent")

    _run(engine, fake, graphql=True)

    assert ("review", "201") not in _docs(engine)
    with engine.connect() as conn:
        staged = (
            conn.execute(
                select(s.raw_objects.c.object_id).where(s.raw_objects.c.object_type == "pr_details")
            )
            .scalars()
            .all()
        )
    assert staged == ["2"]


def test_the_per_pr_pass_resumes_from_its_cursor(engine: Engine, fake: FakeGitHub) -> None:
    """The cursor alone, with nothing staged: PR 2 is before it and must stay unvisited."""
    fake.add_pr(4, updated_at="2026-04-01T00:00:00Z", merged_at="2026-04-02T00:00:00Z")
    _run(engine, fake, graphql=True)
    with engine.begin() as conn:
        # An interrupted first pass is the case this cursor exists for, so nothing is
        # staged yet -- otherwise the skip below would decide the outcome, not the cursor.
        conn.execute(s.raw_objects.delete().where(s.raw_objects.c.object_type == "pr_details"))
        repo_layer.set_cursor(conn, REPO, FILES_PASS, "2")

    result = _run(engine, fake, graphql=True)

    files = next(p for p in result.passes if p.name == FILES_PASS)
    assert files.staged == 1, "only PR 4 is after the cursor"
    with engine.connect() as conn:
        assert repo_layer.get_cursor(conn, REPO, FILES_PASS) is None


def test_the_per_pr_pass_costs_only_the_prs_it_has_not_staged(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Re-running it walked every merged PR again -- 20,429 on `transformers`, hours of
    GraphQL points, to collect the handful the poll had found since. The poller stages no
    detail, so that gap is real and has to be cheap enough to close on a timer."""
    _run(engine, fake, graphql=True)
    fake.add_pr(4, updated_at="2026-04-01T00:00:00Z", merged_at="2026-04-02T00:00:00Z")

    result = _run(engine, fake, graphql=True)

    files = next(p for p in result.passes if p.name == FILES_PASS)
    assert files.staged == 1, "PR 2's detail is already staged; only PR 4 is new"

    # And the escape hatch, for when the extraction changed rather than the corpus.
    again = _run(engine, fake, graphql=True, refresh_details=True)
    assert next(p for p in again.passes if p.name == FILES_PASS).staged == 2


def test_review_bodies_are_not_duplicated_when_both_sources_have_them(
    engine: Engine, fake: FakeGitHub
) -> None:
    """A poll stages REST reviews; a later backfill stages the GraphQL node. One document."""
    from relore.ingest.index_thread import index_thread

    with fake.client() as client:
        index_thread(engine, client, REPO, 2)
    _run(engine, fake, graphql=True)

    with engine.connect() as conn:
        count = conn.execute(
            select(func.count())
            .select_from(s.documents)
            .where(s.documents.c.source_type == "review", s.documents.c.source_id == "200")
        ).scalar_one()
    assert count == 1

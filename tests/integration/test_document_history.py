"""Section 14.1, settled 2026-09-09: keep versions.

``index_thread`` is otherwise last-write-wins, and people materially rewrite issue bodies
-- the motivating deployment's per-run triage issues are refreshed in place, so their
current body is not a record of what was dispatched. An append-only ``documents_history``
is cheap now and **impossible to reconstruct later**, which is why section 15.1 makes it a
decision that has to precede the first production backfill rather than a feature.

Four properties, and each is a way the capture could quietly be wrong: it records the
previous text, it records it *only* when the text changed, it never breaks section 12's
zero-write assertion, and it is never served. All of them run on both dialects.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine, func, select

from relore.ingest.index_thread import index_thread
from relore.store import schema as s

REPO = "owner/name"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _index(engine: Engine, fake: FakeGitHub, number: int = 1):
    with fake.client() as client:
        return index_thread(engine, client, REPO, number)


def _history(engine: Engine) -> list[tuple[str, str, str]]:
    with engine.connect() as conn:
        return [
            (row.source_type, row.body_text, row.reason)
            for row in conn.execute(
                select(
                    s.documents_history.c.source_type,
                    s.documents_history.c.body_text,
                    s.documents_history.c.reason,
                ).order_by(s.documents_history.c.id)
            )
        ]


def _count(engine: Engine, table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


# -- what it keeps ---------------------------------------------------------


def test_an_edited_comment_keeps_what_it_used_to_say(engine: Engine, fake: FakeGitHub) -> None:
    """The case section 14.1 was written for. Without this the previous text is gone the
    moment a poll sees the new one, and no later migration can recover it."""
    pr = fake.add_pr(1)
    comment = fake.add_comment(pr, 100, "the cache is cropped before the mask is built")
    _index(engine, fake)

    comment["body"] = "actually the mask is built first"
    _index(engine, fake)

    assert _history(engine) == [
        ("issue_comment", "the cache is cropped before the mask is built", "edited")
    ]


def test_a_deleted_comment_keeps_what_it_said(engine: Engine, fake: FakeGitHub) -> None:
    """Section 5.1's fourth row -- absent remotely, present locally -- is the *other* way a
    document's text stops existing, and it is the one nobody thinks of."""
    pr = fake.add_pr(1)
    fake.add_comment(pr, 100, "a claim somebody thought better of")
    _index(engine, fake)

    pr.issue_comments.clear()
    _index(engine, fake)

    assert _history(engine) == [("issue_comment", "a claim somebody thought better of", "deleted")]
    # ...and the live document really is gone, so the history is the only copy.
    assert _count(engine, s.documents) == 2  # title + body


def test_successive_edits_each_leave_a_version(engine: Engine, fake: FakeGitHub) -> None:
    """Append-only: nothing here is ever updated, so a comment rewritten twice leaves two
    rows in the order they were superseded."""
    pr = fake.add_pr(1)
    comment = fake.add_comment(pr, 100, "first")
    _index(engine, fake)
    comment["body"] = "second"
    _index(engine, fake)
    comment["body"] = "third"
    _index(engine, fake)

    assert [(text, reason) for _t, text, reason in _history(engine)] == [
        ("first", "edited"),
        ("second", "edited"),
    ]


def test_an_edited_issue_body_is_kept_too(engine: Engine, fake: FakeGitHub) -> None:
    """The concrete case named in section 14.1: a triage issue refreshed in place, whose
    current body is not a record of what was dispatched."""
    issue = fake.add_issue(1, body="dispatched: model-a, model-b")
    _index(engine, fake)

    issue.payload["body"] = "dispatched: model-c"
    _index(engine, fake)

    assert ("body", "dispatched: model-a, model-b", "edited") in _history(engine)


# -- what it must not do ---------------------------------------------------


def test_reindexing_an_unchanged_thread_writes_no_history(engine: Engine, fake: FakeGitHub) -> None:
    """Section 12's load-bearing assertion says re-indexing an unchanged thread performs
    **zero** document writes, and a history row is a document write. If this goes red the
    incremental story is gone and the history table is where it went."""
    pr = fake.add_pr(1)
    fake.add_comment(pr, 100, "a conversation comment")
    fake.add_review_comment(pr, 300, "this belongs in the parent class")
    _index(engine, fake)

    _index(engine, fake)
    _index(engine, fake)

    assert _count(engine, s.documents_history) == 0


def test_a_trust_promotion_is_not_an_edit(engine: Engine, fake: FakeGitHub) -> None:
    """Section 5.1: ``trust`` is a DERIVED_COLUMN whose input lives outside the payload, so
    resolving an author rewrites the row **though no byte of text changed**. Recording that
    as a version would fill the history with rows in which nothing the author wrote is
    different -- and would do it once per author, on a pass that touches every thread."""
    pr = fake.add_pr(1)
    fake.add_comment(pr, 100, "unchanged text", author="someone", assoc="MEMBER")
    _index(engine, fake)

    with engine.begin() as conn:
        conn.execute(
            s.documents.update()
            .where(s.documents.c.source_type == "issue_comment")
            .values(trust="reported")
        )
    _index(engine, fake)  # re-derives; the tier moves back, the text does not

    assert _count(engine, s.documents_history) == 0


def test_the_history_is_not_searchable(engine: Engine, fake: FakeGitHub) -> None:
    """Nothing retrieves a superseded document, and that is a decision rather than an
    omission (see ``repository.keep_versions``). A claim the author withdrew, served as
    current, would be worse than not having kept it -- section 6.2 is about what is
    *served*, and the history has no tier, no age and no envelope of its own.
    """
    from relore.search import SearchQuery, open_backend

    pr = fake.add_pr(1)
    comment = fake.add_comment(pr, 100, "flibbertigibbet is the distinctive word")
    _index(engine, fake)
    comment["body"] = "something else entirely"
    _index(engine, fake)

    assert _count(engine, s.documents_history) == 1
    hits = open_backend(engine).search(SearchQuery(repos=(REPO,), text="flibbertigibbet"))
    assert hits == []


def test_status_counts_what_was_kept(engine: Engine, fake: FakeGitHub) -> None:
    """Nothing searches the table, so this count is the only way to see the capture works.
    Zero after a long-running poll is a symptom, not a quiet corpus."""
    from relore.store.repository import index_summary

    pr = fake.add_pr(1)
    comment = fake.add_comment(pr, 100, "before")
    _index(engine, fake)
    comment["body"] = "after"
    _index(engine, fake)

    with engine.connect() as conn:
        assert index_summary(conn)["superseded_documents"] == 1

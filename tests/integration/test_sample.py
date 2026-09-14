"""``relored sample`` -- section 10's corpus: bounded, and every thread complete.

Runs on both dialects. The load-bearing tests here are
:func:`test_a_sample_does_not_advance_the_thread_walks_mark` and
:func:`test_a_selected_thread_is_indexed_whole`: the first is what keeps a later full
backfill possible, and the second is the reason a sample is not just a `backfill --since`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine, select

from relore.ingest.poll import poll_once
from relore.ingest.sample import pass_name, sample
from relore.store import repository as repo_layer
from relore.store import schema as s

REPO = "owner/name"
WINDOW = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _sample(engine: Engine, fake: FakeGitHub, **kwargs):
    with fake.client() as client:
        return sample(engine, client, REPO, since=WINDOW, **kwargs)


def _numbers(engine: Engine) -> set[int]:
    with engine.connect() as conn:
        return {int(n) for (n,) in conn.execute(select(s.threads.c.github_number))}


# -- what lands in the sample ----------------------------------------------


def test_the_window_bounds_the_sample(engine: Engine, fake: FakeGitHub) -> None:
    fake.add_issue(1, updated_at="2026-01-15T00:00:00Z")
    fake.add_issue(2, updated_at="2026-07-15T00:00:00Z")

    result = _sample(engine, fake)

    assert _numbers(engine) == {2}
    assert (result.selected, result.indexed) == (1, 1)


def test_only_the_requested_kind_is_indexed(engine: Engine, fake: FakeGitHub) -> None:
    """The listing walk returns both kinds and costs the same either way, so ``seen``
    counts everything and ``selected`` counts the sample."""
    fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")
    fake.add_pr(2, updated_at="2026-07-01T00:00:00Z", merged_at="2026-07-02T00:00:00Z")

    result = _sample(engine, fake, thread_type="issue")

    assert _numbers(engine) == {1}
    assert (result.seen, result.selected) == (2, 1)


def test_merged_only_skips_a_pull_request_that_never_landed(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Read from the list payload's nested ``pull_request`` block: it is the only place
    ``merged_at`` is free, and a request per candidate to discover most are unmerged
    would cost more than the sample."""
    fake.add_pr(1, updated_at="2026-07-01T00:00:00Z", merged_at="2026-07-02T00:00:00Z")
    fake.add_pr(2, updated_at="2026-07-01T00:00:00Z", merged_at=None)

    _sample(engine, fake, thread_type="pr", merged_only=True)

    assert _numbers(engine) == {1}


def test_limit_stops_the_walk(engine: Engine, fake: FakeGitHub) -> None:
    for number in range(1, 6):
        fake.add_issue(number, updated_at=f"2026-07-0{number}T00:00:00Z")

    result = _sample(engine, fake, limit=2)

    assert result.indexed == 2
    assert len(_numbers(engine)) == 2


def test_a_selected_thread_is_indexed_whole(engine: Engine, fake: FakeGitHub) -> None:
    """The reason a sample is not ``backfill --since``. That walks
    ``/issues/comments?since=`` which filters *individual comments*, so this thread would
    arrive without the 2025 comment that explains it -- a hole inside the retrievable
    unit. Fetching the thread reads its whole conversation regardless of the window."""
    issue = fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")
    fake.add_comment(issue, 100, "the old explanation", created_at="2025-02-01T00:00:00Z")
    fake.add_comment(issue, 101, "the recent nudge", created_at="2026-07-01T00:00:00Z")

    _sample(engine, fake)

    with engine.connect() as conn:
        bodies = {
            row.body_text
            for row in conn.execute(select(s.documents.c.body_text, s.documents.c.source_type))
            if row.source_type == "issue_comment"
        }
    assert bodies == {"the old explanation", "the recent nudge"}


# -- the checkpoint --------------------------------------------------------


def test_a_sample_does_not_advance_the_thread_walks_mark(engine: Engine, fake: FakeGitHub) -> None:
    """Its own row in ``sync_state``. The ``threads`` mark is shared by the poll and the
    backfill, so a sample that moved it would tell either one that the years before the
    window had already been covered -- and the history would be unreachable without
    resetting state by hand."""
    fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")

    _sample(engine, fake)

    with engine.connect() as conn:
        assert repo_layer.high_water(conn, REPO, "threads") is None
        assert repo_layer.high_water(conn, REPO, pass_name("issue")) is not None
        assert repo_layer.high_water(conn, REPO, pass_name("pr")) is None


def test_the_full_history_is_still_reachable_after_a_sample(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The consequence of the test above, stated as the thing an operator cares about."""
    fake.add_issue(1, updated_at="2026-01-01T00:00:00Z")
    fake.add_issue(2, updated_at="2026-07-01T00:00:00Z")
    _sample(engine, fake)
    assert _numbers(engine) == {2}

    with fake.client() as client:
        poll_once(engine, client, REPO)

    assert _numbers(engine) == {1, 2}


def test_a_resumed_sample_covers_the_remainder(engine: Engine, fake: FakeGitHub) -> None:
    for number in range(1, 6):
        fake.add_issue(number, updated_at=f"2026-07-0{number}T00:00:00Z")
    _sample(engine, fake, limit=2)

    second = _sample(engine, fake)

    assert _numbers(engine) == {1, 2, 3, 4, 5}
    assert second.indexed <= 4, "the checkpoint means the first two are not all re-fetched"


def test_the_checkpoint_stops_at_the_first_failure(
    engine: Engine, fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 5.2 rule 5. Threads arrive ascending, so advancing past a failure would
    skip it for ever while the pass reported success."""
    for number in (1, 2, 3):
        fake.add_issue(number, updated_at=f"2026-07-0{number}T00:00:00Z")

    import relore.ingest.sample as sample_module

    real = sample_module.fetch_thread

    def flaky(client, repo, number):
        if number == 2:
            raise RuntimeError("boom")
        return real(client, repo, number)

    monkeypatch.setattr(sample_module, "fetch_thread", flaky)
    result = _sample(engine, fake)

    assert result.failed == [2]
    assert not result.clean
    assert _numbers(engine) == {1, 3}, "later threads are still indexed -- only the mark waits"
    with engine.connect() as conn:
        mark = repo_layer.high_water(conn, REPO, pass_name("issue"))
    assert mark is not None
    assert mark < dt.datetime(2026, 7, 2, tzinfo=dt.timezone.utc), "the mark did not pass #2"


def test_re_sampling_a_finished_window_writes_nothing(engine: Engine, fake: FakeGitHub) -> None:
    fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")
    _sample(engine, fake)

    again = _sample(engine, fake)

    assert (again.documents_written, again.signals_written) == (0, 0)


# -- widening --------------------------------------------------------------


def test_widening_the_window_indexes_the_older_ground(engine: Engine, fake: FakeGitHub) -> None:
    """The progress mark records where a run got to *within* a window, so it is only a
    resume point for the same window or a narrower one. Clamping a widened request to it
    would walk from where the last run finished and index nothing -- while reporting
    success, which is the silent failure this module is written against."""
    fake.add_issue(1, updated_at="2026-02-01T00:00:00Z")
    fake.add_issue(2, updated_at="2026-07-01T00:00:00Z")
    _sample(engine, fake)  # since=2026-06-01
    assert _numbers(engine) == {2}

    with fake.client() as client:
        result = sample(engine, client, REPO, since=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))

    assert _numbers(engine) == {1, 2}
    assert result.indexed >= 1


def test_widening_does_not_refetch_what_is_already_current(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The listing's own ``updated_at`` came free with the walk, so a thread already held
    at that timestamp costs no requests. Without this, widening re-fetches every thread
    the narrower run indexed -- two to five requests each, to re-confirm data we have."""
    fake.add_issue(1, updated_at="2026-02-01T00:00:00Z")
    fake.add_issue(2, updated_at="2026-07-01T00:00:00Z")
    _sample(engine, fake)
    requests_before = len(fake.requests)

    with fake.client() as client:
        result = sample(engine, client, REPO, since=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))

    assert (result.indexed, result.skipped) == (1, 1), "#2 was already current"
    fetched = len(fake.requests) - requests_before
    assert fetched < 8, f"a widen should fetch only the new thread, not both ({fetched} requests)"


def test_a_moved_thread_is_refetched_rather_than_skipped(engine: Engine, fake: FakeGitHub) -> None:
    """The skip is keyed on the timestamp, not on presence."""
    issue = fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")
    _sample(engine, fake)

    issue.payload["updated_at"] = "2026-07-09T00:00:00Z"
    fake.add_comment(issue, 100, "something new", created_at="2026-07-09T00:00:00Z")
    result = _sample(engine, fake)

    assert (result.indexed, result.skipped) == (1, 0)
    assert result.documents_written == 1


# -- the declared floor ----------------------------------------------------


def test_the_floor_is_recorded_and_reported(engine: Engine, fake: FakeGitHub) -> None:
    """Nothing else in the schema can say "this corpus starts in 2026":
    ``sync_state.high_water`` records where a pass *reached*, which reads as covered for
    ground it never walked. Section 10 needs the window, because a recall number is only
    comparable against a baseline restricted to the same one."""
    fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")

    _sample(engine, fake)

    with engine.connect() as conn:
        rows = repo_layer.samples(conn, REPO)
        summary = repo_layer.index_summary(conn)
    assert [(r["thread_type"], r["indexed_from"]) for r in rows] == [("issue", WINDOW)]
    assert "updated>=" in rows[0]["selector"]
    assert summary["samples"][0]["thread_type"] == "issue"


def test_an_earlier_sample_lowers_the_floor_and_a_later_one_does_not_raise_it(
    engine: Engine, fake: FakeGitHub
) -> None:
    """The rows already indexed do not go away, so the corpus really does start where it
    started."""
    fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")
    _sample(engine, fake)

    with fake.client() as client:
        sample(engine, client, REPO, since=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc))
        sample(engine, client, REPO, since=dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))

    with engine.connect() as conn:
        assert repo_layer.samples(conn, REPO)[0]["indexed_from"] == dt.datetime(
            2025, 1, 1, tzinfo=dt.timezone.utc
        )


def test_the_floor_is_declared_before_the_first_thread_is_fetched(
    engine: Engine, fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run killed after one thread has still put a floor under what it built. An
    undeclared floor is the whole failure this table exists to prevent."""
    fake.add_issue(1, updated_at="2026-07-01T00:00:00Z")

    import relore.ingest.sample as sample_module

    monkeypatch.setattr(
        sample_module, "fetch_thread", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    _sample(engine, fake)

    with engine.connect() as conn:
        assert repo_layer.samples(conn, REPO)[0]["indexed_from"] == WINDOW


def test_an_unknown_thread_type_is_refused(engine: Engine, fake: FakeGitHub) -> None:
    with pytest.raises(ValueError):
        _sample(engine, fake, thread_type="discussion")

"""The clone refresh loop (issue #7).

The failure it exists for is silent: nothing re-ran `relored clone`, so HEAD aged and
`grep`, `copies` and `why` kept answering -- about a tree from whenever somebody last
remembered. The second failure class is the loop itself becoming the outage: a refresh
that cannot reach github.com must leave the clone it has and say so.
"""

from __future__ import annotations

import threading

from relore.code.clone import CloneInfo, CloneUnavailable, WorkingClones
from relore.code.refresh import cloned_repos, refresh_once, start_refresh


def _clone_dir(root, repo: str) -> None:
    (root / repo.replace("/", "__") / ".git").mkdir(parents=True)


def test_only_directories_that_are_clones_count(tmp_path) -> None:
    clones = WorkingClones(str(tmp_path))
    _clone_dir(tmp_path, "owner/name")
    (tmp_path / "owner__half_done").mkdir()  # an interrupted clone: no .git yet

    assert cloned_repos(clones) == ["owner/name"]


def test_a_missing_root_is_not_an_error(tmp_path) -> None:
    """`serve` starts before anyone has cloned anything, and that is the normal first day."""
    assert cloned_repos(WorkingClones(str(tmp_path / "nothing/here"))) == []


def test_the_refresh_never_creates_a_clone(tmp_path, monkeypatch) -> None:
    """Which repositories deserve a clone is an operator's decision and a long download."""
    clones = WorkingClones(str(tmp_path))
    _clone_dir(tmp_path, "owner/name")
    asked: list[str] = []
    monkeypatch.setattr(
        WorkingClones,
        "ensure",
        lambda self, repo, url=None: (
            asked.append(repo) or CloneInfo(repo=repo, path="p", head="abc123", fetched_at="now")
        ),
    )

    refresh_once(clones)

    assert asked == ["owner/name"]


def test_one_unreachable_remote_does_not_stop_the_others(tmp_path, monkeypatch) -> None:
    clones = WorkingClones(str(tmp_path))
    _clone_dir(tmp_path, "owner/first")
    _clone_dir(tmp_path, "owner/second")

    def ensure(self, repo, url=None):
        if repo == "owner/first":
            raise CloneUnavailable("git fetch failed: could not resolve host")
        return CloneInfo(repo=repo, path="p", head="abc123", fetched_at="now")

    monkeypatch.setattr(WorkingClones, "ensure", ensure)

    done = refresh_once(clones)

    assert [info.repo for info in done] == ["owner/second"]


def test_no_interval_means_no_thread(tmp_path) -> None:
    """Off by default: a `git fetch` is the one thing `serve` does that leaves the pod."""
    assert start_refresh(WorkingClones(str(tmp_path)), 0) is None


def test_the_loop_refreshes_until_it_is_stopped(tmp_path, monkeypatch) -> None:
    clones = WorkingClones(str(tmp_path))
    _clone_dir(tmp_path, "owner/name")
    passes = threading.Semaphore(0)
    monkeypatch.setattr(
        WorkingClones,
        "ensure",
        lambda self, repo, url=None: (
            passes.release() or CloneInfo(repo=repo, path="p", head="abc123", fetched_at="now")
        ),
    )
    stop = threading.Event()

    thread = start_refresh(clones, 0.01, stop=stop)
    assert passes.acquire(timeout=5), "the loop never ran a pass"
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive()

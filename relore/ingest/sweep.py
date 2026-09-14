"""The existence sweep -- section 5.2's residual gap.

A thread whose only change was a *deletion*, which then goes permanently quiet, is
invisible to the poll: whether a deletion bumps ``updated_at`` is a GitHub implementation
detail this design does not bet on. So reconcile **existence** rather than content, on a
slow cadence -- weekly is ample.

Cost: a full uncapped walk of both comment endpoints, and the live id sets held in memory
(a few tens of MB on a repository with a few hundred thousand comments). That is why it is
a separate verb and not part of the poll.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import Engine

from relore.github.bulk import BulkObject, walk_issue_comments, walk_review_comments
from relore.github.client import GitHubClient
from relore.ingest.index_thread import derive_thread
from relore.store import repository as repo_layer

log = logging.getLogger(__name__)

SWEEP_PASS = "sweep"

WALKS = (
    ("issue_comment", walk_issue_comments),
    ("review_comment", walk_review_comments),
)


@dataclass
class SweepResult:
    repo: str
    live: dict[str, int] = field(default_factory=dict)
    removed: dict[str, int] = field(default_factory=dict)
    rederived: int = 0
    documents_written: int = 0

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())


def sweep(engine: Engine, client: GitHubClient, repo: str) -> SweepResult:
    result = SweepResult(repo=repo)
    touched: set[int] = set()

    for object_type, walker in WALKS:
        live = {obj.object_id for obj in _walk_all(walker, client, repo)}
        result.live[object_type] = len(live)
        with engine.connect() as conn:
            staged = repo_layer.staged_object_ids(conn, repo, object_type)
        gone = staged - live
        result.removed[object_type] = len(gone)
        if not gone:
            continue
        log.info("%s: %d %s objects no longer exist", repo, len(gone), object_type)
        with engine.begin() as conn:
            touched |= repo_layer.threads_of_objects(conn, repo, object_type, gone)
            repo_layer.delete_raw(conn, repo, object_type, gone)

    # Deriving after the deletes, per thread, so a failure mid-list leaves the rest of the
    # reconciliation intact and a re-run finishes it.
    for number in sorted(touched):
        with engine.begin() as conn:
            try:
                result.documents_written += derive_thread(conn, repo, number).wrote
            except LookupError:
                log.warning("%s#%s: thread row gone, nothing to re-derive", repo, number)
                continue
            result.rederived += 1

    with engine.begin() as conn:
        repo_layer.touch_pass(conn, repo, SWEEP_PASS, ok=True)
    return result


def _walk_all(walker, client: GitHubClient, repo: str) -> Iterator[BulkObject]:
    """Deliberately unfiltered: a ``since`` window cannot tell you what is *absent*."""
    return walker(client, repo, None)

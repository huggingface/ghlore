"""``relored backfill`` -- the full history, in section 3's four passes.

A backfill is not a very long poll. The poll fetches one thread at a time, which is two
to five requests; at tens of thousands of threads that is an order of magnitude more
budget than exists. So the backfill walks the *repository-wide* endpoints instead, stages
what they return, and derives threads from staged rows afterwards.

Every pass checkpoints independently (section 5.2 rule 4), so a stalled per-PR pass never
blocks the thread walk, and the whole thing is **one restartable day** rather than one
unrepeatable one.

The thread walk deliberately shares the ``threads`` high-water mark with the poll: they
are the same logical pass, so a finishing backfill hands over to the poller with no gap
and no special case. The corollary is that **a backfill and a poll must not run against
the same repository at the same time** -- they would fight over that mark.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine

from relore.github.bulk import (
    BulkObject,
    walk_issue_comments,
    walk_review_comments,
    walk_threads,
)
from relore.github.client import GitHubClient
from relore.github.graphql import BATCH, GraphQLClient, fetch_pr_details
from relore.ingest.authority import AuthorityResult, resolve_authority
from relore.ingest.index_thread import derive_thread
from relore.ingest.poll import OVERLAP
from relore.ingest.timestamps import iso_utc, parse_timestamp
from relore.store import repository as repo_layer
from relore.store.dialect import utcnow

log = logging.getLogger(__name__)

Walker = Callable[[GitHubClient, str, str | None], Iterator[BulkObject]]

# Pass names match section 4's `sync_state.pass` vocabulary.
BULK_PASSES: tuple[tuple[str, Walker], ...] = (
    ("threads", walk_threads),
    ("issue_comments", walk_issue_comments),
    ("pr_comments", walk_review_comments),
)
DERIVE_PASS = "derive"
FILES_PASS = "files"
AUTHORITY_PASS = "authority"

# One transaction per batch. This is the resumability unit: a kill loses at most this
# many staged objects, and the next run re-covers them from the pass's high-water mark.
BATCH_SIZE = 200


@dataclass
class PassResult:
    name: str
    staged: int = 0
    batches: int = 0
    skipped: bool = False
    documents_written: int = 0


@dataclass
class BackfillResult:
    repo: str
    passes: list[PassResult] = field(default_factory=list)
    derived: int = 0
    documents_written: int = 0
    authority: AuthorityResult | None = None

    @property
    def staged(self) -> int:
        return sum(p.staged for p in self.passes)


def backfill(
    engine: Engine,
    client: GitHubClient,
    repo: str,
    *,
    batch_size: int = BATCH_SIZE,
    only: tuple[str, ...] | None = None,
    derive: bool = True,
    gql: GraphQLClient | None = None,
    authority: bool = True,
    refresh_details: bool = False,
) -> BackfillResult:
    result = BackfillResult(repo=repo)
    for name, walker in BULK_PASSES:
        if only and name not in only:
            result.passes.append(PassResult(name=name, skipped=True))
            continue
        result.passes.append(_bulk_pass(engine, client, repo, name, walker, batch_size))

    if derive and (not only or DERIVE_PASS in only):
        result.derived, result.documents_written = derive_all(engine, repo)

    # Last, because it needs `threads.merged_at` -- which exists only once the thread walk
    # has been derived.
    if gql is not None and (not only or FILES_PASS in only):
        files = _files_pass(engine, gql, repo, refresh=refresh_details)
        result.passes.append(files)
        result.documents_written += files.documents_written
    elif gql is None:
        log.info("%s: skipping the %s pass (no GraphQL client)", repo, FILES_PASS)
        result.passes.append(PassResult(name=FILES_PASS, skipped=True))

    # Last, because it needs both of the passes above: `MEMBER` authors come from derived
    # documents, and the `merged_by` fallback comes from the per-PR pass.
    if authority and (not only or AUTHORITY_PASS in only):
        outcome = resolve_authority(engine, repo, client=client)
        result.authority = outcome
        result.documents_written += outcome.documents_written
    return result


def _files_pass(
    engine: Engine, gql: GraphQLClient, repo: str, *, refresh: bool = False
) -> PassResult:
    """Reviews, changed files and commits for merged PRs, batched over GraphQL.

    Section 3's fourth constraint: there is no repository-wide reviews endpoint, so on the
    backfill path this is the **only** source of review bodies. Point-limited rather than
    request-limited, hence budgeted in hours and checkpointed per batch.

    It derives each PR in the same transaction that stages it, rather than deferring to a
    second global derive pass: a kill costs one batch either way, and this way the index is
    never carrying staged detail nobody has read.

    **PRs whose detail is already staged are skipped**, which is what makes a second run
    cost the PRs merged since the first rather than the whole history again -- 20,429 of
    them on `transformers`, hours of points, to collect the dozen the poll has since found.
    The poller stages no detail (it runs the `threads` pass only), so those PRs carry no
    diff stats, no `merged_by` and no reviews until this runs: cheap enough to schedule is
    the difference between a gap that closes daily and one that closes when somebody
    remembers. ``refresh`` re-stages everything, for when the *extraction* changed rather
    than the corpus.
    """
    with engine.connect() as conn:
        resume = repo_layer.get_cursor(conn, repo, FILES_PASS)
        numbers = repo_layer.merged_pr_numbers(conn, repo, after=int(resume) if resume else None)
        authority = repo_layer.get_authority(conn, repo)
        if not refresh:
            staged = repo_layer.staged_object_ids(conn, repo, "pr_details")
            numbers = [number for number in numbers if str(number) not in staged]
    if resume:
        log.info("%s: resuming the %s pass after PR %s", repo, FILES_PASS, resume)
    log.info("%s: %s pass over %d merged PRs", repo, FILES_PASS, len(numbers))

    outcome = PassResult(name=FILES_PASS)
    now = utcnow()
    for start in range(0, len(numbers), BATCH):
        chunk = numbers[start : start + BATCH]
        details = fetch_pr_details(gql, repo, chunk)
        with engine.begin() as conn:
            repo_layer.stage_raw(
                conn,
                repo,
                [
                    {
                        "repo": repo,
                        "object_type": "pr_details",
                        "object_id": str(number),
                        "thread_number": number,
                        "payload": node,
                        "fetched_at": now,
                        "github_updated_at": None,
                    }
                    for number, node in sorted(details.items())
                ],
            )
            for number in sorted(details):
                outcome.documents_written += derive_thread(
                    conn, repo, number, authority=authority
                ).wrote
            outcome.staged += len(details)
            outcome.batches += 1
            # Checkpoint the whole chunk, not only what came back: a PR that returned no
            # node is deleted or unreachable, and retrying it every run would stall here.
            repo_layer.set_cursor(conn, repo, FILES_PASS, str(chunk[-1]))

    with engine.begin() as conn:
        repo_layer.set_cursor(conn, repo, FILES_PASS, None)
        repo_layer.touch_pass(conn, repo, FILES_PASS, ok=True)
    return outcome


def _bulk_pass(
    engine: Engine,
    client: GitHubClient,
    repo: str,
    name: str,
    walker: Walker,
    batch_size: int,
) -> PassResult:
    with engine.connect() as conn:
        mark = repo_layer.high_water(conn, repo, name)
    since = iso_utc(mark - OVERLAP) if mark else None
    log.info("%s: pass %s from %s", repo, name, since or "the start")

    outcome = PassResult(name=name)
    batch: list[BulkObject] = []
    for obj in walker(client, repo, since):
        batch.append(obj)
        if len(batch) >= batch_size:
            outcome.staged += _commit_batch(engine, repo, name, batch)
            outcome.batches += 1
            batch = []
    if batch:
        outcome.staged += _commit_batch(engine, repo, name, batch)
        outcome.batches += 1

    with engine.begin() as conn:
        repo_layer.touch_pass(conn, repo, name, ok=True)
    log.info("%s: pass %s staged %d objects", repo, name, outcome.staged)
    return outcome


def _commit_batch(engine: Engine, repo: str, name: str, batch: list[BulkObject]) -> int:
    """Stage a batch and advance this pass's mark, atomically.

    The mark moves to the newest checkpoint in the batch, which is only sound because
    every walk is ascending -- and ``advance_high_water`` refuses to move backwards, so a
    walk that is *not* ascending degrades to no progress rather than to lost objects.
    """
    now = utcnow()
    rows: list[dict[str, Any]] = [
        {
            "repo": repo,
            "object_type": obj.object_type,
            "object_id": obj.object_id,
            "thread_number": obj.thread_number,
            "payload": obj.payload,
            "fetched_at": now,
            "github_updated_at": parse_timestamp(obj.payload.get("updated_at")),
        }
        for obj in batch
    ]
    checkpoints = [parse_timestamp(obj.checkpoint) for obj in batch]
    newest = max((c for c in checkpoints if c is not None), default=None)
    with engine.begin() as conn:
        repo_layer.stage_raw(conn, repo, rows)
        if newest is not None:
            repo_layer.advance_high_water(conn, repo, name, newest)
    return len(rows)


def derive_all(engine: Engine, repo: str) -> tuple[int, int]:
    """Rebuild every staged thread. Local, no network, resumable, re-runnable.

    The cursor here is *interrupted-run progress only* and is cleared on completion, so a
    second full backfill really does re-derive everything -- and the zero document writes
    it reports are the idempotency claim rather than an artefact of skipping the work.
    """
    with engine.connect() as conn:
        resume = repo_layer.get_cursor(conn, repo, DERIVE_PASS)
    after = int(resume) if resume else None
    if after is not None:
        log.info("%s: resuming derive after thread %d", repo, after)

    with engine.connect() as conn:
        numbers = repo_layer.staged_thread_numbers(conn, repo, after=after)
        # The same few hundred rows for every thread, so read them once (section 6.2).
        authority = repo_layer.get_authority(conn, repo)

    derived = written = 0
    for number in numbers:
        with engine.begin() as conn:
            try:
                written += derive_thread(conn, repo, number, authority=authority).wrote
            except LookupError:
                # Staged comments whose thread row never arrived -- the thread walk was
                # interrupted, or the comment outlived its thread. Either way the next
                # pass over `threads` supplies it; skipping is correct, silence is not.
                log.warning("%s#%s: comments staged but no thread row yet", repo, number)
                continue
            derived += 1
            repo_layer.set_cursor(conn, repo, DERIVE_PASS, str(number))

    with engine.begin() as conn:
        repo_layer.set_cursor(conn, repo, DERIVE_PASS, None)
        repo_layer.touch_pass(conn, repo, DERIVE_PASS, ok=True)
    return derived, written

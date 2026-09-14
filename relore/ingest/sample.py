"""``relored sample`` -- a bounded, *complete* subset of a repository's history.

Section 10 needs a corpus to build an evaluation set on, and a full backfill of a large
repository is about a day of API budget (section 3). The obvious shortcut is wrong in a
way that does not look wrong: a backfill with a ``since`` on the repository-wide passes
filters **individual comments**, so a thread that moved inside the window is indexed
without the older comments that explain it. The hole is *inside* the retrievable unit,
and the discussion is exactly what the failure slice's ground truth is.

So a sample is fetched thread by thread, through ``index_thread``, which reads each one
whole. Two to five requests per thread is what the backfill exists to avoid at fifty
thousand threads and what it should not avoid at two thousand.

Three rules it does not get to skip:

* **Section 5.2 rule 2 and 5.** The checkpoint advances per *committed* thread and stops
  at the first failure. Threads arrive ascending by ``updated_at``, so advancing past a
  failure would skip that thread for ever while the pass looked healthy.
* **It checkpoints under its own pass name, never ``threads``.** A sample that advanced
  the thread walk's mark would tell a later ``backfill`` -- or a ``poll``, which shares
  that mark -- that the years before the window had already been covered.
* **The floor is recorded.** ``sync_state.high_water`` says where a pass has *reached*,
  which on a sampled index reads as "covered" for ground it never walked. Nothing else in
  the schema can express a corpus that starts in 2026, so ``repo_sample`` does, and
  ``status`` prints it -- because a recall number from a sampled index is only comparable
  against a baseline restricted to the same window (section 10).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine

from relore.github.bulk import walk_threads
from relore.github.client import GitHubClient, PaginationCapReached
from relore.github.fetch_thread import fetch_thread
from relore.ingest.index_thread import index_payload
from relore.ingest.poll import OVERLAP, may_advance
from relore.ingest.timestamps import iso_utc, parse_timestamp
from relore.store import repository as repo_layer

log = logging.getLogger(__name__)

THREAD_TYPES = ("issue", "pr")


def pass_name(thread_type: str) -> str:
    """Its own row in ``sync_state``. See the third rule in this module's docstring."""
    return f"sample:{thread_type}"


@dataclass
class SampleResult:
    repo: str
    thread_type: str
    since: dt.datetime
    merged_only: bool = False
    seen: int = 0
    """Threads the walk returned, of any kind."""
    selected: int = 0
    """Threads that matched the filter -- the size of the intended sample."""
    skipped: int = 0
    """Selected threads already held at the listing's ``updated_at``, so not re-fetched."""
    indexed: int = 0
    documents_written: int = 0
    signals_written: int = 0
    failed: list[int] = field(default_factory=list)
    capped: bool = False

    @property
    def clean(self) -> bool:
        return not self.failed and not self.capped


def sample(
    engine: Engine,
    client: GitHubClient,
    repo: str,
    *,
    since: dt.datetime,
    thread_type: str = "issue",
    merged_only: bool = False,
    limit: int | None = None,
) -> SampleResult:
    """Index every ``thread_type`` thread updated since ``since``, each one whole.

    Restartable: killed halfway, the next run resumes from the checkpoint and re-covers
    the remainder. Re-running a finished sample is a no-op at the document level, which
    is section 5.1's claim and not a separate one.
    """
    if thread_type not in THREAD_TYPES:
        raise ValueError(f"thread_type must be one of {list(THREAD_TYPES)}")
    name = pass_name(thread_type)
    with engine.connect() as conn:
        mark = repo_layer.high_water(conn, repo, name)
        floor = _recorded_floor(conn, repo, thread_type)
        current = repo_layer.thread_freshness(conn, repo, thread_type)

    # The mark records progress *within* a window, so it is only a valid resume point for
    # the same window or a narrower one. **Widening** -- asking for an earlier `since`
    # than the recorded floor -- makes it worthless: clamping to it would walk from where
    # the old run finished and index nothing, reporting success. That is the silent
    # failure this whole module is written against, so the mark is dropped instead.
    widening = floor is not None and since < floor
    if widening:
        log.info(
            "%s: widening the %s sample from %s to %s; ignoring the progress mark at %s",
            repo,
            thread_type,
            iso_utc(floor) if floor else "?",
            iso_utc(since),
            iso_utc(mark) if mark else "none",
        )
    window = since if widening or not mark else max(since, mark - OVERLAP)
    result = SampleResult(repo=repo, thread_type=thread_type, since=since, merged_only=merged_only)

    # Written before the first request: a run killed after one thread has still put a
    # floor under this corpus, and an undeclared floor is the failure this table exists
    # to prevent.
    with engine.begin() as conn:
        repo_layer.record_sample(
            conn,
            repo,
            thread_type,
            indexed_from=since,
            selector=_selector(since, merged_only, limit),
        )

    log.info(
        "%s: sampling %s threads updated since %s%s",
        repo,
        thread_type,
        iso_utc(window),
        " (merged only)" if merged_only else "",
    )
    try:
        candidates = _select(client, repo, window, thread_type, merged_only, limit, result)
    except PaginationCapReached:
        # Whatever the walk returned is still a valid prefix; the checkpoint advances
        # through it and the next run resumes from there.
        result.capped = True
        log.warning("%s: GitHub refused deeper pagination; sampling the prefix found", repo)
        candidates = []

    blocked = False
    for number, checkpoint in candidates:
        # Already holding a current copy: the listing's own `updated_at` is the freshness
        # signal, and it came free with the walk. This is what makes widening a normal
        # operation rather than a re-fetch of everything -- and it is safe in the same
        # sense section 5.2 rule 1 is: an unmoved `updated_at` means the thread has not
        # moved. (A review submission does not bump it, which is why the *poll* has a
        # second discovery layer; a sample re-covering ground it indexed minutes ago has
        # no such gap to close.)
        stamp = parse_timestamp(checkpoint)
        if stamp is not None and current.get(number) == stamp:
            result.skipped += 1
            with engine.begin() as conn:
                if may_advance(stamp, blocked=blocked, bound=None):
                    repo_layer.advance_high_water(conn, repo, name, stamp)
            continue
        try:
            payload = fetch_thread(client, repo, number)
        except Exception:
            log.exception("%s#%s: fetch failed", repo, number)
            result.failed.append(number)
            blocked = True
            continue
        try:
            with engine.begin() as conn:
                indexed = index_payload(conn, payload)
                if may_advance(indexed.updated_at, blocked=blocked, bound=None):
                    assert indexed.updated_at is not None
                    repo_layer.advance_high_water(conn, repo, name, indexed.updated_at)
            result.indexed += 1
            result.documents_written += indexed.wrote
            result.signals_written += indexed.signals.wrote
            if indexed.redactions:
                # Names only. The matched value is never logged or used as a metric label.
                log.info("%s#%s: redacted %s", repo, number, dict(indexed.redactions))
        except Exception:
            log.exception("%s#%s: index failed", repo, number)
            result.failed.append(number)
            blocked = True
        if result.indexed % 50 == 0:
            log.info(
                "%s: %d/%d indexed, %d failed",
                repo,
                result.indexed,
                result.selected,
                len(result.failed),
            )

    with engine.begin() as conn:
        repo_layer.touch_pass(conn, repo, name, ok=result.clean)
    return result


def _select(
    client: GitHubClient,
    repo: str,
    window: dt.datetime,
    thread_type: str,
    merged_only: bool,
    limit: int | None,
    result: SampleResult,
) -> list[tuple[int, str | None]]:
    """The listing walk, filtered. One request per 100 threads of *any* kind.

    ``merged_at`` is read from the list payload's nested ``pull_request`` block rather
    than from ``GET /pulls/{n}``: the bulk walk is the only place it is free, and paying
    a request per candidate to discover that most of them are unmerged would cost more
    than the sample.
    """
    out: list[tuple[int, str | None]] = []
    for obj in walk_threads(client, repo, iso_utc(window)):
        result.seen += 1
        if obj.object_type != thread_type:
            continue
        if merged_only and not (obj.payload.get("pull_request") or {}).get("merged_at"):
            continue
        result.selected += 1
        out.append((obj.thread_number, obj.checkpoint))
        if limit is not None and len(out) >= limit:
            log.info("%s: stopping the walk at --limit %d", repo, limit)
            break
    # Ascending by the timestamp the checkpoint moves on, which the walk already is --
    # made explicit because rule 5 depends on it and a walk is easy to change.
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    out.sort(key=lambda item: (parse_timestamp(item[1]) or epoch, item[0]))
    return out


def _recorded_floor(conn: Any, repo: str, thread_type: str) -> dt.datetime | None:
    for row in repo_layer.samples(conn, repo):
        if row["thread_type"] == thread_type:
            return row["indexed_from"]
    return None


def _selector(since: dt.datetime, merged_only: bool, limit: int | None) -> str:
    """How this set was chosen, in the form a reader can re-run."""
    parts = [f"updated>={iso_utc(since)}"]
    if merged_only:
        parts.append("merged only")
    if limit is not None:
        parts.append(f"limit {limit}")
    return ", ".join(parts)

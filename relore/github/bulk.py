"""The repository-wide walks a backfill uses instead of per-thread fetches.

A backfill cannot use :func:`~relore.github.fetch_thread.fetch_thread`: two to five
requests times tens of thousands of threads is an order of magnitude more budget than
section 3 allows. So it walks the *bulk* endpoints -- one pass per object kind -- stages
what they return, and lets ``derive`` assemble threads from staged rows afterwards. That
is the whole reason fetch and derive are separate (section 5.0).

Each walk yields objects with the timestamp that advances its own pass's high-water mark,
so every pass resumes independently (section 5.2 rule 4).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from relore.github.client import GitHubClient
from relore.github.fetch_thread import strip_mirror_fields

log = logging.getLogger(__name__)

_THREAD_NUMBER = re.compile(r"/(?:issues|pulls)/(\d+)")


@dataclass(frozen=True)
class BulkObject:
    """One staged object plus the checkpoint it justifies."""

    object_type: str
    object_id: str
    thread_number: int
    payload: dict[str, Any]
    checkpoint: str | None
    """The ISO timestamp this pass orders by. Advancing the mark past it is safe."""


def walk_threads(client: GitHubClient, repo: str, since: str | None = None) -> Iterator[BulkObject]:
    """Every issue and PR, ascending by ``updated_at``.

    ~475 requests on the reference repository. The list payload is not the whole story
    for a PR -- ``merged_at``, ``merged_by`` and the diff stats live only on
    ``GET /pulls/{n}`` -- which is what the per-PR pass is for.
    """
    params: dict[str, Any] = {"state": "all", "sort": "updated", "direction": "asc"}
    if since:
        params["since"] = since
    for item in client.paginate(f"repos/{repo}/issues", params):
        number = int(item["number"])
        yield BulkObject(
            object_type="pr" if "pull_request" in item else "issue",
            object_id=str(number),
            thread_number=number,
            payload=strip_mirror_fields(item),
            checkpoint=item.get("updated_at"),
        )


def walk_issue_comments(
    client: GitHubClient, repo: str, since: str | None = None
) -> Iterator[BulkObject]:
    """Every conversation comment, through the 30,000-item cap.

    ``since`` on this endpoint filters by *updated* time while we order by ``created_at``,
    which is the chaining key section 3 measured. The mismatch is safe in one direction
    only, and that is the direction we need: ``updated_at >= created_at`` always, so a
    slice can re-serve an old comment that was recently edited but can never hide one.
    Re-processing is free (section 5.1), hiding would be silent.
    """
    params: dict[str, Any] = {"sort": "created"}
    if since:
        params["since"] = since
    for item in client.paginate_since_chained(
        f"repos/{repo}/issues/comments", params, timestamp_field="created_at"
    ):
        number = _thread_number(item, "issue_url")
        if number is None:
            continue
        yield BulkObject(
            object_type="issue_comment",
            object_id=str(item["id"]),
            thread_number=number,
            payload=strip_mirror_fields(item),
            checkpoint=item.get("created_at"),
        )


def walk_review_comments(
    client: GitHubClient, repo: str, since: str | None = None
) -> Iterator[BulkObject]:
    """Every inline review comment. Not capped -- this one walks straight through.

    The highest-value pass in the backfill: roughly three-quarters of the mentions of a
    technical symbol live in comments, and the file-and-line ones are what the code lens
    later resolves.

    **This one walks at ``per_page=50``, and the halving is not caution.** A review
    comment carries its ``diff_hunk``, so a page here is far heavier than a page of
    issue comments, and GitHub cannot always build one. Measured 2026-09-09, on the page
    that wedged the production backfill of `huggingface/transformers` for good:

        per_page=100 -> HTTP 504 Gateway Timeout (502 from inside the cluster)
        per_page=50  -> HTTP 200, 50 items
        per_page=30  -> HTTP 200, 30 items

    No retry policy can help, which is what makes this a page-size problem rather than a
    transport one: the walk resumes from the same ``since``, asks for the same 100 items,
    and GitHub times out again for ever. It cost ~1,349 pages at 100 and costs ~2,700 at
    50 -- affordable against 5,000 requests/hour, and this endpoint is not capped (§3).
    """
    params: dict[str, Any] = {"sort": "created", "direction": "asc", "per_page": 50}
    if since:
        params["since"] = since
    for item in client.paginate(f"repos/{repo}/pulls/comments", params):
        number = _thread_number(item, "pull_request_url")
        if number is None:
            continue
        yield BulkObject(
            object_type="review_comment",
            object_id=str(item["id"]),
            thread_number=number,
            payload=strip_mirror_fields(item),
            checkpoint=item.get("created_at"),
        )


def _thread_number(item: dict[str, Any], url_field: str) -> int | None:
    """Read the thread number before the mirror fields are stripped.

    ``strip_mirror_fields`` drops every ``*_url`` but ``html_url``, so this must run on
    the raw payload -- afterwards the link that says which thread a comment belongs to is
    gone.
    """
    for field in (url_field, "html_url"):
        match = _THREAD_NUMBER.search(str(item.get(field) or ""))
        if match:
            return int(match.group(1))
    log.warning("comment %s has no resolvable thread number", item.get("id"))
    return None

"""Page size is part of section 3's pass structure, not a tuning knob."""

from __future__ import annotations

import json

import httpx
from fake_github import BASE

from relore.github.bulk import walk_issue_comments, walk_review_comments
from relore.github.client import GitHubClient


def _recording_client(seen: list[httpx.URL]) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=b"[]", headers={"Content-Type": "application/json"})

    return GitHubClient(
        "t",
        base_url=BASE,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE),
        sleep=lambda _s: None,
    )


def test_the_review_comment_walk_asks_for_fifty_not_a_hundred() -> None:
    """A 100-item page of review comments is one GitHub cannot always build.

    Measured 2026-09-09 against the page that wedged a production backfill:
    per_page=100 timed out (504 / 502), per_page=50 and 30 returned 200. The walk resumes
    from the same `since` after a crash, so an unservable page size is permanent rather
    than transient -- no retry policy reaches it, which is why this is asserted here.
    """
    seen: list[httpx.URL] = []
    list(walk_review_comments(_recording_client(seen), "owner/name", since="2026-01-01T00:00:00Z"))
    assert seen, "the walk must have made a request"
    assert seen[0].params.get("per_page") == "50"


def test_the_issue_comment_walk_keeps_the_full_page() -> None:
    """The halving is specific to review comments, which carry a `diff_hunk` each.

    Pinned so the next person to meet a 502 does not lower this one too: it is capped at
    30,000 items (section 3), and a smaller page spends more of that budget per slice.
    """
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(
            200, content=json.dumps([]).encode(), headers={"Content-Type": "application/json"}
        )

    client = GitHubClient(
        "t",
        base_url=BASE,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE),
        sleep=lambda _s: None,
    )
    list(walk_issue_comments(client, "owner/name", since="2026-01-01T00:00:00Z"))
    assert seen and seen[0].params.get("per_page") == "100"

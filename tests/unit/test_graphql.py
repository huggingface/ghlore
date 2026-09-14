"""The per-PR pass is point-limited, not request-limited (section 3)."""

from __future__ import annotations

import json

import httpx
import pytest
from fake_github import FakeGitHub, FakeGraphQL

from relore.github.graphql import (
    BATCH,
    GraphQLClient,
    GraphQLError,
    build_pr_query,
    fetch_pr_details,
)


def test_one_query_aliases_every_pr_in_the_batch() -> None:
    query = build_pr_query([7, 8])
    assert "pr7: pullRequest(number: 7)" in query
    assert "pr8: pullRequest(number: 8)" in query
    assert "rateLimit { limit cost remaining resetAt }" in query


def test_batching_keeps_requests_proportional_to_prs_over_batch_size() -> None:
    fake = FakeGitHub()
    for n in range(1, BATCH * 2 + 2):
        fake.add_pr(n, merged_at="2026-01-01T00:00:00Z")
    gql = FakeGraphQL(fake)
    with gql.client() as client:
        got = fetch_pr_details(client, fake.repo, list(range(1, BATCH * 2 + 2)))
    assert len(got) == BATCH * 2 + 1
    assert len(gql.queries) == 3


def test_a_deleted_pr_is_skipped_not_fatal() -> None:
    fake = FakeGitHub()
    fake.add_pr(1, merged_at="2026-01-01T00:00:00Z")
    with FakeGraphQL(fake).client() as client:
        got = fetch_pr_details(client, fake.repo, [1, 999])
    assert set(got) == {1}


def test_truncation_is_recorded_rather_than_hidden() -> None:
    """A PR with more than 100 files is a bulk rename; the flag says so."""
    fake = FakeGitHub()
    pr = fake.add_pr(1, merged_at="2026-01-01T00:00:00Z")
    pr.files = [f"f{i}.py" for i in range(150)]
    with FakeGraphQL(fake).client() as client:
        node = fetch_pr_details(client, fake.repo, [1])[1]
    assert node["truncated_files"] is True


def test_a_rate_limited_reply_waits_and_retries() -> None:
    fake = FakeGitHub()
    fake.add_pr(1, merged_at="2026-01-01T00:00:00Z")
    gql = FakeGraphQL(fake)
    gql.fail_once_rate_limited = True
    slept: list[float] = []
    with gql.client(sleep=slept.append) as client:
        assert set(fetch_pr_details(client, fake.repo, [1])) == {1}
    assert len(slept) == 1


def test_partial_errors_alongside_data_are_not_fatal() -> None:
    """GitHub returns 200 with an `errors` block when one alias is unreachable."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "data": {"repository": {"pr1": {"number": 1}}},
                    "errors": [{"message": "Could not resolve to a node", "type": "NOT_FOUND"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )

    client = GraphQLClient(
        "t", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )
    assert client.execute("query{}")["repository"]["pr1"]["number"] == 1


def test_errors_with_no_data_raise() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps({"data": None, "errors": [{"message": "Bad credentials"}]}).encode(),
            headers={"Content-Type": "application/json"},
        )

    client = GraphQLClient(
        "t", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )
    with pytest.raises(GraphQLError, match="Bad credentials"):
        client.execute("query{}")


def test_the_client_waits_before_the_budget_is_spent() -> None:
    """Pacing costs the same wall clock as blocking, but a mid-batch refusal wastes the
    points the batch already spent."""
    body = {
        "data": {
            "rateLimit": {
                "limit": 5000,
                "cost": 50,
                "remaining": 60,
                "resetAt": "2099-01-01T00:00:00Z",
            },
            "repository": {},
        }
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )

    slept: list[float] = []
    client = GraphQLClient(
        "t", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append
    )
    client.execute("query{}")
    assert slept and slept[0] > 0, "remaining < cost*2 must throttle"

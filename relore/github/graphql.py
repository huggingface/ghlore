"""The GraphQL client, and the batched per-PR query.

Section 3, constraint 4: there is **no repository-wide reviews endpoint**.
``pulls/{n}/reviews``, ``pulls/{n}/files`` and the commit list are per-PR only, so at
tens of thousands of PRs this is the expensive pass -- and the one that must be batched.
~25 PRs per query, aliased into one request.

This pass is **point**-limited, not request-limited: 5,000 points an hour, which is why
section 3 budgets 6-12 hours for it against about an hour for all the REST passes
together. So the client reads the ``rateLimit`` block out of every response and waits
before it is refused, rather than after.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from relore.ingest.timestamps import parse_timestamp

log = logging.getLogger(__name__)

ENDPOINT = "https://api.github.com/graphql"

# 25 aliased PRs per query, per section 3. Higher risks the 500-node limit and makes a
# single failure more expensive; lower wastes the fixed per-request cost.
BATCH = 25

# `files` and `commits` are capped rather than paged, and the cap is a budget decision:
# paging every large PR costs points this pass does not have (section 3).
#
# **The original reasoning for it was wrong, and the measurement is worth keeping.** It
# said a PR with more than 100 changed files is nearly always a bulk rename whose file
# list is not evidence. On `huggingface/transformers` a 323-file sweeping refactor that
# regresses one model is the *normal* shape of the interesting change:
# `huggingface/transformers#39847` touches 323 files, this pass staged 105, and the 218 it
# dropped included every `gpt_neox*` path -- which is exactly the entry someone was
# checking when they asked whether that PR caused the bug. So the field is most wrong
# where it is most needed.
#
# What follows from that is *reporting*, not a bigger page: `truncated_*` is on the stored
# node, `threads.metadata.changed_files` carries the true count, and the thread view now
# serves both so an absent path cannot be read as a negative fact. Lifting the cap is a
# section 3 budget question and belongs in the plan, not here.
PAGE = 100

_PR_FIELDS = f"""
    number
    mergedAt
    additions
    deletions
    changedFiles
    mergedBy {{ login }}
    reviews(first: 50) {{
      totalCount
      nodes {{
        databaseId
        body
        state
        submittedAt
        url
        authorAssociation
        author {{ login __typename }}
        commit {{ oid }}
      }}
    }}
    files(first: {PAGE}) {{
      totalCount
      nodes {{ path additions deletions changeType }}
    }}
    commits(first: {PAGE}) {{
      totalCount
      nodes {{ commit {{ oid messageHeadline messageBody }} }}
    }}
    closingIssuesReferences(first: 10) {{
      nodes {{ number repository {{ nameWithOwner }} }}
    }}
"""


class GraphQLError(RuntimeError):
    """GitHub answered 200 with an ``errors`` block, or refused outright."""


class GraphQLClient:
    def __init__(
        self,
        token: str,
        *,
        endpoint: str = ENDPOINT,
        user_agent: str = "relore",
        timeout: float = 60.0,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.max_retries = max_retries
        self._sleep = sleep
        self.last_rate_limit: dict[str, Any] = {}
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphQLClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        for attempt in range(self.max_retries + 1):
            response = self._client.post(self.endpoint, json=payload)
            if response.status_code >= 500 and attempt < self.max_retries:
                self._sleep(min(2.0**attempt, 60.0))
                continue
            if response.status_code == 403 or response.status_code == 429:
                wait = float(response.headers.get("retry-after") or 60.0)
                if attempt < self.max_retries:
                    log.warning("graphql %s; sleeping %.0fs", response.status_code, wait)
                    self._sleep(wait)
                    continue
            if response.status_code >= 400:
                raise GraphQLError(f"{response.status_code}: {response.text[:200]}")

            body = response.json()
            errors = body.get("errors") or []
            if errors and self._is_rate_limited(errors) and attempt < self.max_retries:
                self._wait_for_reset(body)
                continue
            if errors:
                # A NOT_FOUND on one alias is normal -- a PR can be deleted -- and comes
                # back alongside usable data. Only raise when there is nothing to use.
                if body.get("data") is None:
                    raise GraphQLError("; ".join(str(e.get("message")) for e in errors)[:400])
                log.warning("graphql partial errors: %s", errors[0].get("message"))
            self._note_rate_limit(body)
            self._throttle()
            return body.get("data") or {}
        raise GraphQLError("exhausted retries")

    # -- point budget ------------------------------------------------------

    @staticmethod
    def _is_rate_limited(errors: list[dict[str, Any]]) -> bool:
        return any(e.get("type") == "RATE_LIMITED" for e in errors)

    def _note_rate_limit(self, body: dict[str, Any]) -> None:
        limit = ((body.get("data") or {}).get("rateLimit")) or {}
        if limit:
            self.last_rate_limit = limit

    def _wait_for_reset(self, body: dict[str, Any]) -> None:
        self._note_rate_limit(body)
        self._sleep(self._seconds_until_reset() or 60.0)

    def _seconds_until_reset(self) -> float:
        reset = parse_timestamp(self.last_rate_limit.get("resetAt"))
        if reset is None:
            return 0.0
        return max(0.0, (reset - dt.datetime.now(dt.timezone.utc)).total_seconds() + 1.0)

    def _throttle(self) -> None:
        """Wait *before* being refused, not after.

        The budget is points per hour, so running it to zero and then blocking costs the
        same wall-clock time as pacing -- but a mid-batch refusal wastes the points the
        batch already spent. Waiting while one more batch would not fit is strictly cheaper.
        """
        limit = self.last_rate_limit
        remaining, cost = limit.get("remaining"), limit.get("cost")
        if remaining is None or cost is None or remaining > cost * 2:
            return
        wait = self._seconds_until_reset()
        if wait > 0:
            log.warning(
                "graphql points nearly spent (%s left, %s per query); waiting %.0fs",
                remaining,
                cost,
                wait,
            )
            self._sleep(wait)


def build_pr_query(numbers: Sequence[int]) -> str:
    """One query, one alias per PR, plus the point budget we are spending."""
    aliases = "\n".join(f"    pr{n}: pullRequest(number: {n}) {{{_PR_FIELDS}}}" for n in numbers)
    return (
        "query($owner: String!, $name: String!) {\n"
        "  rateLimit { limit cost remaining resetAt }\n"
        "  repository(owner: $owner, name: $name) {\n"
        f"{aliases}\n"
        "  }\n"
        "}"
    )


def fetch_pr_details(
    gql: GraphQLClient, repo: str, numbers: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """``{number: node}`` for the PRs that still exist.

    The node is returned **verbatim**: section 4 stores raw objects exactly as fetched, so
    the GraphQL shape is what gets staged and ``derive`` adapts it at read time. Translating
    on the way in would mean a later extractor improvement could never recover a field this
    version happened to drop.
    """
    owner, name = repo.split("/", 1)
    out: dict[int, dict[str, Any]] = {}
    for start in range(0, len(numbers), BATCH):
        chunk = list(numbers[start : start + BATCH])
        data = gql.execute(build_pr_query(chunk), {"owner": owner, "name": name})
        repository = data.get("repository") or {}
        for number in chunk:
            node = repository.get(f"pr{number}")
            if not node:
                continue  # deleted, or unreachable with this token
            node["truncated_files"] = (node.get("files") or {}).get("totalCount", 0) > PAGE
            node["truncated_commits"] = (node.get("commits") or {}).get("totalCount", 0) > PAGE
            out[number] = node
    return out

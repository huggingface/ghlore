"""Resolve write access per author, so ``MEMBER`` stops meaning "we don't know".

Section 6.2's trap, restated: ``author_association`` is only a proxy for write access.
``OWNER`` and ``COLLABORATOR`` are unambiguous, but on an org-owned repository the actual
maintainers report as ``MEMBER`` -- their access comes through a team rather than a direct
collaborator invite -- and so does everyone else in the organisation. Admitting ``MEMBER``
wholesale promotes thousands of strangers; rejecting it wholesale discards the maintainers
whose judgements are the entire reason the index exists.

Measured on ``huggingface/serge`` before this pass existed: **zero** documents in the
``authoritative`` tier, out of 415. Both maintainers read as ``MEMBER``.

This pass is network-bound and therefore separate from ``derive``, which section 5.0
requires to be local and repeatable. It writes ``repo_authority`` only; the tier itself is
still derived at derive time from the payload plus this table, so a rebuild reproduces it.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import Engine

from relore.github.client import GitHubClient, GitHubError
from relore.store import repository as repo_layer
from relore.store.dialect import utcnow

log = logging.getLogger(__name__)

#: Permissions that carry the write act. ``triage`` and ``read`` do not.
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})

#: Section 6.2's **bot-account list**, and it is deployment config rather than core:
#: only the operator knows which of their ``User`` accounts are machines. Comma-separated
#: logins, matched case-insensitively because GitHub logins are.
#:
#: ``user.type == "Bot"`` catches the obvious ones and is not enough. Measured on real
#: payloads: ``HuggingFaceDocBuilderDev`` presents as ``User`` / ``MEMBER`` and its
#: docs-build boilerplate is indistinguishable from a maintainer's comment to every rule in
#: section 6.2 -- so without this list it is admissible evidence, and it takes result slots.
#:
#: A deployment that indexes its own agent's comments and leaves this empty has
#: reintroduced by the back door exactly what section 11 refused: the agent's own
#: unreviewed output, returned to it as prior discussion.
BOT_ACCOUNTS_ENV = "RELORE_BOT_ACCOUNTS"


def bot_accounts() -> frozenset[str]:
    """The configured machine logins, lowercased. Empty is legal and is a choice.

    Read at derive time rather than captured at fetch time, for the same reason the tier
    is (section 6.2): a rebuild has to reproduce it, and changing the list has to be able
    to move documents between tiers. **After editing it, re-derive** -- ``trust`` and
    ``author_is_bot`` are ``DERIVED_COLUMNS`` (section 5.1), so ``relored derive`` moves
    exactly the documents whose tier changed and writes nothing else.
    """
    raw = os.environ.get(BOT_ACCOUNTS_ENV, "")
    return frozenset(login.strip().lower() for login in raw.split(",") if login.strip())


#: People gain and lose access rarely, so re-resolving is a slow background concern
#: rather than part of the poll. Section 6.2: standing is mutable, and this table answers
#: "today" rather than "then".
REFRESH_AFTER = dt.timedelta(days=30)


@dataclass
class AuthorityResult:
    repo: str
    candidates: int = 0
    resolved: int = 0
    skipped_fresh: int = 0
    #: How many candidates hold write access *now* -- not how many this pass happened to
    #: resolve. A pass that skipped everyone as fresh is healthy, and must not report
    #: "0 of 3 have write access", which reads exactly like the failure it is not.
    authoritative: int = 0
    sources: Counter[str] = field(default_factory=Counter)
    endpoint_available: bool = True
    promoted: set[str] = field(default_factory=set)
    rederived: int = 0
    documents_written: int = 0


def resolve_authority(
    engine: Engine,
    repo: str,
    *,
    client: GitHubClient | None = None,
    refresh_after: dt.timedelta = REFRESH_AFTER,
    now: dt.datetime | None = None,
) -> AuthorityResult:
    """Resolve every unresolved ``MEMBER`` author, then re-derive what moved.

    ``client`` is optional on purpose: the ``merged_by`` fallback is local, so a
    deployment whose token cannot reach the permission endpoint -- or an operator who
    would rather not spend the requests -- still gets the maintainers who have merged
    something, which on most repositories is the same set.
    """
    moment = now or utcnow()
    result = AuthorityResult(repo=repo)

    with engine.connect() as conn:
        candidates = repo_layer.member_authors(conn, repo)
        checked = repo_layer.authority_checked_at(conn, repo)
        merged_by = repo_layer.merged_by_logins(conn, repo)
        before = repo_layer.get_authority(conn, repo)

    result.candidates = len(candidates)
    rows = []
    for login in candidates:
        seen = checked.get(login)
        if seen is not None and moment - seen < refresh_after:
            result.skipped_fresh += 1
            continue

        permission, source = _resolve_one(client, repo, login, merged_by, result)
        rows.append(
            {
                "repo": repo,
                "login": login,
                "permission": permission,
                "source": source,
                "checked_at": moment,
            }
        )
        result.resolved += 1
        result.sources[source] += 1
        if permission in WRITE_PERMISSIONS and before.get(login) not in WRITE_PERMISSIONS:
            result.promoted.add(login)

    with engine.begin() as conn:
        repo_layer.upsert_authority(conn, rows)

    with engine.connect() as conn:
        after = repo_layer.get_authority(conn, repo)
    result.authoritative = sum(1 for login in candidates if after.get(login) in WRITE_PERMISSIONS)

    # Only threads whose tier actually moved need rewriting. Everything else would
    # reconcile to "unchanged" anyway; not asking is just faster.
    changed = {row["login"] for row in rows if before.get(row["login"]) != row["permission"]}
    result.rederived, result.documents_written = _rederive(engine, repo, changed)
    return result


def _resolve_one(
    client: GitHubClient | None,
    repo: str,
    login: str,
    merged_by: set[str],
    result: AuthorityResult,
) -> tuple[str | None, str]:
    """Section 6.2's ladder: the permission API, then ``merged_by``, then unresolved.

    The endpoint needs push access on the repository, and a token without it fails the
    same way for every login -- so the first failure switches the whole pass to the
    fallback rather than spending one doomed request per author.
    """
    if client is not None and result.endpoint_available:
        try:
            payload = client.get_json(f"/repos/{repo}/collaborators/{login}/permission")
        except GitHubError as exc:
            if _is_missing(exc):
                return "none", "permission_api"
            result.endpoint_available = False
            log.warning(
                "%s: the collaborator-permission endpoint is unavailable (%s); "
                "falling back to merged_by for the rest of this pass",
                repo,
                exc,
            )
        else:
            return payload.get("permission"), "permission_api"

    if login in merged_by:
        # Merging is the write act itself, so a positive answer here is definitive.
        return "write", "merged_by"

    # Fail closed (section 6.2): a maintainer demoted to a claim costs one lost result,
    # a stranger promoted to authority costs a wrong patch. Recorded rather than left
    # absent so the next pass knows this login was already asked about.
    return None, "association"


def _is_missing(exc: GitHubError) -> bool:
    """A 404 means "not a collaborator", which is an answer, not a failure."""
    return str(exc).startswith("404")


def _rederive(engine: Engine, repo: str, changed: set[str]) -> tuple[int, int]:
    from relore.ingest.index_thread import derive_thread

    with engine.connect() as conn:
        numbers = repo_layer.threads_authored_by(conn, repo, changed)
    written = 0
    for number in numbers:
        with engine.begin() as conn:
            written += derive_thread(conn, repo, number).wrote
    return len(numbers), written

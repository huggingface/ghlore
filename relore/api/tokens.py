"""Bearer tokens, repository scope, and the two rate limits (section 7, section 11).

A token's scope is resolved into a **concrete list of repository names** here, at the
edge, and nothing below this module ever sees a wildcard. That is deliberate: section 11
applies scoping in the query layer so a new endpoint cannot forget it, and a query layer
that has to interpret ``*`` is a query layer with a way to get it wrong.

Configuration is one environment variable, because the deployment shape is intentionally
unspecified (section 14.5) and a token list is a secret::

    RELORE_API_TOKENS="tok_agent:owner/name,owner/other tok_ui:*:label"

Entries are separated by whitespace or newlines; each is ``secret:repos[:scopes]``.
``repos`` is a comma-separated list or ``*`` for every indexed repository; ``scopes`` is a
comma-separated list of extra permissions, of which there is currently one -- ``label``,
for section 8's benchmark labelling. Repository names contain no colon, so the grammar is
unambiguous. ``RELORE_API_TOKENS_FILE`` points at a file with the same content, for a
deployment that mounts secrets rather than exporting them.

**No tokens configured means no authentication**, which is allowed on a loopback bind, or
on a wider one only when ``relored serve --trust-network`` says the network in front of the
process is the perimeter -- see :func:`relore.api.server.serve`. A laptop should not have to
mint a token to look at its own index; a daemon on a *public* network must.

One consequence worth stating rather than discovering: :meth:`Authenticator.authenticate`
grants the anonymous caller :data:`LABEL_SCOPE`, so an open daemon with
``RELORE_LABELS_PATH`` set accepts section 8's labelling from anyone who can reach it.
Nothing retrieves those labels (section 11's actual property), so the exposure is a
polluted evaluation set rather than a poisoned index -- but it is the one thing an open
bind widens beyond reads.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

TOKENS_ENV = "RELORE_API_TOKENS"
TOKENS_FILE_ENV = "RELORE_API_TOKENS_FILE"
RATE_PER_MINUTE_ENV = "RELORE_API_RATE_PER_MINUTE"
RATE_PER_DAY_ENV = "RELORE_API_RATE_PER_DAY"

#: Generous on purpose: an agent searching once per turn is normal, not abusive
#: (section 7). These exist to bound a runaway loop, not to police ordinary use.
DEFAULT_PER_MINUTE = 60
DEFAULT_PER_DAY = 5000

#: The one scope beyond read. Off unless a token asks for it, because it is the only
#: request in the whole API that writes anything (section 8's labelling, which lands in a
#: JSONL file and never in the index -- see :mod:`relore.api.server`).
LABEL_SCOPE = "label"

ANONYMOUS = "anonymous"


class AuthError(Exception):
    """401. Kept distinct from :class:`RateLimited` so the handler cannot conflate them."""


class RateLimited(Exception):
    """429, and it says which limit was hit -- "slow down" and "come back tomorrow" call
    for different behaviour from the caller (section 7)."""

    def __init__(self, which: str, limit: int, retry_after: int) -> None:
        super().__init__(f"{which} rate limit of {limit} reached; retry in {retry_after}s")
        self.which = which
        self.limit = limit
        self.retry_after = retry_after


@dataclass(frozen=True)
class Token:
    name: str
    secret: str
    #: ``None`` means every indexed repository, resolved to names by :meth:`scope`.
    repos: tuple[str, ...] | None
    scopes: frozenset[str] = frozenset()

    def scope(self, indexed: tuple[str, ...]) -> tuple[str, ...]:
        """The concrete repositories this token may read.

        A configured name that is not indexed stays in the scope: it is an authorization
        statement, not a claim about what exists, and dropping it would silently change
        the token's meaning the moment a repository is added.
        """
        return indexed if self.repos is None else self.repos

    def may(self, scope: str) -> bool:
        return scope in self.scopes


def load_tokens(env: dict[str, str] | None = None) -> tuple[Token, ...]:
    env = dict(os.environ if env is None else env)
    raw = env.get(TOKENS_ENV, "")
    path = env.get(TOKENS_FILE_ENV)
    if path:
        with open(path) as handle:
            raw = f"{raw}\n{handle.read()}"
    return parse_tokens(raw)


def parse_tokens(raw: str) -> tuple[Token, ...]:
    out: list[Token] = []
    for index, entry in enumerate(raw.split(), start=1):
        secret, _, rest = entry.partition(":")
        if not secret or not rest:
            raise ValueError(
                f"{TOKENS_ENV} entry {index} is not secret:repos[:scopes] "
                "(use '*' for every indexed repository)"
            )
        repo_spec, _, scope_spec = rest.partition(":")
        repos = (
            None
            if repo_spec.strip() == "*"
            else tuple(r.strip() for r in repo_spec.split(",") if r.strip())
        )
        if repos is not None and not repos:
            raise ValueError(f"{TOKENS_ENV} entry {index} lists no repositories")
        out.append(
            Token(
                name=f"token{index}",
                secret=secret,
                repos=repos,
                scopes=frozenset(s.strip() for s in scope_spec.split(",") if s.strip()),
            )
        )
    return tuple(out)


@dataclass
class Authenticator:
    """Token lookup plus the per-token limits. One process, one instance.

    In-process state, because the daemon is one writer and one server by design
    (section 4.1). A second replica would need a shared counter; that is a deployment
    decision (section 14.5), and pretending otherwise here would be a limit that silently
    doubles.
    """

    tokens: tuple[Token, ...] = ()
    per_minute: int = DEFAULT_PER_MINUTE
    per_day: int = DEFAULT_PER_DAY
    _recent: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    _daily: dict[str, tuple[dt.date, int]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Authenticator:
        source = dict(os.environ if env is None else env)
        return cls(
            tokens=load_tokens(source),
            per_minute=int(source.get(RATE_PER_MINUTE_ENV, DEFAULT_PER_MINUTE)),
            per_day=int(source.get(RATE_PER_DAY_ENV, DEFAULT_PER_DAY)),
        )

    @property
    def open(self) -> bool:
        """True when no token is configured, i.e. anyone may read."""
        return not self.tokens

    def authenticate(self, header: str | None) -> Token:
        """Resolve an ``Authorization`` header to a token, or raise.

        Compared with :func:`secrets.compare_digest` rather than by dict lookup, and every
        configured token is compared even after a match, so the time taken says nothing
        about which secrets exist.
        """
        if self.open:
            return Token(name=ANONYMOUS, secret="", repos=None, scopes=frozenset({LABEL_SCOPE}))
        presented = _bearer(header)
        matched: Token | None = None
        for token in self.tokens:
            if secrets.compare_digest(token.secret, presented):
                matched = token
        if matched is None:
            raise AuthError("unknown or missing bearer token")
        return matched

    def charge(self, token: Token, *, now: float | None = None) -> None:
        """Count one request against both limits, or raise :class:`RateLimited`."""
        now = time.time() if now is None else now
        today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()

        date, count = self._daily.get(token.name, (today, 0))
        if date != today:
            date, count = today, 0
        if count >= self.per_day:
            midnight = dt.datetime.combine(
                today + dt.timedelta(days=1), dt.time.min, dt.timezone.utc
            )
            raise RateLimited("daily", self.per_day, int(midnight.timestamp() - now))

        window = self._recent.setdefault(token.name, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self.per_minute:
            raise RateLimited("per-minute", self.per_minute, int(61 - (now - window[0])))

        window.append(now)
        self._daily[token.name] = (date, count + 1)

    def usage(self, token: Token) -> dict[str, Any]:
        window = self._recent.get(token.name, deque())
        _date, count = self._daily.get(token.name, (None, 0))
        return {
            "per_minute": {"used": len(window), "limit": self.per_minute},
            "daily": {"used": count, "limit": self.per_day},
        }


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""

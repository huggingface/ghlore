"""``ghlore`` -- the read-only client.

Two verb families, deliberately in one binary:

* **history verbs** (``search``, ``thread``, ``inflight``, ``precedent``, ``why``,
  ``status``) talk to a
  ``ghlored`` over HTTP. They need ``GHLORE_API``, a daemon of **this same version**
  (:mod:`ghlore.wire` -- the two ship together and refuse to talk across a difference)
  and, if that daemon requires one, a token in ``GHLORE_TOKEN``.
* **code verbs** (``map``, ``defs``, ``refs``) run locally against the working tree and
  never touch the network, a database, or a token. ``defs`` and ``refs`` take ``--repo``
  to ask the daemon's working clone instead, and ``symbol``, ``grep`` and ``copies``
  exist only there (issue #7).

This module must not import :mod:`ghlore.store`, :mod:`ghlore.github` or
:mod:`ghlore.api`. That is asserted by ``tests/unit/test_module_boundary.py`` and it is
what makes "an agent cannot write to the index" a property of the build. See AGENTS.md.
It reaches :mod:`ghlore.render` and :mod:`ghlore.security.untrusted`, which are pure
functions over dictionaries and text -- one renderer, so what an agent reads here and what
a person inspects in the web UI cannot drift.

**No results is exit 0 with an empty result**, always: an agent must not be able to
mistake "nothing in the index" for "the tool is broken". A *transport* failure is a
different thing and does exit non-zero, with a sentence saying which.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit

from ghlore import __version__
from ghlore.code.api import MissingParser
from ghlore.render import (
    render_inflight,
    render_search,
    render_status,
    render_thread,
    render_why,
)
from ghlore.wire import CLIENT_HEADER, SERVER_HEADER, UPGRADE_REQUIRED, explain

API_ENV = "GHLORE_API"
TOKEN_ENVS = ("GHLORE_TOKEN", "GHLORE_API_TOKEN")
# The deployment. This was `http://localhost:8080` for a day and the reason is worth
# keeping, because it is the condition this default is really coupled to: the name existed
# and did not *resolve* -- no certificate, so the ALB controller built no load balancer, so
# no DNS record -- and a default nobody can reach is worse than one that is merely often
# wrong. A connection refused on localhost tells you to start a daemon; a DNS failure on a
# hostname you never typed tells you nothing you can act on.
#
# It resolves now (2026-09-11): the certificate landed, the controller built the load
# balancer and auto-discovered the cert, and `external-dns` created the alias once its
# domain filter included the name. The load balancer is **internal**, so the failure mode
# off the VPN is a timeout against a private address rather than a DNS error -- which is
# actionable only if the message says so, and :func:`_call` does.
DEFAULT_API = "https://ghlore.huggingface.tech"

_MILESTONE = {"precedent": 4, "map": 2, "defs": 2, "refs": 2}


def _also_after_the_verb(parser: argparse.ArgumentParser, *flags: str) -> None:
    """Accept a global flag after the subcommand as well as before it.

    ``ghlore search ... --json`` is what anyone composing a command by analogy with
    ``git`` and ``gh`` writes, and argparse's answer to it was ``unrecognized arguments:
    --json`` -- which names the flag as unknown rather than misplaced, so the reader looks
    for a typo instead of moving it. Accepting it in both positions is cheaper than
    teaching every caller our own convention.

    ``SUPPRESS`` is what makes the alias harmless: without it the subparser would write its
    own default over a flag given *before* the verb, silently turning ``ghlore --json
    search`` back off. Hidden from the subcommand's help, because it is already in the
    program's.
    """
    for flag in flags:
        parser.add_argument(
            flag, action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ghlore", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"ghlore {__version__}")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--compact", action="store_true", help="trim snippets for a tight context budget"
    )
    p.add_argument(
        "--plain",
        action="store_true",
        help="the piped form even on a terminal: facts only, no suggestions (#13)",
    )
    p.add_argument(
        "--api",
        default=None,
        help=f"the ghlored base URL (default: {API_ENV}, else {DEFAULT_API})",
    )
    sub = p.add_subparsers(dest="verb", required=True)

    s = sub.add_parser("search", help="search the indexed issue/PR history")
    s.add_argument("query", nargs="?", default="")
    # Repeatable, because the API takes a list of each: a traceback names more than one
    # file, and section 6's expansion will fan a single call out over all of them.
    for flag, help_text in (
        ("--error", "an exception line or normalized message"),
        ("--test", "a test id"),
        ("--file", "a repository path"),
        ("--symbol", "a function or class name"),
        ("--label", "a GitHub label"),
    ):
        s.add_argument(flag, action="append", default=[], help=f"{help_text} (repeatable)")
    s.add_argument("--kind", help="failure | precedent | rationale")
    s.add_argument(
        "--trust",
        help="raise the floor to 'authoritative', or 'machine' to ask what our own bots said",
    )
    s.add_argument("--since", help="ISO timestamp; only documents written after it")
    # Narrows the token's scope, never widens it -- see the server. Repeatable, so it
    # reads like the other filters even though one name is the usual case.
    s.add_argument(
        "--repo",
        action="append",
        default=[],
        dest="repos",
        help="OWNER/NAME; narrow to this repository (repeatable)",
    )
    # Spelled out rather than imported from `search.queries`, like `--kind` above: reaching
    # that module executes `ghlore/search/__init__.py`, which imports SQLAlchemy, and
    # `test_module_boundary` exists to catch exactly that. The server validates the value.
    s.add_argument(
        "--sort",
        choices=("relevance", "newest"),
        default="relevance",
        help="'newest' orders the same hits by date instead of score",
    )
    s.add_argument("--limit", type=int, default=10)
    s.add_argument(
        "--no-expand",
        dest="expand",
        action="store_false",
        help="ask exactly one question instead of fanning out over the query's parts",
    )
    _also_after_the_verb(s, "--json", "--compact")

    t = sub.add_parser("thread", help="one thread, comments ranked by relevance")
    t.add_argument("number", type=int)
    t.add_argument("--focus", default="", help="rank the comments by this, best first")
    t.add_argument("--repo", help="OWNER/NAME; needed when the token can see several")
    t.add_argument(
        "--full",
        action="store_true",
        help="serve the opening post whole instead of its first 800 characters",
    )
    _also_after_the_verb(t, "--json", "--compact")

    inflight = sub.add_parser(
        "inflight",
        help="open pull requests that already claim to close this issue",
    )
    inflight.add_argument("number", type=int)
    inflight.add_argument("--repo", help="OWNER/NAME; needed when the token can see several")
    _also_after_the_verb(inflight, "--json")

    pr = sub.add_parser(
        "precedent",
        help="(NOT IMPLEMENTED YET) completed units of work and what they consisted of",
    )
    pr.add_argument("--kind")
    pr.add_argument("--file")
    # Every other listing verb is limitable, so this one refusing `--limit` is a papercut
    # rather than a decision.
    pr.add_argument("--limit", type=int, default=5)
    _also_after_the_verb(pr, "--json", "--compact")

    w = sub.add_parser(
        "why",
        help="the pull request that last changed this line, and what reviewers said on it",
    )
    w.add_argument("location", metavar="PATH:LINE")
    w.add_argument("--repo", help="OWNER/NAME; needed when the token can see several")
    _also_after_the_verb(w, "--json")

    st = sub.add_parser("status", help="index freshness and coverage")
    _also_after_the_verb(st, "--json")

    m = sub.add_parser(
        "map",
        help=(
            "ranked repo map of the local checkout: name matches per definition, dunders "
            "excluded (no server)"
        ),
    )
    m.add_argument("--limit", type=int, default=40)
    m.add_argument("path", nargs="?", default=".")
    _also_after_the_verb(m, "--json")

    d = sub.add_parser("defs", help="definitions in a file, locally or with --repo")
    d.add_argument("path")
    d.add_argument("--repo", help="OWNER/NAME; ask the daemon's working clone instead")
    _also_after_the_verb(d, "--json")

    r = sub.add_parser(
        "refs",
        help=(
            "every occurrence of a symbol, by kind: call, definition, attribute, name. "
            "Local unless --repo"
        ),
    )
    r.add_argument("symbol")
    r.add_argument("--repo", help="OWNER/NAME; ask the daemon's working clone instead")
    _also_after_the_verb(r, "--json")

    sym = sub.add_parser("symbol", help="the source of one definition, from the daemon's clone")
    sym.add_argument("qualname")
    sym.add_argument("--repo", help="OWNER/NAME; needed when the token can see several")
    _also_after_the_verb(sym, "--json")

    g = sub.add_parser("grep", help="a regular expression over the daemon's working clone")
    g.add_argument("pattern")
    g.add_argument("--repo", help="OWNER/NAME; needed when the token can see several")
    g.add_argument("--path", help="glob the paths must match, e.g. 'src/**/modeling_*.py'")
    _also_after_the_verb(g, "--json")

    c = sub.add_parser(
        "copies",
        help="every definition of a symbol, grouped by whether the bodies agree",
    )
    c.add_argument("symbol")
    c.add_argument("--repo", help="OWNER/NAME; needed when the token can see several")
    _also_after_the_verb(c, "--json")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "search": _search,
        "thread": _thread,
        "inflight": _inflight,
        "why": _why,
        "status": _status,
        "map": _map,
        "defs": _defs,
        "refs": _refs,
        "symbol": _symbol,
        "grep": _grep,
        "copies": _copies,
    }.get(args.verb)
    if handler is None:
        raise SystemExit(
            f"ghlore {args.verb}: not implemented yet. It is planned "
            f"(milestone {_MILESTONE[args.verb]}) and `--help` marks it, so nothing else "
            "in your plan depends on it."
        )
    try:
        return handler(args)
    except MissingParser as exc:
        # An install, not a bug -- so a sentence, never an ImportError traceback (AGENTS.md,
        # section 2). Raised by the registry rather than by an eager `import tree_sitter`
        # check here, because a machine with universal-ctags and no grammar can still
        # answer, and refusing it would be wrong.
        raise SystemExit(f"ghlore: {exc}") from None


# -- history verbs ---------------------------------------------------------


def _search(args: argparse.Namespace) -> int:
    payload = _call(
        args,
        "POST",
        "/api/v1/search",
        body={
            "query": args.query,
            "kind": args.kind,
            "trust": args.trust,
            "files": args.file,
            "symbols": args.symbol,
            "errors": args.error,
            "tests": args.test,
            "labels": args.label,
            "repos": args.repos,
            "since": args.since,
            "limit": args.limit,
            "compact": args.compact,
            "sort": args.sort,
            "expand": args.expand,
            # The server renders it, so the envelope a person inspects in the web UI and
            # the one an agent reads here are the same string from the same code.
            "render": not args.json,
            "presentation": _presentation(args),
        },
    )
    return _emit(
        args,
        payload,
        lambda: (
            payload.get("rendered")
            or render_search(payload, compact=args.compact, presentation=_presentation(args))
        ),
    )


def _thread(args: argparse.Namespace) -> int:
    query = {
        "focus": args.focus,
        "compact": str(args.compact).lower(),
        "render": str(not args.json).lower(),
        "presentation": str(_presentation(args)).lower(),
        "full": str(args.full).lower(),
    }
    if args.repo:
        query["repo"] = args.repo
    payload = _call(args, "GET", f"/api/v1/thread/{args.number}", params=query)
    return _emit(
        args,
        payload,
        lambda: (
            payload.get("rendered")
            or render_thread(payload, compact=args.compact, presentation=_presentation(args))
        ),
    )


def _inflight(args: argparse.Namespace) -> int:
    """Before diagnosing, ask whether somebody is already fixing it.

    Cheap, one hop, and it prevents the most expensive mistake an agent makes -- see
    :mod:`ghlore.ingest.relationships`.
    """
    query = {
        "render": str(not args.json).lower(),
        "presentation": str(_presentation(args)).lower(),
    }
    if args.repo:
        query["repo"] = args.repo
    payload = _call(args, "GET", f"/api/v1/inflight/{args.number}", params=query)
    return _emit(
        args,
        payload,
        lambda: (
            payload.get("rendered") or render_inflight(payload, presentation=_presentation(args))
        ),
    )


def _why(args: argparse.Namespace) -> int:
    """`git blame` gives the commit; this gives the argument (issue #9)."""
    path, _, line = args.location.rpartition(":")
    if not path or not line.isdigit():
        raise SystemExit(f"ghlore why: expected PATH:LINE, got {args.location!r}")
    params = {
        "path": path,
        "line": line,
        "render": str(not args.json).lower(),
        "presentation": str(_presentation(args)).lower(),
    }
    if args.repo:
        params["repo"] = args.repo
    payload = _call(args, "GET", "/api/v1/why", params=params)
    return _emit(
        args,
        payload,
        lambda: payload.get("rendered") or render_why(payload, presentation=_presentation(args)),
    )


def _status(args: argparse.Namespace) -> int:
    payload = _call(args, "GET", "/api/v1/status")
    return _emit(args, payload, lambda: render_status(payload))


def _presentation(args: argparse.Namespace) -> bool:
    """Whether a person is reading this (#13).

    The `git`/`gh` convention: a terminal gets the flags worth trying next, a pipe gets the
    documented grammar and nothing else. `--plain` forces the pipe form for a script that
    happens to own a TTY.
    """
    return sys.stdout.isatty() and not getattr(args, "plain", False)


def _emit(args: argparse.Namespace, payload: dict[str, Any], text) -> int:
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(text())
    return 0


# -- code verbs: local, offline, no token ----------------------------------
#
# Deliberately local (section 1). The whole justification for a daemon is that
# `/search/issues` is 30 requests/minute shared across the token; that constraint does not
# exist for code -- a parser over the working tree costs nothing, needs no token, and
# describes *the tree the caller is actually on*, dirty files included. A server-side code
# index knows `main`; the agent is on a feature branch mid-edit.


def _map(args: argparse.Namespace) -> int:
    from ghlore.code.repomap import repo_map

    result = repo_map(args.path, limit=args.limit)
    if args.json:
        print(
            json.dumps(
                {
                    "files": result.files,
                    "definitions": result.definitions,
                    "ranked_by": result.ranked_by,
                    "excluded_dunders": result.excluded_dunders,
                    "entries": [vars(e) for e in result.entries],
                },
                indent=2,
            )
        )
        return 0
    print(
        f"{result.definitions} definitions in {result.files} files, "
        f"top {len(result.entries)} by {result.ranked_by}"
        + (f" ({result.excluded_dunders} dunders excluded)" if result.excluded_dunders else "")
    )
    print(
        "  (a count of the written *name*: same-named definitions share it, so the "
        "definition count is how much this row overstates)"
    )
    for entry in result.entries:
        shared = f"{entry.shared_by} definition" + ("s" if entry.shared_by > 1 else "")
        weight = f"{entry.matches} matches / {shared}" if entry.matches else "-"
        print(f"  {entry.qualname:44} {entry.kind:9} {weight:28} {entry.path}:{entry.line}")
    return 0


def _code_params(args: argparse.Namespace, **extra: Any) -> dict[str, str]:
    params = {key: value for key, value in extra.items() if value}
    if args.repo:
        params["repo"] = args.repo
    return params


def _defs(args: argparse.Namespace) -> int:
    from ghlore.code.defs import definitions
    from ghlore.code.walk import read

    if args.repo:
        payload = _call(args, "GET", "/api/v1/code/defs", params=_code_params(args, path=args.path))
        return _emit(args, payload, lambda: _defs_text(payload["definitions"]))

    source = read(args.path)
    if source is None:
        raise SystemExit(f"ghlore: cannot read {args.path}")
    found = definitions(args.path, source)
    if args.json:
        print(json.dumps([vars(d) for d in found], indent=2))
        return 0
    print(_defs_text([vars(d) for d in found]))
    return 0


def _defs_text(found: list[dict[str, Any]]) -> str:
    lines = []
    for definition in found:
        end = definition.get("end_line")
        extent = f"{definition['start_line']}-{end}" if end else str(definition["start_line"])
        lines.append(f"{extent:12} {definition['kind']:9} {definition['qualname']}")
    return "\n".join(lines)


def _refs(args: argparse.Namespace) -> int:
    from ghlore.code.refs import references

    if args.repo:
        payload = _call(
            args, "GET", "/api/v1/code/refs", params=_code_params(args, symbol=args.symbol)
        )
        return _emit(args, payload, lambda: _refs_text(payload))

    result = references(".", args.symbol)
    if args.json:
        print(
            json.dumps(
                {
                    "searched": result.searched,
                    "unsupported": list(result.unsupported),
                    "by_kind": result.by_kind,
                    "hits": [vars(h) for h in result.hits],
                },
                indent=2,
            )
        )
        return 0
    for hit in result.hits:
        print(f"{hit.path}:{hit.line}  {hit.kind}")
    counts = ", ".join(f"{count} {kind}" for kind, count in result.by_kind.items())
    print(
        f"-- {len(result.hits)} references in {result.searched} files"
        + (f" ({counts})" if counts else "")
    )
    if result.unsupported:
        # Section 9's rule 2: say so and exit 0, rather than return an empty list a caller
        # would read as "nothing calls this".
        print(
            f"   ({', '.join(result.unsupported)} offers no reference tier, so files it "
            "claims were not searched)"
        )
    return 0


def _refs_text(payload: dict[str, Any]) -> str:
    hits = payload.get("hits") or []
    lines = [f"{hit['path']}:{hit['line']}  {hit['kind']}" for hit in hits]
    counts = ", ".join(f"{count} {kind}" for kind, count in (payload.get("by_kind") or {}).items())
    lines.append(
        f"-- {len(hits)} references at {payload.get('head', '')[:8]}"
        + (f" ({counts})" if counts else "")
    )
    return "\n".join(lines)


def _symbol(args: argparse.Namespace) -> int:
    """The 18 lines that matter, without a clone (#7 item 2)."""
    payload = _call(
        args, "GET", "/api/v1/code/symbol", params=_code_params(args, qualname=args.qualname)
    )
    return _emit(args, payload, lambda: _symbol_text(payload))


def _symbol_text(payload: dict[str, Any]) -> str:
    head = f"{payload['path']}:{payload['start_line']}  {payload['kind']}  {payload['qualname']}"
    total = payload.get("definitions_total", 1)
    if total > 1:
        # Serving one of many as *the* body is a wrong answer a caller cannot see.
        head += f"\n({total} definitions of this name; `ghlore copies` groups them)"
    return f"{head}\n{payload['body']}"


def _grep(args: argparse.Namespace) -> int:
    payload = _call(
        args,
        "GET",
        "/api/v1/code/grep",
        params=_code_params(args, pattern=args.pattern, path=args.path),
    )
    return _emit(args, payload, lambda: _grep_text(payload))


def _grep_text(payload: dict[str, Any]) -> str:
    hits = payload.get("hits") or []
    lines = [f"{hit['path']}:{hit['line']}  {hit['text']}" for hit in hits]
    lines.append(f"-- {len(hits)} in {payload.get('files_searched', 0)} files")
    if payload.get("truncated"):
        lines.append("   (capped: narrow it with --path)")
    return "\n".join(lines)


def _copies(args: argparse.Namespace) -> int:
    """Which copies diverge -- the question a repository that duplicates code on purpose
    actually asks (#7 item 4)."""
    payload = _call(
        args, "GET", "/api/v1/code/copies", params=_code_params(args, symbol=args.symbol)
    )
    return _emit(args, payload, lambda: _copies_text(payload))


def _copies_text(payload: dict[str, Any]) -> str:
    groups = payload.get("groups") or []
    lines = [
        f"{payload.get('total', 0)} definitions of {payload.get('symbol')} "
        f"in {len(groups)} shape{'' if len(groups) == 1 else 's'}"
    ]
    for index, group in enumerate(groups, start=1):
        note = " (the majority shape)" if index == 1 and len(groups) > 1 else ""
        lines.append(f"\n-- shape {index}: {group['count']} copies{note}")
        lines += [f"   {copy['path']}:{copy['start_line']}" for copy in group["copies"]]
    if payload.get("truncated"):
        lines.append("\n(capped.)")
    return "\n".join(lines)


# -- transport -------------------------------------------------------------


def _call(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One request, with the failure modes told apart.

    A 404 or an empty index is data; a refused connection, a 401, a 426 and a 429 are not,
    and each gets its own sentence. Blurring them is how an agent decides the project has
    no history when the daemon is simply down.
    """
    import httpx

    base = args.api or os.environ.get(API_ENV) or DEFAULT_API
    headers = {"accept": "application/json", CLIENT_HEADER: __version__}
    # The *name* too, not just the value: a 401 has to say which variable was rejected,
    # and there are two it could have come from.
    sent_from = next((e for e in TOKEN_ENVS if os.environ.get(e)), None)
    if sent_from:
        headers["authorization"] = f"Bearer {os.environ[sent_from]}"

    try:
        response = httpx.request(
            method,
            f"{base.rstrip('/')}{path}",
            json=body,
            params=params,
            headers=headers,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise SystemExit(f"ghlore: {_unreachable(base, exc)}") from None

    if response.status_code == UPGRADE_REQUIRED:
        # The daemon caught the version difference. It composed the sentence, because it
        # knows both numbers -- print that rather than a second opinion.
        raise SystemExit(f"ghlore: {_detail(response)}")

    # The other direction: a daemon older than the handshake enforces nothing, so the
    # client is the only end that can catch "the deployment is behind". Checked before
    # the 401 and the 429 so a version problem is never reported as a credential problem,
    # which is the same ordering the daemon's own gate uses.
    served_by = response.headers.get(SERVER_HEADER)
    if served_by is None:
        raise SystemExit(
            f"ghlore: {base} answered without a {SERVER_HEADER} header, so it is not a "
            f"ghlore daemon of this generation — it predates the version handshake, or "
            f"something else is answering on that address. This client is {__version__}."
        )
    mismatch = explain(__version__, served_by)
    if mismatch is not None:
        raise SystemExit(f"ghlore: {mismatch}")

    if response.status_code == 401:
        # Two failures, not one. Telling an operator who has already exported a token to
        # export a token sends them looking for a typo in the value, when the usual cause
        # is that the value is fine and belongs to a different daemon.
        if sent_from:
            raise SystemExit(
                f"ghlore: {base} rejected the token in {sent_from}. A token is only valid "
                f"on the daemon whose GHLORE_API_TOKENS lists it, so one minted for "
                f"another deployment will not work here."
            )
        raise SystemExit(
            f"ghlore: {base} requires a token. Set {TOKEN_ENVS[0]} to one scoped to your "
            "repositories."
        )
    if response.status_code == 429:
        detail = _detail(response)
        raise SystemExit(f"ghlore: rate limited ({detail}). Retry after the header says to.")
    if response.status_code >= 400:
        raise SystemExit(
            f"ghlore: {base}{path} returned {response.status_code}: {_detail(response)}"
        )
    return dict(response.json())


def _unreachable(base: str, exc: Exception) -> str:
    """Why nothing answered, told apart by *which* address did not answer.

    Two situations wear the same symptom and take opposite next actions. The deployment
    sits behind an **internal** load balancer, so from outside the VPN it is a timeout
    against a private address and looks exactly like a daemon that is down. A loopback
    address is the other one: nothing is listening because the caller pointed
    ``GHLORE_API`` at a daemon they have not started -- and a client-only install cannot
    start one, which is what turned a good error message into a dead end
    (huggingface/ghlore#19). So the remedy offered names the extra, and names the way out
    that needs no daemon at all.
    """
    why = f"cannot reach {base} ({exc.__class__.__name__})"
    if _is_loopback(base):
        return (
            f"{why}. That is a local address, so nothing is listening on it: {API_ENV} "
            f"points at a daemon you have not started. Start one -- `ghlored serve` needs "
            f"`pip install 'ghlore[server]'`, which the client install does not carry -- "
            f"or unset {API_ENV} to use the deployment at {DEFAULT_API}."
        )
    return (
        f"{why}. The deployment is VPN-internal — check the VPN first. Otherwise point "
        f"{API_ENV} at your own `ghlored serve`, or port-forward the deployment: "
        f"`kubectl -n ghlore port-forward deploy/ghlore 8080:8080`."
    )


def _is_loopback(base: str) -> bool:
    host = urlsplit(base).hostname or ""
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return detail if isinstance(detail, str) else json.dumps(detail)


if __name__ == "__main__":
    sys.exit(main())

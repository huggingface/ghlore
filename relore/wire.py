"""The client/server version handshake.

``relore`` and ``relored`` are one repository, one version and one wire format. They are
*installed* separately, though -- the daemon is a pinned image, the client is a
``pip install`` inside whatever sandbox an agent happens to be running in -- so nothing
about the deployment keeps the two in step. What that produced before this module existed
is the failure this project is least equipped to notice: an old client asks a new daemon,
every field it knows about is still there, and the answer is well-formed, plausible and
missing whatever the version it does not have would have added. Section 13.3's whole
lesson is that *silently incomplete output* is the shape of every defect that got through.

So the version is checked rather than displayed. Every request to ``/api/v1`` declares its
client version in :data:`CLIENT_HEADER`; a daemon that reads anything but its own answers
**426 Upgrade Required** and says which side is behind. Every response carries
:data:`SERVER_HEADER`, so a client talking to a daemon too old to enforce the rule catches
the same mismatch from the other end.

The match is exact, and both directions are an error. "Client older" means an agent is
reading a stale contract; "client newer" means the deployment is behind and the answer
would be missing whatever the client was installed for. Neither is something to warn about
and continue -- the point of the handshake is that a wrong answer never reaches a caller
that cannot tell it is wrong.

Two consequences worth stating, because they are the price:

* the version in :mod:`relore` is the compatibility contract, so it is bumped in the same
  commit as any change a client can see, and
* the bump and the deploy are one operation. Bumping on ``main`` without shipping the
  image breaks every client installed after the merge, and the error tells the operator
  exactly that (see :func:`explain`).

Stdlib only, and it imports nothing from :mod:`relore.api` or :mod:`relore.store`: both
binaries import this one, and the client's import graph is a security boundary
(``tests/unit/test_module_boundary.py``, AGENTS.md invariant 1).
"""

from __future__ import annotations

from relore import __version__

#: Sent by the client on every ``/api/v1`` request. A request without it is refused: a
#: client too old to send one is exactly the client this exists to keep out.
CLIENT_HEADER = "x-relore-client"

#: Returned on every response, including the refusals, so the client can name both
#: versions in its own error and so ``curl -I`` answers "which version is deployed".
SERVER_HEADER = "x-relore-version"

#: 426 Upgrade Required: "switch to a different version of this protocol". Distinct from
#: 400/401/429, which the client already tells apart and treats differently.
UPGRADE_REQUIRED = 426

INSTALL = "pip install --upgrade 'relore @ git+https://github.com/huggingface/relore'"


def _parts(version: str) -> tuple[int, ...] | None:
    """``"0.3.1"`` -> ``(0, 3, 1)``; anything else -> ``None``, which reads as "cannot
    say which is older" and produces the undirected message."""
    bits = version.split(".")
    if not bits or not all(bit.isdigit() for bit in bits):
        return None
    return tuple(int(bit) for bit in bits)


def explain(client: str | None, server: str = __version__) -> str | None:
    """``None`` when the two match; otherwise one sentence naming both and what to do.

    Written once and used from both ends: the daemon puts it in the 426's ``detail`` and
    the client prints it, so the operator reads the same sentence wherever the mismatch was
    caught first.
    """
    if client == server:
        return None
    if not client:
        return (
            f"this relore daemon is {server} and the request did not say which client "
            f"version it came from. Upgrade the client ({INSTALL}), or send "
            f"{CLIENT_HEADER}: {server} if you are calling the API by hand."
        )
    mine, theirs = _parts(client), _parts(server)
    if mine is None or theirs is None or mine == theirs:
        return (
            f"client {client} does not match this relore daemon ({server}); the two ship "
            f"together and must be the same version. {INSTALL}"
        )
    if mine < theirs:
        return (
            f"client {client} is older than this relore daemon ({server}), so it would "
            f"read an out-of-date answer as a complete one. {INSTALL}"
        )
    return (
        f"client {client} is newer than this relore daemon ({server}): the deployment is "
        f"behind, not the client. Redeploy the daemon at {client}, or install the client "
        f"at {server}."
    )

"""`git blame` for one line, from the daemon's working clone (issue #9).

Blame is the thing a cheap clone cannot give you: `--depth 200` is plenty to *read* a file
and useless for attributing a line, so an agent minimising clone cost lands in a hole no
local implementation can dig out of. The daemon's clone is complete for exactly this
reason -- the whole history is addressable, and locally.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

TIMEOUT = 120


@dataclass(frozen=True)
class BlameLine:
    sha: str
    author: str
    author_time: str
    summary: str
    text: str


@dataclass(frozen=True)
class BlameCommit:
    """One revision in a line's history, oldest last -- `git log -L`, not `git blame`."""

    sha: str
    date: str
    summary: str


def blame_line(root: str, path: str, line: int) -> BlameLine | None:
    """Who last touched ``line``, or ``None`` when the file or line is not there."""
    # A query does not reach the network, and this is what makes that a property of the
    # call rather than a claim about `CLONE_ARGS`: against a clone that is partial anyway,
    # blame fails here instead of hanging on a fetch to github.com.
    done = subprocess.run(
        ["git", "blame", "-L", f"{line},{line}", "--porcelain", "--", path],
        cwd=root,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if done.returncode != 0 or not done.stdout:
        return None

    lines = done.stdout.splitlines()
    fields: dict[str, str] = {}
    for entry in lines[1:]:
        if entry.startswith("\t"):
            fields["text"] = entry[1:]
            break
        key, _, value = entry.partition(" ")
        fields.setdefault(key, value)
    return BlameLine(
        sha=lines[0].split(" ", 1)[0],
        author=fields.get("author", ""),
        author_time=fields.get("author-time", ""),
        summary=fields.get("summary", ""),
        text=fields.get("text", ""),
    )


def line_history(
    root: str, path: str, start: int, end: int, *, limit: int = 8
) -> tuple[BlameCommit, ...]:
    """Every revision that touched ``start..end``, newest first (huggingface/relore#57).

    ``blame`` answers *who touched this line last*, which on a file that has been
    reformatted, moved or renamed since is a cosmetic pull request sitting in front of the
    one that decided anything. Measured on `transformers`: blame of
    ``generation/utils.py:2301`` names #43121, a later refactor, while the behaviour was
    argued in #37866 -- and an agent handed only the first spent eleven further calls
    looking for the second.

    ``git log -L`` follows the range through the file's history, so a line that moved is
    still tracked. What it does **not** follow is a line that was deleted and reintroduced
    elsewhere: the chain ends where this range's history ends, and a caller wanting the
    whole story of a *string* wants ``-S`` and a term to pickaxe on, which is a judgement
    this cannot make for them. So the chain is offered as history, never as provenance.
    """
    if end < start:
        start, end = end, start
    done = subprocess.run(
        [
            "git",
            "log",
            f"-L{start},{end}:{path}",
            "--format=@@%H%x09%ad%x09%s",
            "--date=short",
            "-s",
            f"--max-count={limit}",
        ],
        cwd=root,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if done.returncode != 0 or not done.stdout:
        return ()

    out: list[BlameCommit] = []
    for entry in done.stdout.splitlines():
        if not entry.startswith("@@"):
            continue
        sha, _, rest = entry[2:].partition("\t")
        date, _, summary = rest.partition("\t")
        out.append(BlameCommit(sha=sha, date=date, summary=summary))
    return tuple(out)

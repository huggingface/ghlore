"""`git blame` for one line, from the daemon's working clone (issue #9).

Blame is the thing a cheap clone cannot give you: `--depth 200` is plenty to *read* a file
and useless for attributing a line, so an agent minimising clone cost lands in a hole no
local implementation can dig out of. The daemon's clone is blobless rather than shallow
for exactly this reason -- the whole history is addressable.
"""

from __future__ import annotations

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


def blame_line(root: str, path: str, line: int) -> BlameLine | None:
    """Who last touched ``line``, or ``None`` when the file or line is not there."""
    done = subprocess.run(
        ["git", "blame", "-L", f"{line},{line}", "--porcelain", "--", path],
        cwd=root,
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

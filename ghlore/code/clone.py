"""The working clone the daemon answers code questions from (section 1, issue #7).

One complete clone per indexed repository, checked out at HEAD and refreshed on a loop.
HEAD is the right depth **for these verbs** and does not settle the build plan's open
question 4(a): the lens needs the tree as it was when a comment was written, so it will
want per-merge-commit checkouts. `grep`, `copies` and `symbol` ask *what does this code
look like now*, which is what "check all models and revert `partial_rotation` where it got
deleted" needed and what a caller cannot get from the index.

Missing is a state, not a failure (4(b)): a repository with no clone answers these verbs
with a sentence saying so, and the conversation index never depends on a checkout.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ROOT_ENV = "GHLORE_CLONE_ROOT"
DEFAULT_ROOT = "/var/lib/ghlore/clones"

# Complete, not shallow and not blobless. `--depth` makes blame impossible at any depth
# (#9). `--filter=blob:none` was the first answer and the wrong one: blame walks back
# through a file's history, so every `why` fetched blobs from the remote mid-query, and on
# `transformers` the walk reached objects the promisor remote would not serve at all --
# `modeling_llama.py:1` failed after 30s. Complete costs disk (transformers: 180MB ->
# ~800MB) and buys a query that never touches the network.
CLONE_ARGS = ("--single-branch",)

TIMEOUT = 1800


class CloneUnavailable(Exception):
    """No working clone for this repository, and that is an answer rather than an error."""


@dataclass(frozen=True)
class CloneInfo:
    repo: str
    path: str
    head: str
    fetched_at: str


class WorkingClones:
    """The clones, by repository. One process owns the directory; git owns concurrency."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or os.environ.get(ROOT_ENV) or DEFAULT_ROOT)

    def path(self, repo: str) -> Path:
        return self.root / repo.replace("/", "__")

    def available(self, repo: str) -> bool:
        return (self.path(repo) / ".git").is_dir()

    def require(self, repo: str) -> str:
        if not self.available(repo):
            raise CloneUnavailable(
                f"no working clone of {repo} on this daemon. "
                f"`ghlored clone --repo {repo}` creates one; until then the code verbs "
                "cannot answer and the history verbs are unaffected."
            )
        return str(self.path(repo))

    def ensure(self, repo: str, *, url: str | None = None) -> CloneInfo:
        """Clone if absent, fetch if present. Safe to call on every refresh."""
        target = self.path(repo)
        if (target / ".git").is_dir():
            self._git(target, "fetch", "origin")
            self._git(target, "reset", "--hard", "FETCH_HEAD")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._run(
                ["git", "clone", *CLONE_ARGS, url or f"https://github.com/{repo}.git", str(target)],
                cwd=target.parent,
            )
        return self.info(repo)

    def info(self, repo: str) -> CloneInfo:
        target = self.path(repo)
        head = self._git(target, "rev-parse", "HEAD").strip()
        when = self._git(target, "log", "-1", "--format=%cI", "HEAD").strip()
        return CloneInfo(repo=repo, path=str(target), head=head, fetched_at=when)

    def _git(self, cwd: Path, *args: str) -> str:
        return self._run(["git", *args], cwd=cwd)

    @staticmethod
    def _run(argv: list[str], *, cwd: Path) -> str:
        done = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
        if done.returncode != 0:
            raise CloneUnavailable(f"{' '.join(argv[:3])} failed: {done.stderr.strip()[:300]}")
        return done.stdout

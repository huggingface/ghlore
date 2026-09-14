"""Keeping the working clone current (issue #7).

`relored clone` creates a clone and re-running it is the refresh -- but nothing re-ran it,
so HEAD aged from the moment somebody typed it and `grep`, `copies` and `why` answered
about the tree as of that day. This is the loop that re-runs it.

**A thread inside `serve`, not a CronJob.** The clones volume is ReadWriteOnce and the
serve pod holds it, so a second pod wanting it stays Pending; and a laptop running
`relored serve` has no Kubernetes to schedule anything with. The refresh belongs to the
process that owns the directory.

It refreshes what is already cloned and **never creates one**: which repositories deserve
a clone is an operator's decision and a long download, while keeping an existing one
current is neither. A repository added with `relored clone` joins the loop on its next
tick, because the set is re-read each time rather than captured at startup.
"""

from __future__ import annotations

import logging
import threading

from relore.code.clone import CloneInfo, CloneUnavailable, WorkingClones

log = logging.getLogger(__name__)

#: Off by default. A refresh is a `git fetch` against github.com, and `serve` reaching the
#: network at all is a property a deployment should opt into rather than discover.
REFRESH_ENV = "RELORE_CLONE_REFRESH"


def cloned_repos(clones: WorkingClones) -> list[str]:
    """The repositories with a clone under this root, as ``owner/name``."""
    root = clones.root
    if not root.is_dir():
        return []
    return sorted(
        entry.name.replace("__", "/") for entry in root.iterdir() if (entry / ".git").is_dir()
    )


def refresh_once(clones: WorkingClones) -> list[CloneInfo]:
    """Fetch and reset every existing clone. One failure does not stop the others."""
    done: list[CloneInfo] = []
    for repo in cloned_repos(clones):
        try:
            info = clones.ensure(repo)
        except (CloneUnavailable, OSError) as exc:
            # A refresh that cannot reach github.com leaves the clone it had, which still
            # answers -- so this is a warning about staleness, never a reason to stop.
            log.warning("clone refresh failed for %s: %s", repo, exc)
            continue
        done.append(info)
        log.info("refreshed %s at %s", repo, info.head[:12])
    return done


def start_refresh(
    clones: WorkingClones, interval: float, *, stop: threading.Event | None = None
) -> threading.Thread | None:
    """Refresh every ``interval`` seconds until ``stop``. ``None`` when disabled.

    Daemonised, so it never holds up shutdown, and it waits before the first pass: the
    clone was current the moment it was made, and a crash-looping pod must not re-fetch
    three repositories on every start.
    """
    if interval <= 0:
        return None

    halt = stop or threading.Event()

    def loop() -> None:
        while not halt.wait(interval):
            refresh_once(clones)

    thread = threading.Thread(target=loop, name="clone-refresh", daemon=True)
    thread.start()
    log.info("refreshing clones every %gs", interval)
    return thread

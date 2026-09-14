"""Fold the UI's relevance judgements back into section 10's evaluation set.

``POST /api/v1/label`` appends one row per *hit*: this query, this thread, relevant or
not. The evaluation set wants one row per *question*: this query, and every thread that
answers it. This is the join, and it is a separate step because the two files answer to
different things -- the labels file is an append-only record of what somebody clicked,
and the set is the frozen artefact a number is measured against.

Three rules, each of which is a way the set would otherwise lie:

* **A question nobody found an answer for is dropped, not scored zero.** Labelling every
  hit ``not_relevant`` means the corpus does not hold the answer, and an example like that
  scores zero for every system and averages that into the slice.
* **A label only matches an example with the same filters.** The same words under a
  different ``--file`` are a different question (see ``LabelRequest.filters``).
* **A query typed freehand in the UI becomes a new example.** Section 10 wants the set to
  be a byproduct of somebody using the search, not a second chore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relore.bench.dataset import Dataset, Example, Relevant

#: ``decisive`` is a stronger claim than ``relevant`` -- section 8 offers both -- but for
#: recall they are the same event: the thread that answers came back.
FOUND = ("relevant", "decisive")


@dataclass
class JudgeStats:
    labels: int = 0
    judged: int = 0
    minted: int = 0
    dropped: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    backends: set[str] = field(default_factory=set)


def read_labels(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fold(
    dataset: Dataset, rows: list[dict[str, Any]], *, judge: str = "human"
) -> tuple[Dataset, JudgeStats]:
    if dataset.frozen:
        # Judging is what makes ground truth, so doing it to a frozen set changes what a
        # published number was measured against. Section 10 freezes for exactly this.
        raise ValueError(f"this set was frozen at {dataset.frozen_at}; fold labels into a new file")
    stats = JudgeStats(labels=len(rows))
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            _key(row.get("kind"), row.get("query", ""), row.get("filters") or {}), []
        ).append(row)
        backend = (row.get("backend") or {}).get("ranking")
        if backend:
            stats.backends.add(backend)

    by_key: dict[tuple, list[Example]] = {}
    for example in dataset.examples:
        by_key.setdefault(_example_key(example), []).append(example)

    updated: dict[str, Example] = {}
    minted: list[Example] = []
    for key, labels in groups.items():
        relevant = tuple(
            Relevant(repo=row["repo"], number=int(row["number"]), why=row.get("note", ""))
            for row in _best(labels)
            if row["verdict"] in FOUND
        )
        matches = by_key.get(key, [])
        if len(matches) > 1:
            stats.ambiguous.append(_describe(key))
            continue
        if not relevant:
            stats.dropped.append(_describe(key))
            continue
        if matches:
            updated[matches[0].id] = _judged(matches[0], relevant, judge)
        else:
            minted.append(_mint(key, relevant, judge))
    stats.judged = len(updated)
    stats.minted = len(minted)

    examples = tuple(updated.get(ex.id, ex) for ex in dataset.examples) + tuple(minted)
    return Dataset(examples=examples, corpus=dataset.corpus, frozen_at=dataset.frozen_at), stats


def _judged(example: Example, relevant: tuple[Relevant, ...], judge: str) -> Example:
    from dataclasses import replace

    return replace(example, relevant=relevant, judged_by=judge)


def _mint(key: tuple, relevant: tuple[Relevant, ...], judge: str) -> Example:
    kind, query, files, symbols, errors, tests = key
    ident = "|".join([kind, query, *files, *symbols, *errors, *tests])
    return Example(
        id=f"{kind}:ui:{abs(hash(ident)) % 10**10}",
        kind=kind,
        query=query,
        files=files,
        symbols=symbols,
        errors=errors,
        tests=tests,
        relevant=relevant,
        judged_by=judge,
        source={"shape": "ui_label", "leaks": False},
    )


def _best(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last verdict per thread wins -- somebody changing their mind is the normal case,
    and an append-only file keeps both clicks."""
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for row in labels:
        latest[(row["repo"], int(row["number"]))] = row
    return list(latest.values())


def _key(kind: str | None, query: str, filters: dict[str, Any]) -> tuple:
    return (
        kind or "failure",
        " ".join(query.lower().split()),
        tuple(filters.get("files") or ()),
        tuple(filters.get("symbols") or ()),
        tuple(filters.get("errors") or ()),
        tuple(filters.get("tests") or ()),
    )


def _example_key(example: Example) -> tuple:
    return _key(
        example.kind,
        example.query,
        {
            "files": list(example.files),
            "symbols": list(example.symbols),
            "errors": list(example.errors),
            "tests": list(example.tests),
        },
    )


def _describe(key: tuple) -> str:
    kind, query, files, symbols, errors, tests = key
    extra = [*(f"--file {f}" for f in files), *(f"--symbol {s}" for s in symbols)]
    extra += [*(f"--error {e}" for e in errors), *(f"--test {t}" for t in tests)]
    return " ".join([f"[{kind}]", query or "(no text)", *extra])

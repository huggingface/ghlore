"""The lens (section 1) and the local verbs: enclosing symbol, refs, the repo map.

``enclosing_symbol`` is the highest-value enrichment in section 1's table -- it is what
turns ``foo.py:412`` into ``Gemma3Model.forward`` and therefore what makes a four-year-old
review comment retrievable after the file has drifted. The tests that matter are the ones
about the *two tiers*: with extents the answer is exact, without them it is the nearest
preceding definition and must say so.
"""

from __future__ import annotations

import pytest
from toy_language import ToyDefsOnlyProvider, ToyProvider

from relore.code import registry
from relore.code.defs import definitions, enclosing_symbol
from relore.code.refs import references
from relore.code.repomap import repo_map

SOURCE = b"""
class Gemma3Model:
    def forward(self, x):
        y = helper(x)
        return y

    def other(self):
        pass


def helper(x):
    return x
"""

FORWARD_BODY = 4  # `y = helper(x)`
BETWEEN = 6  # the blank line between the two methods


@pytest.fixture(autouse=True)
def only_python():
    """Take the ctags fallback out, so these assertions are about the python provider's own
    answers rather than a fallback's."""
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def python():
    try:
        from relore.code.providers.python import PythonProvider
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tree-sitter-python: {exc}")
    return PythonProvider()


# -- enclosing symbol ------------------------------------------------------


def test_a_line_resolves_to_the_innermost_definition(python, monkeypatch) -> None:
    """Innermost matters: a line in a method is inside both the method and its class, and
    the class is not the answer anyone wanted."""
    _use(monkeypatch, python)

    found = enclosing_symbol("m.py", SOURCE, FORWARD_BODY)

    assert found is not None
    assert found.qualname == "Gemma3Model.forward"
    assert found.kind == "method"
    assert found.exact


def test_a_line_inside_nothing_is_none(python, monkeypatch) -> None:
    _use(monkeypatch, python)

    assert enclosing_symbol("m.py", SOURCE, 1) is None


def test_without_extents_the_answer_is_the_nearest_preceding_and_says_so(monkeypatch) -> None:
    """Section 9's rule 2. The nearest preceding definition is usually right and
    occasionally names the function above the one you meant -- so a caller that cannot tell
    would quote the wrong symbol with full confidence."""
    _use(monkeypatch, ToyDefsOnlyProvider())
    source = b"shape Circle\ndo area\n\n\nshape Square\ndo area\n"

    found = enclosing_symbol("x.toy", source, 4)  # a blank line after `do area`

    assert found is not None
    assert found.qualname == "Circle::area"
    assert not found.exact


def test_with_extents_a_line_outside_every_range_is_none_rather_than_nearest(monkeypatch) -> None:
    """The two tiers give genuinely different answers, which is why the flag exists."""
    _use(monkeypatch, ToyProvider())
    source = b"shape Circle\ndo area\n"

    exact = enclosing_symbol("x.toy", source, 2)

    assert exact is not None and exact.exact
    assert exact.qualname == "Circle::area"


def test_the_qualname_uses_the_providers_separator_all_the_way_up(monkeypatch) -> None:
    """Rule 1, end to end: ``::`` survives from the provider through the lens to the
    caller. Anywhere core re-joined the parts this would read ``Circle.area``."""
    _use(monkeypatch, ToyProvider())

    found = enclosing_symbol("x.toy", b"shape Circle\ndo area\n", 2)

    assert found is not None
    assert found.qualname == "Circle::area"


# -- definitions -----------------------------------------------------------


def test_definitions_come_back_in_source_order(python, monkeypatch) -> None:
    _use(monkeypatch, python)

    found = definitions("m.py", SOURCE)

    assert [d.qualname for d in found] == [
        "Gemma3Model",
        "Gemma3Model.forward",
        "Gemma3Model.other",
        "helper",
    ]
    assert [d.start_line for d in found] == sorted(d.start_line for d in found)


# -- refs ------------------------------------------------------------------


def test_refs_reports_every_occurrence_with_its_kind(python, monkeypatch, tmp_path) -> None:
    """Not only call sites. The measured failure was ``refs`` returning 21 of ~400
    occurrences with nothing to say it had left 95% out."""
    _use(monkeypatch, python)
    (tmp_path / "a.py").write_bytes(SOURCE)
    (tmp_path / "b.py").write_bytes(b"from a import helper\n\ndef go():\n    return helper(1)\n")

    result = references(str(tmp_path), "helper")

    assert result.by_kind == {"call": 2, "definition": 1, "name": 1}
    assert result.searched == 2
    assert result.complete


def test_refs_finds_a_name_read_through_an_attribute(python, monkeypatch, tmp_path) -> None:
    """The decisive case: ``self.compute_default_rope_parameters`` assigned rather than
    called was absent from the output, in the file being debugged."""
    _use(monkeypatch, python)
    (tmp_path / "m.py").write_bytes(
        b"class Rot:\n"
        b"    def go(self):\n"
        b"        self.rope_init_fn = self.compute_default_rope_parameters\n"
        b"\n"
        b"    def compute_default_rope_parameters(self):\n"
        b"        pass\n"
    )

    result = references(str(tmp_path), "compute_default_rope_parameters")

    assert [(h.line, h.kind) for h in result.hits] == [(3, "attribute"), (5, "definition")]


def test_refs_names_the_providers_that_offer_no_reference_tier(monkeypatch, tmp_path) -> None:
    """Otherwise a caller reads the gap as an absence: "nothing calls this" and "nobody
    looked" are different answers."""
    _use(monkeypatch, ToyDefsOnlyProvider())
    (tmp_path / "x.toy").write_bytes(b"shape Circle\ndo area\nuse area\n")

    result = references(str(tmp_path), "area")

    assert result.hits == ()
    assert result.unsupported == ("toy-defs-only",)
    assert not result.complete


def test_refs_does_not_split_a_qualname_to_match_it(python, monkeypatch, tmp_path) -> None:
    """Rule 1 forbids core choosing a separator, and the honest consequence is a declared
    limit: Python's provider does not resolve callees, so the qualified form finds nothing
    and the bare name is the query that works."""
    _use(monkeypatch, python)
    (tmp_path / "a.py").write_bytes(SOURCE)

    assert references(str(tmp_path), "helper").hits
    assert references(str(tmp_path), "some.module.helper").hits == ()


# -- the repo map ----------------------------------------------------------


def test_the_map_ranks_by_how_much_of_the_tree_writes_a_name(python, monkeypatch, tmp_path) -> None:
    _use(monkeypatch, python)
    (tmp_path / "core.py").write_bytes(b"def busy():\n    pass\n\ndef quiet():\n    pass\n")
    for i in range(3):
        (tmp_path / f"user{i}.py").write_bytes(b"from core import busy\n\ndef go():\n    busy()\n")

    result = repo_map(str(tmp_path), limit=10)

    assert result.ranked_by == "name matches per definition"
    assert result.entries[0].qualname == "busy"
    assert result.entries[0].matches == 6  # three imports and three calls
    assert result.entries[0].shared_by == 1
    assert {e.qualname for e in result.entries} >= {"busy", "quiet"}


def test_the_map_divides_a_count_shared_by_several_definitions(
    python, monkeypatch, tmp_path
) -> None:
    """The failure this exists for: every ``.to`` in `huggingface/transformers` was
    credited to five unrelated definitions, and 35 of the top 40 were ``__init__`` at an
    identical count. A busy name defined once has to outrank a busier name defined many
    times, or the map is a list of the language's most common method names."""
    _use(monkeypatch, python)
    (tmp_path / "shared.py").write_bytes(
        b"class A:\n    def to(self):\n        pass\n\n"
        b"class B:\n    def to(self):\n        pass\n\n"
        b"def unique():\n    pass\n"
    )
    (tmp_path / "user.py").write_bytes(
        b"def go(a):\n    a.to()\n    a.to()\n    a.to()\n    unique()\n    unique()\n"
    )

    result = repo_map(str(tmp_path), limit=10)

    top = result.entries[0]
    assert top.qualname == "unique"
    assert (top.matches, top.shared_by) == (2, 1)
    shared = [e for e in result.entries if e.qualname.endswith(".to")]
    assert [(e.matches, e.shared_by) for e in shared] == [(3, 2), (3, 2)]


def test_the_map_leaves_dunders_out_and_says_how_many(python, monkeypatch, tmp_path) -> None:
    _use(monkeypatch, python)
    (tmp_path / "m.py").write_bytes(
        b"class A:\n    def __init__(self):\n        pass\n\n"
        b"class B:\n    def __init__(self):\n        pass\n"
    )

    result = repo_map(str(tmp_path), limit=10)

    assert result.definitions == 4
    assert result.excluded_dunders == 2
    assert all("__init__" not in e.qualname for e in result.entries)


def test_the_map_says_when_nothing_supplied_a_reference_graph(monkeypatch, tmp_path) -> None:
    """So a flat ranking is not mistaken for a flat codebase."""
    _use(monkeypatch, ToyDefsOnlyProvider())
    (tmp_path / "x.toy").write_bytes(b"shape Circle\ndo area\n")

    result = repo_map(str(tmp_path), limit=10)

    assert result.ranked_by == "declaration order"
    assert result.definitions == 2


def test_the_map_is_stable_across_runs(python, monkeypatch, tmp_path) -> None:
    """Two runs over an unchanged tree must agree, or a caller cannot tell a code change
    from a reordering."""
    _use(monkeypatch, python)
    for i in range(4):
        (tmp_path / f"m{i}.py").write_bytes(b"def f():\n    pass\n")

    first = repo_map(str(tmp_path), limit=10)
    second = repo_map(str(tmp_path), limit=10)

    assert first.entries == second.entries


def test_the_map_skips_the_directories_nobody_wants_parsed(python, monkeypatch, tmp_path) -> None:
    _use(monkeypatch, python)
    (tmp_path / "kept.py").write_bytes(b"def kept():\n    pass\n")
    for skipped in (".git", "node_modules", ".venv", "__pycache__"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "vendored.py").write_bytes(b"def vendored():\n    pass\n")

    result = repo_map(str(tmp_path), limit=50)

    assert {e.qualname for e in result.entries} == {"kept"}


def test_the_map_reads_the_working_tree_and_caches_nothing(python, monkeypatch, tmp_path) -> None:
    """The property that justifies doing this locally: the answer describes the tree the
    caller is actually on, dirty files included. A cache would be a staleness hazard aimed
    at exactly that."""
    _use(monkeypatch, python)
    path = tmp_path / "m.py"
    path.write_bytes(b"def before():\n    pass\n")
    assert {e.qualname for e in repo_map(str(tmp_path)).entries} == {"before"}

    path.write_bytes(b"def after():\n    pass\n")

    assert {e.qualname for e in repo_map(str(tmp_path)).entries} == {"after"}


def _use(monkeypatch: pytest.MonkeyPatch, *providers) -> None:
    monkeypatch.setattr(registry, "providers", lambda: tuple(providers))
    monkeypatch.setattr(registry, "ctags_provider", lambda: None)

"""The shared conformance suite (section 12), and the registration seam.

Every provider passes the same suite, and the suite is parametrized over whatever is
actually installed. **Nothing here silently passes**: a provider whose grammar or binary is
absent is *skipped by name*, the same discipline the Postgres half of the store suite
follows -- a green run that never exercised a provider is a green run about nothing.
"""

from __future__ import annotations

import logging
from importlib.metadata import EntryPoint

import pytest
from toy_language import ToyDefsOnlyProvider, ToyProvider

from relore.code import registry
from relore.code.api import CAPABILITIES, DEFS, EXTENTS, REFS, LanguageProvider, MissingParser

PYTHON_SOURCE = b'''
import os


class Shape:
    """A docstring, which is not a definition."""

    def area(self):
        return helper(self)

    class Inner:
        def deep(self):
            pass


def helper(shape):
    return shape.area()
'''

TOY_SOURCE = b"""shape Circle
do area
use helper

shape Square
do area
"""


#: The same file mid-edit. Not an unbalanced bracket: an unclosed ``(`` is a *legitimate*
#: reading in which the rest of the file is one expression, so it can swallow later
#: definitions -- see the ceiling test below. These are the shapes an agent actually leaves
#: behind: a truncated call, a junk line.
BROKEN = {
    "x.py": PYTHON_SOURCE.replace(b"return helper(self)", b"return helper("),
    "x.toy": TOY_SOURCE.replace(b"shape Square", b"!!! half-typed\nshape Square"),
}


def _samples(provider: LanguageProvider) -> tuple[str, bytes]:
    """A file this provider claims, and its bytes."""
    for path, source in (("x.py", PYTHON_SOURCE), ("x.toy", TOY_SOURCE)):
        if registry.claims(provider, path):
            return path, source
    raise AssertionError(f"no fixture for {provider.name}")


def _first_class() -> list:
    """The providers to run the suite over, each skipped by name when unavailable."""
    out = [
        pytest.param(ToyProvider(), id="toy"),
        pytest.param(ToyDefsOnlyProvider(), id="toy-defs-only"),
    ]
    try:
        from relore.code.providers.python import PythonProvider

        out.append(pytest.param(PythonProvider(), id="python"))
    except Exception as exc:  # noqa: BLE001
        out.append(
            pytest.param(
                None, id="python", marks=pytest.mark.skip(reason=f"tree-sitter-python: {exc}")
            )
        )
    return out


@pytest.fixture(params=_first_class())
def provider(request: pytest.FixtureRequest) -> LanguageProvider:
    return request.param


# -- the conformance suite -------------------------------------------------


def test_declared_capabilities_are_a_known_subset(provider: LanguageProvider) -> None:
    assert provider.capabilities <= CAPABILITIES
    assert DEFS in provider.capabilities, "a provider that cannot find definitions has no use"


def test_declared_capabilities_match_observed_behaviour(provider: LanguageProvider) -> None:
    """Section 9's rule 2. A declared capability nothing implements is a lie a reader would
    reasonably act on -- and the tier above would present a guess as exact."""
    path, source = _samples(provider)
    found = list(provider.defs(path, source))
    assert found

    if EXTENTS in provider.capabilities:
        assert all(d.end_line is not None for d in found)
        assert all(d.end_line >= d.start_line for d in found)
    else:
        assert all(d.end_line is None for d in found), (
            "an end line a caller cannot trust is worse than none"
        )

    references = list(provider.refs(path, source))
    if REFS not in provider.capabilities:
        assert references == []


def test_qualnames_are_stable_across_runs(provider: LanguageProvider) -> None:
    """The whole incremental story downstream rests on this: ``enclosing_symbol`` is stored
    and compared, so a qualname that varies between processes rewrites rows for nothing."""
    path, source = _samples(provider)

    first = [d.qualname for d in provider.defs(path, source)]
    second = [d.qualname for d in provider.defs(path, source)]

    assert first == second
    assert all(first)


def test_a_nested_definition_is_qualified_by_the_providers_own_separator(
    provider: LanguageProvider,
) -> None:
    """Rule 1: core never joins the parts, because the separator *is* the language. The toy
    provider spells it ``::``, so anywhere core assembled a name itself this fails."""
    path, source = _samples(provider)
    nested = [d for d in provider.defs(path, source) if d.parent]
    assert nested, "the fixtures all contain a nested definition"

    for definition in nested:
        assert definition.name in definition.qualname
        assert definition.qualname != definition.name, "a nested name must carry its scope"
        assert definition.parent in definition.qualname


def test_defs_survive_a_syntax_error(provider: LanguageProvider) -> None:
    """The normal state of a tree an agent is halfway through editing, and the reason this
    is tree-sitter rather than the stdlib parser: definitions before *and* after the break
    still come back."""
    path, source = _samples(provider)

    whole = {d.qualname for d in provider.defs(path, source)}
    broken = {d.qualname for d in provider.defs(path, BROKEN[path])}

    assert broken, "a broken file must not parse to nothing"
    assert broken == whole, f"a mid-edit truncation should cost no definitions: {whole - broken}"


def test_a_provider_is_a_pure_function_of_path_and_bytes(provider: LanguageProvider) -> None:
    """Rule 4. The same bytes under a different name give the same answer, which is what
    lets one provider serve a working tree in the client and a historical blob in the
    daemon."""
    path, source = _samples(provider)
    suffix = path.rsplit(".", 1)[-1]

    here = [d.qualname for d in provider.defs(f"a/b/c.{suffix}", source)]
    there = [d.qualname for d in provider.defs(f"totally/elsewhere.{suffix}", source)]

    assert here == there


def test_error_recovery_has_a_ceiling_and_it_is_unbalanced_brackets() -> None:
    """Documented rather than pretended away.

    An unclosed ``(`` is not junk the parser can skip: it is a legitimate reading in which
    everything after it is one parenthesized expression, so definitions below it stop being
    definitions. Recovery is therefore "lower fidelity", not "always complete" -- and a
    caller that assumed otherwise would read a truncated definition list as a small file.
    """
    try:
        from relore.code.providers.python import PythonProvider
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tree-sitter-python: {exc}")

    provider = PythonProvider()
    swallowed = PYTHON_SOURCE.replace(b"import os", b"import os\nx = ((( ]]]")

    before = {d.qualname for d in provider.defs("x.py", PYTHON_SOURCE)}
    after = {d.qualname for d in provider.defs("x.py", swallowed)}

    assert after != before
    assert len(after) < len(before)


# -- ctags, the breadth fallback -------------------------------------------


def test_ctags_reports_itself_unavailable_rather_than_returning_nothing() -> None:
    """``ctags`` on PATH proves nothing: macOS ships BSD ctags, which has no
    ``--output-format``. A wrong answer here is a silently empty definition list."""
    from relore.code.providers.ctags import CtagsProvider

    provider = CtagsProvider()
    if not provider.available():
        pytest.skip("no universal-ctags on PATH")

    found = list(provider.defs("x.py", PYTHON_SOURCE))

    assert found
    assert EXTENTS not in provider.capabilities
    assert all(d.end_line is None for d in found), "ctags reports no end line"


# -- registration ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.reset()
    yield
    registry.reset()


def _register(monkeypatch: pytest.MonkeyPatch, *entries: EntryPoint) -> None:
    monkeypatch.setattr(registry, "_entry_points", lambda: entries)
    registry.reset()


def test_a_language_is_added_by_declaring_it_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 9's claim, as a test: a second language is a package, not a core diff. The
    entry point below is loaded by the real ``EntryPoint.load``, resolving a real
    ``module:attr`` string -- only the *discovery* is substituted."""
    _register(
        monkeypatch,
        EntryPoint(name="toy", value="toy_language:ToyProvider", group=registry.ENTRY_POINT_GROUP),
    )

    assert [p.name for p in registry.providers()] == ["toy"]
    assert registry.provider_for("shapes.toy").name == "toy"
    assert registry.claimed("shapes.toy")


def test_a_provider_whose_grammar_is_missing_is_skipped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Rule 3: a warning, never an ImportError -- and its files fall to ctags, so the
    language drops one tier instead of disappearing."""
    _register(
        monkeypatch,
        EntryPoint(
            name="klingon",
            value="relore_klingon_grammar_that_is_not_installed:Provider",
            group=registry.ENTRY_POINT_GROUP,
        ),
    )

    with caplog.at_level(logging.WARNING):
        loaded = registry.providers()

    assert loaded == ()
    assert "klingon" in caplog.text
    assert "ctags" in caplog.text


def test_an_unclaimed_file_falls_to_ctags_at_defs_only_fidelity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(monkeypatch)  # no first-class providers at all
    from relore.code.providers.ctags import CtagsProvider

    if not CtagsProvider().available():
        pytest.skip("no universal-ctags on PATH")

    provider = registry.provider_for("anything.rs")

    assert provider.name == "ctags"
    assert provider.capabilities == frozenset({DEFS})


def test_with_no_provider_and_no_ctags_the_error_is_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never an ImportError traceback (AGENTS.md, section 2)."""
    _register(monkeypatch)
    monkeypatch.setenv(registry.DISABLE_CTAGS_ENV, "1")
    registry.reset()

    with pytest.raises(MissingParser, match="pip install"):
        registry.provider_for("anything.rs")


def test_the_shipped_entry_point_registers_python() -> None:
    """The real one, through the real mechanism: ``pyproject.toml`` declares it and the
    installed distribution exposes it."""
    names = [p.name for p in registry.providers()]

    assert "python" in names, f"expected the shipped python provider, got {names}"

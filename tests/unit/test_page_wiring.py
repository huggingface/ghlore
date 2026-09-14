"""The page is one string, and its failure mode is total.

`$("#typo")` is `null`, `null.addEventListener` throws, and a top-level throw aborts the
whole script -- so the search form loses its submit handler and the browser falls back to a
native GET. The page still renders and the access log still says 200. Nothing about that
looks like an error, which is why it is worth a test rather than a careful eye.
"""

from __future__ import annotations

import re
from html import unescape

from relore.api.ui import page

HTML = page()

ELEMENT_IDS = set(re.findall(r'id="([^"]+)"', HTML))
SELECTED_IDS = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', HTML))


def test_every_selector_matches_an_element() -> None:
    missing = SELECTED_IDS - ELEMENT_IDS
    assert not missing, f'$("#id") with no element in the markup: {sorted(missing)}'


def test_the_why_examples_name_a_repository_and_a_line() -> None:
    """`why` blames a clone, which is per repository: a path from one is a 404 against
    another, and an example that does not land teaches nothing."""
    examples = re.findall(r'\{label: "[^"]+",\s*at: "([^"]+)", repo: "([^"]+)"\}', HTML)

    assert examples, "the why samples are gone or no longer parseable"
    for at, repo in examples:
        path, _, line = at.rpartition(":")
        assert path.endswith(".py"), at
        assert line.isdigit() and int(line) >= 1, at
        assert "/" in repo, repo


def test_the_page_documents_every_verb_the_cli_has() -> None:
    """Issue #29: `--help` listed twelve subcommands and the page named four, so an agent
    handed the URL got the toolset from two milestones ago. The list comes from the parser
    rather than a copy of it, so a verb added later fails here until the page names it --
    and one still marked NOT IMPLEMENTED is excluded by its own help text, so shipping it
    is what puts it on the page's hook."""
    import argparse

    from relore.cli import build_parser

    actions = [
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    verbs = {
        name: choice.description or ""
        for action in actions
        for name, choice in action.choices.items()
    }
    helps = {
        choice.dest: choice.help
        for action in actions
        for choice in action._get_subactions()  # type: ignore[attr-defined]
    }
    shipped = [v for v in verbs if "NOT IMPLEMENTED" not in (helps.get(v) or "")]

    assert len(shipped) >= 11, f"expected the full verb list, got {sorted(shipped)}"
    missing = [verb for verb in shipped if f"relore {verb}" not in HTML]
    assert not missing, f"verbs the landing page never names in command form: {missing}"


def _without_script(html: str) -> str:
    """What a reader with no JS engine gets: `curl`, an HTML-to-markdown fetch, most agent
    page fetchers."""
    stripped = re.sub(r"<script>.*?</script>", " ", html, flags=re.S)
    return unescape(re.sub(r"<[^>]*>", " ", stripped))


def test_the_setup_block_names_its_variables_without_a_js_engine() -> None:
    """Issue #30: `RELORE_API` and `RELORE_TOKEN` existed only inside a template literal
    that interpolates `location.origin`, so no reader without a JS engine could see either
    name. One that fetched the page described authentication with `RELORE_API_TOKENS`
    instead -- the *server operator's* variable, taken from the prose further up -- and
    pointed a new user at the wrong variable entirely."""
    static = _without_script(HTML)

    assert "export RELORE_API=" in static
    assert "export RELORE_TOKEN=" in static


def test_a_daemon_that_wants_no_token_does_not_name_one_to_a_curl_reader() -> None:
    """Telling somebody to paste a credential that is not read is worse than saying
    nothing, and the script that hides it is the thing this reader does not run."""
    static = _without_script(page(auth_required=False))

    assert "export RELORE_API=" in static
    assert "RELORE_TOKEN" not in static


def test_the_agent_paragraph_is_readable_by_the_agent_it_is_for() -> None:
    """It is the payload of "one paragraph in the file it reads at startup", and it was
    the one part of that section an agent could not read (issue #30)."""
    static = _without_script(HTML)

    assert "## Project history" in static
    assert "relore inflight" in static


def test_the_page_carries_its_own_version() -> None:
    """The handshake refuses a client of another version, and the page is a client."""
    from relore import __version__

    assert f'"{__version__}"' in HTML
    assert "/*VERSION*/" not in HTML
    assert "/*FAVICON*/" not in HTML

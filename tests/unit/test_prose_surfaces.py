"""Every surface that describes the verbs, held to the invariant one of them already had.

`ghlore` describes its verbs in four places -- the parser, the landing page, `docs/cli.md`
and the agent skill. At 0.3.4 exactly one of them had a drift test, and it was the only one
still correct: `cli.md` called `why` "a stub today" after it shipped, and the skill told
agents *not* to use `grep` and `symbol`, which is the opposite of what they are for.
Correctness tracked the test, not the care taken writing the prose. So the test is
parametrized over all four (issue #40).

The three rules here each come from a real failure in a field report (#8):

1. **Every surface names every shipped verb**, in command form. That is #29's rule for the
   page, and the list comes from :func:`ghlore.guidance.shipped_verbs`, which reads the
   parser -- so a verb added later fails here until the surfaces name it, and one still
   marked ``NOT IMPLEMENTED`` excludes itself.
2. **Every runnable example of a repo-scoped verb carries ``--repo``** (#39). 26 of 29
   examples in `cli.md` omitted it, and agents copy the shape they are shown rather than
   the sentence that states the rule.
3. **No surface calls a shipped verb unimplemented** (#39 item 4).
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest

from ghlore import guidance
from ghlore.api.ui import page
from ghlore.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
CLI_MD = (ROOT / "docs" / "cli.md").read_text()
SKILL_MD = (ROOT / "skills" / "search-project-history" / "SKILL.md").read_text()
README = (ROOT / "README.md").read_text()
AGENTS_MD = (ROOT / "docs" / "agents.md").read_text()
HOW_SEARCH_WORKS = (ROOT / "docs" / "how-search-works.md").read_text()
HELP = build_parser().format_help()
HTML = page()

#: The surfaces that must name every verb. README and `docs/agents.md` are deliberately not
#: here -- they are an argument and a wiring note, not a reference -- but their examples are
#: still examples, so they appear in the `--repo` check below.
REFERENCES = [("help", HELP), ("page", HTML), ("cli.md", CLI_MD), ("skill", SKILL_MD)]

EVERY_SURFACE = REFERENCES + [
    ("README.md", README),
    ("agents.md", AGENTS_MD),
    ("how-search-works.md", HOW_SEARCH_WORKS),
]

#: Phrasings that tell a reader a verb does not work yet. `precedent` is genuinely one of
#: these; anything the parser ships is not.
UNBUILT = re.compile(r"not implemented|NOT IMPLEMENTED|a stub today|not built|is a stub")

#: A line with one of these in it is teaching a shape, not offering something to paste --
#: `ghlore thread N --full` cannot be run as written. Only runnable lines are held to rule
#: 2, because the rule exists to stop a reader copying a command that then 400s.
SCHEMATIC = re.compile(r"[<>]|\bN\b|PATH:LINE|OWNER/NAME|QUERY|REGEX|SYMBOL|QUALNAME|\.\.\.|…")


def _code_lines(name: str, text: str) -> list[str]:
    """The command lines a reader could copy, with continuations joined.

    Fenced blocks in markdown, ``<pre>`` in HTML, everything in ``--help``. Prose is
    excluded on purpose: "`ghlore thread` reads the rest of it" is a sentence, and holding a
    sentence to the shape of a command would only teach us to stop writing sentences.
    """
    if name == "help":
        blocks = [text]
    elif name == "page":
        blocks = [unescape(block) for block in re.findall(r"<pre[^>]*>(.*?)</pre>", text, re.S)]
    else:
        blocks = re.findall(r"^```[a-z]*\n(.*?)^```", text, re.S | re.M)
    joined = "\n".join(blocks).replace("\\\n", " ")
    return [re.sub(r"^(\$ |\d+\. )", "", line.strip()) for line in joined.splitlines()]


def _invocations(name: str, text: str) -> list[str]:
    verbs = set(guidance.shipped_verbs())
    out = []
    for line in _code_lines(name, text):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "ghlore" and parts[1] in verbs:
            out.append(line)
    return out


@pytest.mark.parametrize("name,text", REFERENCES, ids=[n for n, _ in REFERENCES])
def test_every_prose_surface_names_every_shipped_verb(name: str, text: str) -> None:
    """Issue #40. The page has had this since #29; the other three had nothing."""
    readable = unescape(text)
    shipped = guidance.shipped_verbs()

    assert len(shipped) >= 11, f"expected the full verb list, got {sorted(shipped)}"
    missing = [verb for verb in shipped if f"ghlore {verb}" not in readable]
    assert not missing, f"{name} never names in command form: {missing}"


@pytest.mark.parametrize("name,text", EVERY_SURFACE, ids=[n for n, _ in EVERY_SURFACE])
def test_every_runnable_example_carries_the_repository(name: str, text: str) -> None:
    """Issue #39. `thread`, `inflight`, `why`, `symbol`, `grep` and `copies` answer a bare
    argument with a 400 on a daemon that serves more than one repository -- and a reader
    copies the example, not the paragraph next to it explaining that."""
    offenders = [
        line
        for line in _invocations(name, text)
        if line.split()[1] in guidance.REPO_SCOPED
        and not SCHEMATIC.search(line)
        and "--repo" not in line
    ]

    assert not offenders, f"{name}: runnable examples with no --repo: {offenders}"


@pytest.mark.parametrize("name,text", EVERY_SURFACE, ids=[n for n, _ in EVERY_SURFACE])
def test_no_surface_calls_a_shipped_verb_unimplemented(name: str, text: str) -> None:
    """Issue #39 item 4: `why` shipped in 0.3.x and `cli.md` went on calling it a stub, in
    two places -- a table row, and a transcript of it refusing to run.

    The window reads forward one line and never back. Both real cases named the verb on the
    same line as the claim; reaching backwards instead picks up the row above in a table,
    which is a different verb saying something else entirely.
    """
    lines = unescape(text).splitlines()
    shipped = guidance.shipped_verbs()
    offenders = []
    for index, line in enumerate(lines):
        if not UNBUILT.search(line):
            continue
        window = " ".join(lines[index : index + 2])
        offenders += [
            f"{verb}: {line.strip()}"
            for verb in shipped
            if re.search(rf"\bghlore {verb}\b|`{verb}[ `]", window)
        ]

    assert not offenders, f"{name} calls a shipped verb unimplemented: {offenders}"


@pytest.mark.parametrize("name,text", EVERY_SURFACE, ids=[n for n, _ in EVERY_SURFACE])
def test_no_surface_pins_a_stale_version(name: str, text: str) -> None:
    """A `ghlore status` sample is a reader's mental model of the command, and two of them
    had drifted by four releases -- one showing a capability set and a schema list the
    daemon no longer has (#39 item 5).

    This one fires at the bump rather than at the audit, which is the point and is also the
    friction: AGENTS.md's rule 2 says the bump and the deploy are one operation, so the
    sample the deploy is about is being edited in the same breath either way. The message
    has to say exactly that, because whoever trips it is mid-release and did not come here
    to read a test.
    """
    from ghlore import __version__

    stale = [
        line.strip()
        for line in unescape(text).splitlines()
        if re.match(r"\s*version\s+\d+\.\d+\.\d+\s*$", line) and __version__ not in line
    ]

    assert not stale, (
        f"{name} shows a `ghlore status` version that is not {__version__}: {stale}. "
        f"Bumping `__version__` means updating the samples that print it -- "
        f"docs/cli.md and docs/how-search-works.md -- in the same commit."
    )


def test_the_help_epilog_is_the_guidance_module() -> None:
    """#38 asks that `--help` be sufficient on its own, and #40 that it not become a fifth
    copy of the same prose while doing so."""
    assert guidance.epilog() in HELP
    assert "GHLORE_REPO" in HELP
    assert "GHLORE_TOKEN" in HELP


def test_the_page_agent_paragraph_is_the_guidance_module() -> None:
    """It was a hardcoded `<pre>`, which is how the best guidance in the project came to
    live only on the surface least likely to arrive intact."""
    assert guidance.agent_paragraph() in unescape(HTML)

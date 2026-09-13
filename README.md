# ghlore — GitHub project memory

**Every codebase has lore. Ask it, then check it against the code.**

<img src="docs/ghlore.png" alt="logo" width="200">

`ghlore` indexes a repository's complete issue and pull-request history and keeps a working
clone beside it, both served over one HTTP API. Three questions, in the order an agent hits
them:

**1. Is somebody already doing this?** `inflight <issue>` — every open claimant, its state
and its author, in one hop. The most expensive thing an agent does is patch something that
is already in review.

**2. Why is it like this?** Why a fallback cannot be removed, which approach was tried and
rejected, what a maintainer made the last five contributors change — none of it is in the
codebase. `thread`, `why PATH:LINE` and `search` return it at comment level with who said
it attached, so `--trust authoritative` keeps only what someone with write access settled
and machine authors are excluded by default. `search --symbol GemmaRotaryEmbedding` asks
what has been *said* about a function — a question `grep` cannot answer.

**3. Is that still true?** `grep`, `symbol` and `copies` run against that clone at HEAD,
server-side, no checkout on your side. A three-year-old review is a claim about code that
has moved since.

Threads tell you what people decided; the code verbs tell you whether it was true. A
[contributor claim] confirmed by `grep` is stronger evidence than either alone — the
argument for this over a search box.

Six agents have run it cold on the same `transformers` bug, one per release, on `--help`
alone and with no memory of the runs before. They filed 39 issues against the tool; most
are fixed, each run checking the last one's from the outside.
[#8](https://github.com/huggingface/ghlore/issues/8) is the record.

## Try it

```bash
pip install git+https://github.com/huggingface/ghlore   # no PyPI release yet
export GHLORE_API=https://your-ghlore  # a running `ghlored serve`
export GHLORE_REPO=owner/name          # the default for --repo
export GHLORE_TOKEN=…                  # if that daemon requires one

ghlore inflight 47720 --repo owner/name              # ask this one first
ghlore why src/model.py:90 --repo owner/name         # what was said about this line
ghlore search "AttributeError: 'NoneType' object has no attribute 'shape'" --kind failure
ghlore search "why is this cast here" --kind rationale --file src/model.py
ghlore search --symbol GemmaRotaryEmbedding          # every mention, exactly matched
ghlore copies compute_default_rope_parameters --repo owner/name   # which copies diverge
```

`--repo` is required on a bare number or path whenever the daemon serves more than one
repository: it refuses rather than guessing which project you meant. `GHLORE_REPO` is the
default that makes the flag a per-call override instead of a per-call tax.

`ghlore --help` is the reference — every verb, the order to reach for them in, and the
environment. More worked queries, and the four ways a healthy index returns nothing:
[`docs/cli.md`](docs/cli.md). There is no MCP server on purpose: any agent with a shell can
already call an HTTP API.

## What you get

| | |
| --- | --- |
| **Comment-level results** | the matching document with its author, trust tier, age and URL — not a thread number to go re-read |
| **Exact signals** | errors, files, symbols, test ids, shas, extracted at ingest and repeatable as filters |
| **Trust tiers** | maintainer / contributor / bot, as a filter rather than a weight |
| **Schema to join on** | "which threads touched this file", and a bug and its merged fix as one record — `search --file` under-returns the newest PRs touching a path until [#48](https://github.com/huggingface/ghlore/issues/48) lands |
| **The code, server-side** | `why PATH:LINE`, `grep`, `copies`, `symbol`, and `defs`/`refs` with `--repo`, against a working clone the daemon keeps |
| **Throughput** | every query is a Postgres query and makes no GitHub request; ten agents in parallel cost the same as one |

The trade is freshness: an answer is at most one poll interval behind.
[Why not GitHub search](docs/why-not-github-search.md) has the measurements.

Running the daemon needs a Postgres and a GitHub token with `issues:read` +
`pull_requests:read` — never write. `deploy/` has a Helm chart and the scripts that are its
interface; setup is in [`docs/operations.md`](docs/operations.md).

## Docs

- [`docs/how-search-works.md`](docs/how-search-works.md) — what kind of index this is
  (lexical and ranked, no embeddings), and what it is better and worse at than `grep`.
- [`docs/cli.md`](docs/cli.md) — worked queries, and the empty results that are not faults.
- [`docs/why-not-github-search.md`](docs/why-not-github-search.md) — the comparison.
- [`docs/architecture.md`](docs/architecture.md) — what is indexed, what is deliberately
  not, the symbol lens, and language plugins.
- [`docs/agents.md`](docs/agents.md) — wiring it into a harness (not to be confused with
  `AGENTS.md` below, which is the contributor contract for this repository).
- [`docs/ranking.md`](docs/ranking.md) — the scoring model and what the benchmark says.
- [`docs/operations.md`](docs/operations.md) — backfill, poll, clones, tokens, serving.
- [`docs/security.md`](docs/security.md) — the properties and how each is enforced.
- [`docs/prior-art.md`](docs/prior-art.md) — related work, and what was borrowed.
- [`AGENTS.md`](AGENTS.md) — the operating contract: the invariants with tests behind them.

## License

[Apache-2.0](LICENSE).

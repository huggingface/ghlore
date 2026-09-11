# ghlore — GitHub project memory

**Index a repository's complete issue and pull-request history, and serve it back as
searchable project memory for coding agents and humans.**

The knowledge that explains *why* a codebase is the way it is mostly does not live in the
codebase. It lives in a review comment from three years ago: why a fallback cannot be
removed, which approach was tried and rejected, what a maintainer made the last five
contributors change. An agent fixing a bug cannot read any of it. `ghlore` makes it
queryable.

Two things make it answer questions an agent would otherwise answer badly:

- **Symbols are indexed, so an agent does not have to grep the code.** Every symbol, file
  path, error string, test id and commit sha mentioned anywhere in the history is extracted
  and matched *exactly*, so `--symbol compute_default_rope_parameters` asks *what has been
  said about this function* — a question `grep` cannot answer at all, because the answers
  are in issues and reviews, not in the tree. On the benchmark's `failure` slice `grep`
  scores **0.000** and `ghlore` **1.000**. A review comment's anchor is stored as a name
  rather than a line number, so it survives the file being edited.
- **Every document carries who said it.** A maintainer's "we cannot do that because…" is a
  judgement, a passer-by's is a claim, and a bot's is our own output coming back.
  `--trust authoritative` keeps only what someone with write access settled; machine
  authors are excluded by default and reachable only by asking for them explicitly
  (`--trust machine`), because returning an agent's own comment as prior discussion makes
  its unreviewed output its own evidence.

## Try it

```bash
# No PyPI release yet, so install from main -- `pip install ghlore` would fetch
# whatever else owns that name. The client must be the same version as the daemon it
# talks to; if it is not, the daemon refuses the request and says which end is behind.
pip install git+https://github.com/huggingface/ghlore
export GHLORE_API=https://your-ghlore  # a running `ghlored serve`
export GHLORE_TOKEN=…                  # if that daemon requires one

ghlore search "AttributeError: 'NoneType' object has no attribute 'shape'" --kind failure
ghlore search "why is this cast here" --kind rationale --file src/model.py
ghlore search "repeat guard" --trust authoritative   # only what a maintainer settled
ghlore search --symbol GemmaRotaryEmbedding          # every mention, exactly matched
ghlore inflight 47720                  # is somebody already fixing this?
ghlore thread 47720 --focus "cropping"
```

More worked queries, and the four ways a healthy index returns nothing:
[`docs/cli.md`](docs/cli.md).

The daemon's own web page carries examples against *that* index and a snippet for a
`CLAUDE.md` / `AGENTS.md`. There is no MCP server on purpose: any agent with a shell can
already call an HTTP API.

## What you get

| | |
| --- | --- |
| **Comment-level results** | the matching document with its author, trust tier, age and URL — not a thread number to go re-read |
| **Exact signals** | errors, files, symbols, test ids, shas, extracted at ingest and repeatable as filters |
| **Trust tiers** | maintainer / contributor / bot, as a filter rather than a weight |
| **Schema to join on** | "which threads touched this file", and a bug and its merged fix as one record |
| **Throughput** | every query is a Postgres query and makes no GitHub request; ten agents in parallel cost the same as one |
| **Determinism** | same query, same answer |

The trade is freshness: an answer is at most one poll interval behind.
[Why not GitHub search](docs/why-not-github-search.md) has the measurements.

## Two binaries, on purpose

| | reads | writes | needs |
| --- | --- | --- | --- |
| `ghlore` | the HTTP API; your local checkout | nothing | an API URL + a scoped token — the code verbs need neither |
| `ghlored` | GitHub, Postgres | Postgres | a GitHub token + a database |

A security boundary, not packaging taste. `ghlore` — the binary agents get — **cannot open
a database connection, because the code to do so is not in it**, asserted by a test over
the import graph. Parsers and the server are optional extras, so the sandbox install
stays thin.

### One version, both ends

Client and daemon ship together and **refuse to talk across a version difference**: every
request declares its version, a daemon that reads any other answers `426 Upgrade Required`,
and the message names which end is behind. An old client would otherwise get an answer in
which every field it knows about is correct and whatever the newer version added is simply
absent — well-formed, plausible, silently incomplete, which is the one failure nothing
downstream can detect. `ghlore/__init__.py`'s `__version__` is a contract rather than a
label; the rules are in
[`docs/operations.md`](docs/operations.md#one-version-both-ends).

Running the daemon needs a Postgres and a GitHub token with `issues:read` +
`pull_requests:read` — never write. `deploy/` has a Helm chart and the scripts that are its
interface. Setup is in [`docs/operations.md`](docs/operations.md).

## Docs

- [`docs/cli.md`](docs/cli.md) — worked query examples against a real index, and the four
  ways a healthy index returns nothing.
- [`docs/why-not-github-search.md`](docs/why-not-github-search.md) — the comparison, and
  where GitHub search is the better tool.
- [`docs/architecture.md`](docs/architecture.md) — what is indexed, what is deliberately
  not, the symbol lens, and language plugins.
- [`docs/agents.md`](docs/agents.md) — wiring it into a harness, and the two things every
  integration must get right.
- [`docs/ranking.md`](docs/ranking.md) — the scoring model and what the benchmark says,
  caveats included.
- [`docs/operations.md`](docs/operations.md) — backfill, poll, tokens, serving.
- [`docs/security.md`](docs/security.md) — the properties and how each is enforced.
- [`docs/prior-art.md`](docs/prior-art.md) — related work, and what was borrowed.
- [`AGENTS.md`](AGENTS.md) — the operating contract: invariants that have tests behind
  them, the API limits that make a backfill look successful while missing most of the
  corpus, and what was deferred with a reason.
- **The build plan** — the specification this codebase is written against; every `section
  N` reference in the source points into it. It lives with the deployment that commissioned
  it rather than in this repository, because it carries operational detail about that
  deployment; ask its operator for a copy. The **use-cases companion** — the evidence base
  it argues from, including where this tool would have changed nothing — is held alongside
  it, for the same reason.

A Jekyll site over `docs/` is planned, not built. The markdown here is the source of truth.

## License

[Apache-2.0](LICENSE).

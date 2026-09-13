# AGENTS.md — working in `ghlore`

`ghlore` indexes a repository's complete issue and pull-request history into Postgres and
serves it back to coding agents and humans. It is memory of the project's *conversation*,
not of its tree.

**The build plan is the specification.** It carries the reasoning for everything
below, and every `section N` reference in this repository points into it. It is held with
the deployment that commissioned it rather than in this repository — it carries operational
detail about that deployment — so a contributor without it should treat this file, the
tests, and the docstrings as the contract. This file is the operating contract: what you may not break, what to run, and what
looks like a bug but is not.

**Order of authority:** build plan → tests → code. If code disagrees with the plan, one of
them is wrong; fix that **in the same change**. A deviation left undocumented does not
survive a context window.

## Where things stand

**Milestones 0–3 are done, and it is deployed** (2026-09-09) — Postgres, `ghlored serve`
and a poll loop in a Kubernetes namespace, from `deploy/`. Building is paused so the thing
can be used: everything left is gated on evidence a laptop cannot produce.

**The build plan is not in this repository.** It and its evidence base are held with the
deployment that commissioned them — see the note below. Every `section N` reference in this
codebase points into it, and §15.3 records what the first deploy taught. Do not start
milestone 4 without reading it.

 Milestone 3 landed in six pieces: §6.2's authority
resolution with the query-kind trust floors (§13.1 #9), **§5.3's extraction pass**,
**`ghlored sample` (§5.5)**, **§10's benchmark** (§10.1 for the method, §10.2–10.3 for
what it measured), **§6's query expansion** (§10.4), and **§6's weighted ranking**
(§10.5). `ghlored migrate |
fetch | backfill | sample | derive | poll | sweep | authority | mine | judge | bench |
serve | status` and `ghlore search | thread | inflight | why | status | map | defs | refs |
symbol | grep | copies` work end to end; `precedent` is the one verb still a stub, and
`--help` marks it. Do not write that list from memory — `ghlore.guidance.shipped_verbs()`
derives it from the parser, and `tests/unit/test_prose_surfaces.py` holds every
reader-facing surface to it (#40).
~11,000 lines under `ghlore/`, **806 tests**, every store-level and retrieval-level one on
both dialects. The `--file`/`--symbol`/`--error`/`--test` filters answer. **Retrieval is
ranked**: full text, filters, the trust floor, the expansion fan-out and §6's weighted
score. Kind-aware decay is built and **gated off** (`ranking.DECAY_ENABLED`) — §6's table
is a claim about an eight-year history and both corpora are six-month samples, so it costs
MRR and gains nothing here (§10.5). Update this section when a milestone in §13 lands.

It has run against **real GitHub** on four repositories: the full history of
`huggingface/serge` (114 threads, 415 documents), a §5.5 sample of
`huggingface/transformers` (3,064 threads, 41,857 documents), and a second sample at the
same 2026-03-01 floor holding `huggingface/diffusers` + `huggingface/transformers-mlinter`
(1,103 threads, 11,212 documents) — the corpus §10.3's leak-free rationale slice is judged
from, built because the *rate* of bot review comments is what that slice is short of, not
the volume. Cursor pagination is now
exercised for real — the transformers listing walk followed 50+ pages of `after=` cursors —
along with the GraphQL per-PR pass, `authority`, and idempotency (a re-derive of all 1,750
threads wrote **0 documents**). The **30,000-item comment cap is now
exercised for real too**: the full-history backfill of `huggingface/transformers` staged
**186,540** issue comments, six times the cap, so the `since`-chaining walk of §3 #2 has
walked a corpus that a page-counting one could not have. It was the last unproven limit —
the earlier samples could not reach it, because `sample` fetches per thread and so never
walks `/issues/comments` at all.

Extraction was measured on that index: 616 file rows over 222 distinct paths, 522 symbols,
173 commits — and **2 errors and 2 test ids**. serge's PR discussions rarely paste a
traceback, so §10's error and test slices could not be built from it. That is what
`ghlored sample` (§5.5) exists for, and the corpus it built is the one to work against:
a sample of `huggingface/transformers` from **2026-03-01** — 1,265 issues and 1,799 merged
PRs, 3,064 threads, 41,857 documents, 776 errors across 542 threads, 1,407 test ids,
19,955 file rows over 6,241 paths, 25,049 symbols. Trust: 52.5% `authoritative`, 36.6%
`reported`, 11.0% `machine`. §10's rationale ground truth needs the *human* reviewer shape
there, not only the bot one — see §10, where the 18-vs-4,071 measurement is recorded.

| Built | Empty — nothing written yet |
| --- | --- |
| `store/` — `schema.py`, `dialect.py`, `migrations/` (7 steps), `repository.py`, `documents_history` (§14.1) | `ingest/plugins.py` |
| `github/` — `client.py`, `bulk.py`, `graphql.py`, `fetch_thread.py` | `precedents` and `ghlore precedent` — milestone 4 |
| `ingest/` — normalize, chunk, timestamps, versions, `extract` (§5.3), `relationships` (§13.3), `index_thread`, `poll`, `backfill`, `sample` (§5.5), `sweep` | `code/` — the *server-side* half of the lens: the working clone, `path_aliases`, `enclosing_symbol` at derive time |
| `search/` — `SearchBackend` per dialect, filters, caps, the thread view, §6.2's query-kind trust floors, `expansion.py` (§6, §10.4), `ranking.py` (§6, §10.5) | |
| `bench/` — §10's evaluation set, the miner, the label fold, and the runner with both baselines | |
| `ingest/authority.py` — §6.2 write-access resolution, and `repo_authority` | |
| `api/` — `server.py`, `tokens.py`, `schemas.py`, `ui.py` | |
| `security/untrusted.py` — the envelope; redaction is in `ingest/normalize.py` | |
| `code/` — the provider seam, tree-sitter and ctags providers, `defs`/`refs`/`repomap` | |
| `render.py` — one renderer for the CLI and the UI's raw view | |
| `cli.py` and `daemon.py` — every verb above; the rest exit with their milestone | |

**Part of milestone 4's code lens landed** (issues #7 and #9): `ghlored clone` keeps a
working clone per repository at HEAD -- complete, not filtered, so a query never fetches -- `/api/v1/code/{defs,refs,symbol,grep,copies}`
answers from it, and `ghlore why PATH:LINE` blames the line and returns the pull request
that carried the commit with the review comments anchored near it. HEAD is the depth these
verbs need and settles nothing about §14.4(a): the *lens* — `enclosing_symbol` at the tree a
comment was written against — still wants per-merge-commit checkouts. A repository with no
clone answers 503; the conversation index never depends on a checkout (§14.4b). Still
unbuilt in milestone 4: `precedents`, `ghlore precedent`, the §9 extension entry points.

Tables that exist but nothing populates: `path_aliases`, `precedents`, `precedent_signals`.
**`thread_links` is now written** (§13.3): the derive pass records "this pull request claims
to close #N" and `ghlore inflight` walks it backwards. It used to be a foreign key to
`threads` on *both* ends, which is why nothing ever wrote it — an edge was unstorable until
its target was indexed, so a corpus-wide pass had to exist before a single edge did. It now
carries `target_number` (known from the source thread alone, so the poll fills it) and a
nullable `target_thread_id` (the resolution, which only ranking's `w_rel` needs). Migration
6 recreates the table; it was empty on every database that exists.
There is no keywords table and §5.3's keywords are deliberately not extracted — no term in
§6's score reads them, so they would be rows nothing looks at (§13.2).
`documents.trust` is derived from the
payload **plus** `repo_authority`, which `ghlored authority` fills — run it after a backfill
or the `authoritative` tier is empty on any org-owned repository (measured: 0 of 415
documents on `huggingface/serge` before the pass, 288 after).

**Both evaluation sets are frozen** (2026-09-08), which was §10's condition for moving the
weights, and both weights and expansion have now run against them.
`benchmarks/huggingface-transformers.jsonl` holds 190 mined candidates, **139 judged**
across all three slices, and `benchmarks/huggingface-bot-reviews.jsonl` holds 209
candidates, **9 judged**, all leak-free — both scored against **both** baselines. Every
judgement is `judged_by: model`. **A frozen set does not accept more:** `judge` refuses one
and `bench` scores it without `--allow-unfrozen`. Growing the ground truth now means a new
file, which is what `judge` tells you.
The loop:

```bash
ghlored mine  --repo huggingface/transformers --out benchmarks/huggingface-transformers.jsonl
ghlored serve                                       # label through the section 8 UI
ghlored judge --set benchmarks/…jsonl --labels labels.jsonl   # verdicts -> ground truth
ghlored bench --repo huggingface/transformers --set benchmarks/…jsonl \
  --system index --system github --system grep --clone /path/to/transformers
```

The `grep` baseline needs a checkout and nothing else. Use a detached worktree of
`origin/main` rather than a branch someone is working on — the number is only reproducible
if the tree is.

Labelling needs a token with the `label` scope and `GHLORE_LABELS_PATH` pointing somewhere.
Both sets are already frozen (`ghlored judge --set … --freeze`, which takes no `--labels`
when there is nothing left to fold), so `bench` needs no `--allow-unfrozen`.

**Milestone 4 is next when building resumes** (see §15 first), and two of its pieces are
owed to measurements already taken:
rename chains, because §13's caveat says file overlap has a recall floor without them; and
`w_rel`, because §10.5 #1 found the §6 weight *values* cannot be falsified on this corpus at
all — they only discriminate over evidence a filter did not already require, and a
relationship term would be the first that does. `thread_links` itself now exists (§13.3),
pulled forward out of milestone 4 because *using* the tool asked for it: the duplicate-work
query is one hop through an edge the data model already declared, and nothing filled it.

Two measurements to carry into that work rather than re-derive:

* **A weight only discriminates over evidence the filter did not already require**, which
  is §10.5 #1 and the least obvious thing here. Keeping the whole weighted sum but scoring
  each expansion leg against *itself* reproduces the pre-weights numbers exactly on all
  five benchmark rows — because a leg that filters on precisely what it scores gives every
  surviving row an overlap of 1.0. All of the gain is `RankSpec`, none of it is the weight
  values, and no run on this corpus can tell 3.0 from 2.0.
* **§10's rationale ground truth cannot be mined from bot findings here.** 142 of 15,475
  review comments in the sample are bot-authored, giving 18 maintainer replies across 11
  PRs; the *human* reviewer-finding shape gives 4,071. Recorded in §10 with the reasoning.
* **The human shape leaks and the bot shape does not.** A human finding is a retrievable
  document in the answer's own thread, so a lexical system reaches the answer without
  retrieving anything; every candidate carries `source.leaks` and the report splits them
  (§10.1 #2). Do not average the two and quote one number.
* **The unranked baseline, so a later weight set is a measurement and not a claim.**
  Postgres, window 2026-03-01, R@10 `ghlore` vs GitHub search vs `grep` — `failure` 0.938
  vs 0.875 vs 0.000, `rationale` 0.765 vs 0.294 vs 0.314, `precedent` 0.375 vs 0.325 vs
  0.200. **`ghlore` beats `grep` on all three**, so milestone 3's two-baseline requirement
  is met.
* **`grep`'s zero on `failure` is structural, not a cutoff.** The answers are 50 issues to
  29 PRs and grep's only bridge is "threads that touched this path", which is PR-shaped:
  the answer is somewhere in its ranked list for 13 of 48 examples and its best rank over
  those is 26. Do not re-derive this by widening the top-k — it was traced over every
  candidate, not the top ten.
* **Do not quote the `rationale` row. On the leak-free subset `ghlore` loses**, and it is
  now 16 examples over three repositories rather than seven: 0.286 vs GitHub's 0.857 on
  transformers, 0.222 vs 0.667 on the diffusers+mlinter corpus (§10.3). Replicated, and
  losing to grep too.
* **That loss was under-retrieval, not ranking, which is why expansion came before the
  weights.** 6 of the 9 returned nothing at all: the query is drawn from the bot's
  finding, and in 10 of the 14 examples with text terms the only document in the answer's
  thread carrying all of them is the bot finding — which §6.2 excludes from reads. Search
  ANDs content terms, so the query asked for the one document the index refuses to return.
  **Do not re-derive §10.3's controls** — they are recorded on both corpora.
* **Expansion fixed it, and the delta is measured per leg family (§10.4).** `index+expand`
  moves the leak-free rationale row 0.286 → 0.857 on transformers and 0.222 → 1.000 on the
  bot corpus, `failure` 0.938 → 1.000, `precedent` 0.375 → 0.475, regressing nothing. The
  attribution is the part worth keeping: the `filters` leg alone fixes `failure` and does
  nothing for `precedent`; the derived legs alone fix `precedent` and do nothing for
  `failure`; `rationale` needs both. So neither family is redundant, and the obvious
  objection — "this is just the caller's own filters without their text" — is answered.
* **`precedent`'s MRR was expansion's one gap, and the weights closed it (§10.5).**
  0.191 → **0.286** at Recall@10 0.475 → 0.525, measured against §10.4's scoring shape
  reproduced inside the same build rather than against its published table. `rationale`
  leak-free MRR went 0.459 → 0.602 and pooled 0.864 → 0.884. `failure` lost 0.011 of MRR
  at unchanged ceiling recall and **was not compensated for** — recovering it on the one
  slice §10.2 #2 calls close to circular would be fitting noise.
* **Per-term attribution, so a later change knows what it is touching.** `precedent` is
  carried entirely by the signal-overlap terms (lexical-only reproduces the control
  exactly); `failure` entirely by the lexical term; `rationale` needs both. Same
  disjointness §10.4 found between leg families, one layer down.
* **Decay is built and gated off**, and this is the one thing the benchmark decided rather
  than an argument set beforehand. §6's half-life table is a claim about an eight-year
  history; both corpora are six-month samples, so a half-life can only act as an
  age-proportional noise term — and does: `failure` 0.927 → 0.917, `precedent` 0.286 →
  0.259 with it on. The table is kept; `ranking.DECAY_ENABLED` says it is untested rather
  than disproved. **Turn it on with the first full-history backfill.**
* **Filtering ANDs, scoring ORs (§10.6), and the benchmark could not see the difference.**
  The term that scores text a query is *not* filtering on used a conjunction, so of 861
  admissible documents on one query's threads exactly **1** scored above zero; the rest tied
  and the recency fallback picked each thread's last comment, which on a merged PR is the
  approval. Nine of ten slots came back `LGTM`. Recall@k scored it perfect. **A recall
  number is necessary and not sufficient** — nothing in §10 charges for a wasted slot,
  though §6 caps results at ten precisely because slots cost the caller tokens.
* **`empty` has stopped being a diagnostic.** A textless leg almost always returns
  something, so every `empty` count is now 0 — an unanswerable query no longer announces
  itself, and whatever replaces that signal has to be a confidence measure, not a count.
* **`failure` cannot carry a weight set either** — its ground truth is "the threads
  carrying this error" and `--error` matches exactly that (0.810 even with the filter
  dropped). **`precedent` is the discriminating slice.**

§13.1 records the nine decisions milestone 2 settled that the plan had left open, §13.2
the seven extraction settled and §10.1 the four the benchmark harness settled — read them
before assuming the plan describes the code.

## Write briefly

**Hard rule, and it applies to every commit message, doc and comment you touch.**

- **Commit messages describe what the commit did.** Subject line, then at most a few terse
  bullets. No narration of your reasoning, no account of how the plan changed, no essays
  about what the tests caught.
- **Never add a `Co-Authored-By` trailer.**
- **Prose earns its length.** Cut a paragraph that restates a table, a caveat nobody will
  act on, and any sentence explaining why you are about to explain something.
- **Rationale goes where a reader will look for it:** a docstring next to the code, or the
  build plan. Not the commit log, and not a fourth copy in the README.

Compression is not the same as omission. The API limits below, and every "why" attached to
an invariant, are load-bearing — keep those and cut around them.

## The four invariants

Each has a test. If you find yourself weakening the test to land a change, you are about
to break the property the test exists for.

**1. The client cannot reach the index.** The module graph from `ghlore.cli` contains no
database driver, no GitHub client, no web server, no ML stack, and no
`ghlore.store`/`github`/`api`. Neither does `ghlore.code`, which runs in *both* binaries.
Base `pip install ghlore` is the client and must stay small enough for a constrained agent
sandbox; `[server]`, `[postgres]`, `[code]`, `[python]` are extras, and a verb whose extra
is missing exits with an install hint, never an `ImportError`.
→ `tests/unit/test_module_boundary.py`, which checks this **twice**: statically over the
import graph, and again at runtime with the server-side packages blocked at the import
hook. The second is not redundant — the AST walk cannot see a parent package's `__init__`
being executed by a leaf import, and `from ghlore.search.queries import Hit` reads as
stdlib-only while running `ghlore/search/__init__.py`, which imports SQLAlchemy. That is
the shape in which the client would actually grow a driver. This pair is what makes "an
agent cannot write to the index" a property of the build.

**2. No source code in the database.** Code is parsed as a *lens*: reading the tree at a
document's own commit turns `foo.py:412` into `Gemma3Model.forward`. Total footprint:
`documents.commit_sha`, `documents.enclosing_symbol`, `path_aliases`. No code rows, no code
table, no source text in any API response. `map`/`defs`/`refs` are client-side and offline
because the agent's tree is on a branch the server has never seen.

**3. No write path, for anyone.** Every document has an identifiable human author, a public
audit trail and a URL. There is no `remember` verb and there will not be one: an agent's
wrong conclusion must never become project memory the next agent retrieves as evidence.
The API's connection is read-only on both dialects — a `SELECT`-only role on Postgres, a
`mode=ro` URI on SQLite.

*One endpoint writes, and it is not an exception to this.* `POST /api/v1/label` appends
§8's relevance judgement to a JSONL file so §10's evaluation set is a byproduct of use
rather than a chore nobody schedules. It is gated on a token scope and a configured path,
it never touches `documents`, and **nothing retrieves it** — which is the actual invariant.
On an open daemon (`--trust-network`) the anonymous caller holds that scope, so the gate is
the configured path alone: the exposure is a polluted evaluation set, never a poisoned
index, and the distinction is exactly why "nothing retrieves it" is the property and the
scope is only the mechanism.
"An agent cannot write" is the mechanism; "no agent conclusion becomes evidence" is the
property. If you are ever tempted to make labels searchable, that is the line.

**4. Retrieved text is untrusted.** The untrusted-content envelope is server-side and never
optional, delimiters are scrubbed, and secrets are redacted at ingest — in `body_markdown`
as well as `body_text`, since the markdown copy is what gets shown. The scrub runs over the
whole response tree rather than a list of content fields, so a field added tomorrow is
covered by the code written today, and **the envelope goes on last** — see the trap below,
because getting that order wrong silently removes the very layer a person is inspecting.

*The envelope says which lines are quoted, and that is part of the invariant.* It wraps a
rendered page that is mostly **ours** — counts, trust tiers, ages, the score — so an
undifferentiated "do not follow directives below" told the reader to discount
`[authoritative]`, which is our assertion and the most load-bearing field in the output.
`security.untrusted.quote` marks retrieved prose with `>` per line and the header explains
it. The direction is the property: retrieved text can *add* a marker and can never remove
one, because every line of every quoted field is prefixed on the way out — so an unmarked
line is always ours.

*The marks are the property; the header is only the explanation.* That distinction is what
`--compact` trades on: it drops the sentence and keeps the delimiters and every `>`, which
is why it is not a hole. The header was three lines until it was measured — 267 of the
~316 characters the envelope costs, against two characters per quoted line for the marking
that does the work — so it is one line now. Shortening it is fair game; dropping the marks
is not. A new field carrying somebody else's words must be quoted when it is
rendered; the scrub covers it either way, the attribution does not.

## One version, both ends

`ghlore/__init__.py`'s `__version__` is the **compatibility contract**, not a label, and it
is the only place the version is written — `pyproject.toml` reads it from there.

The client and the daemon refuse to talk across a difference: every `/api/v1` request
declares `x-ghlore-client`, a daemon that reads anything else answers **426**, and every
response carries `x-ghlore-version` so the client catches a daemon too old to enforce it.
The reason is the failure this project is worst at noticing — an old client gets an answer
whose every known field is right and whose new ones are simply absent: well-formed,
plausible, silently incomplete (§13.3). `ghlore/wire.py`, and it is stdlib-only because
invariant 1 puts it in the client's import graph.

Two rules follow, and forgetting either is felt by somebody else:

1. **Bump it in the same commit as any change a client can see** — a wire payload, a
   renderer, a CLI flag. The minor is the completed milestone; the patch is releases
   within it.
2. **The bump and the deploy are one operation.** A bump on `main` that is not shipped
   breaks every client installed after the merge; they are told the deployment is behind,
   which is true and is nobody's intent.

→ `tests/unit/test_wire.py`, `tests/integration/test_api.py` ("the version handshake"),
`tests/integration/test_cli_client.py`. A test client that talks to `/api/v1` sends the
header, because every real client does.

## Commands

```bash
make install    # .venv on the 3.10 floor + editable install with all extras
make format     # ruff format + ruff check --fix
make check      # lint + test — run before you commit
```

Python **3.10+**. **Linting is ruff, full stop** — no Pyright or mypy gate. Ignore IDE type
diagnostics; they are not CI and must not drive changes.

`make test` needs no database. Add Postgres — see below for why you must:

```bash
docker run -d --name ghlore-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=ghlore_test \
  -p 55432:5432 pgvector/pgvector:pg16
GHLORE_TEST_POSTGRES_URL=postgresql://postgres:test@127.0.0.1:55432/ghlore_test make test
```

`pgvector/pgvector:pg16` rather than plain `postgres`, so migration 2 applies in full. CI
runs the same and **fails if the Postgres half was skipped**.

Two more optional binaries, each gating tests that **skip by name** rather than pass
quietly: `universal-ctags` for the fallback provider's conformance tests (macOS's
`/usr/bin/ctags` is BSD ctags and does not count), and `node` to syntax-check the UI's
inline script.

To look at an index by hand:

```bash
export GHLORE_DATABASE_URL=sqlite:///ghlore.db     # a laptop index
ghlored migrate && ghlored poll --repo owner/name --once
# or a bounded corpus, each thread whole (section 5.5) -- not a small backfill:
ghlored sample --repo owner/name --since 2026-03-01 --kind issue
ghlored sample --repo owner/name --since 2026-06-01 --kind pr --merged
ghlored authority --repo owner/name                # else every MEMBER stays `reported`
ghlored serve --allow-sqlite                       # refuses SQLite without the flag
# no GHLORE_API_TOKENS set => loopback only, unless --trust-network says the network in
# front of the process is the perimeter (which is how the deployment runs: Tailscale-only,
# internal load balancer, read-only responses over public GitHub history)
# GHLORE_API is not optional here: the client's default is the deployment.
GHLORE_API=http://localhost:8080 ghlore search "some error text"
```

## Layout

| Path | Holds |
| --- | --- |
| `ghlore/github/` | REST client, GraphQL client, bulk walks, per-thread fetch |
| `ghlore/ingest/` | normalize, chunk, extract, `index_thread`, poll, backfill, sample, sweep, plugins |
| `ghlore/store/` | schema, migrations, repository, **`dialect.py` — the only dialect branch** |
| `ghlore/search/` | `queries.py` (the vocabulary and the caps), `backends/` one per dialect, `expansion.py` (§6's fan-out and the merge); `ranking.py` is the §6 weights, unbuilt |
| `ghlore/api/` | `server.py` (routes + the response scrub), `tokens.py` (scope and limits), `schemas.py`, `ui.py` (the §8 page) |
| `ghlore/code/` | the lens, in **both** binaries; `providers/` is one module per language, `registry.py` the entry-point seam |
| `ghlore/security/` | untrusted-content envelope, secret redaction |
| `ghlore/bench/` | §10's benchmark: `dataset.py` the frozen set, `mine.py` the candidates, `judge.py` the label fold, `run.py` the runner and the two baselines |
| `ghlore/render.py` | dicts in, text out. **One renderer**, shared by the CLI and the UI's raw view — stdlib only, which is what lets it sit on both sides of the boundary |
| `ghlore/guidance.py` | the prose that describes the verbs. **One owner**, rendered into `--help` and the page, and the source of the verb list `tests/unit/test_prose_surfaces.py` holds `docs/cli.md` and the skill to. Text and a lazy parser read, nothing else |
| `ghlore/cli.py` | the client. Imports none of the above except `code`, `render` and `security` |
| `benchmarks/` | §10's evaluation sets, one JSONL per corpus. Data, not code — the header carries the corpus window and every example carries who judged it |

Anything that knows a *particular* project's conventions is an extension, not core (§9).

## Storage: Postgres in production, SQLite in tests and on laptops

Tables are SQLAlchemy **Core**, not the ORM: §5.1's one-transaction guarantee must be
readable as SQL, not inferred from a flush order.

SQLite is a development affordance, **not a deployment**. §4.1 is required reading before
touching the schema. In short:

- The **portable core** is every table except what is marked `POSTGRES ONLY`, and it must
  behave *identically* on both dialects.
- The **search layer does not port.** `tsvector`+GIN, `pg_trgm` and `vector(384)` are
  Postgres-only; SQLite gets FTS5 and no trigram or vector tier. Retrieval sits behind a
  `SearchBackend` per dialect, and `status` prints which one you are talking to.
- SQLite's FTS5 index is kept in step by **triggers** created in migration 3, not by the
  ingest path: maintaining it there would put a dialect branch in `repository.py`, which
  the test below forbids. The write path stays one portable upsert and knows nothing.
- **Never tune ranking or run the §10 benchmark on SQLite.** The weights are fitted to
  `ts_rank_cd`; SQLite scores with `bm25`. A number from the wrong backend describes a
  different engine. The benchmark records its backend and refuses to mix.
- **`ghlored serve` refuses a SQLite URL** without `--allow-sqlite`.
- **`store/dialect.py` is the only module that may branch on the dialect.** An
  `if dialect ==` in `repository.py` is a bug, and `tests/unit/test_no_dialect_leak.py`
  says so.

Four traps, all in §4.1: SQLite autoincrements only an exact `INTEGER PRIMARY KEY`, so keys
need `BigInteger().with_variant(sqlite.INTEGER(), "sqlite")`; `func.now()` yields a *naive*
value, so set timestamp defaults in Python as aware UTC; the upsert construct is
dialect-specific; and `text[]` is gone — `threads.labels` and the `precedents` arrays are
child tables.

A green suite that only ever ran SQLite is a green suite about the wrong database.

## Traps

**A derived column is null until something re-derives it, and the fallback is the old
behaviour.** `thread_files.source` (migration 7, huggingface/ghlore#17) is filled by
`extract_signals`, so on an index migrated but not re-derived every row reads null and
`_files_by_provenance` falls back to inferring from `change_type` — which cannot tell an
inline comment's anchor from a filename in prose, exactly the conflation the column exists
to end. The deploy is not the fix; `ghlored derive` is. Same shape as `trust` after
`ghlored authority`, and as `thread_links` after §13.3.

**The GitHub API lies about its own limits, quietly.** All five were measured, and a
backfill written without them looks successful while missing most of the corpus:

- `GET /issues?page=N` **422s past ~page 100** → cursor pagination. The client follows
  `Link: rel="next"` verbatim and never builds a `page=` itself. (Passing `params={}` while
  following a Link URL makes httpx *drop* the page and loop forever — pass `None`.)
- `GET /issues/comments` is **hard-capped at 30,000 items**, and `since` + `direction=asc`
  does **not** lift it → chain on the last item's `created_at`.
- `GET /pulls/comments` is **not** capped, but **cannot always be served at
  `per_page=100`** -- each comment carries a `diff_hunk`, and GitHub times out building the
  page (504, or 502 from inside a cluster). The walk asks for **50**; 30 also works, 100
  does not. This wedged the first production backfill *permanently*, and it is the failure
  class retries cannot reach: the walk resumes from the same `since`, asks for the same
  unservable page, and dies identically for ever. **The tell is a `high_water` that stops
  advancing while the process keeps restarting -- check the mark, not the restart count.**
- There is **no repository-wide reviews endpoint** → batch over GraphQL (~25 PRs/query),
  merged PRs only. On the backfill path this is the *only* source of review bodies.

**A 5xx is an answer; a dropped connection is not, and both have to be retried.**
`request` retries on two different signals — a status (5xx, or a rate limit) and an
exception that arrived with no status at all (`TRANSIENT_TRANSPORT_ERRORS`). Only the first
existed until the first production backfill, where GitHub closed a connection mid-body
every few minutes: the exception propagated straight out of the retry loop and the Job
restarted **five times in 101 minutes**. §5.2's per-pass cursors made that survivable
rather than lossy, which is precisely why it was cheap to miss — the walk still converged,
just in fourteen-minute pieces. `LocalProtocolError` is deliberately *not* retried: that
one describes a request we built wrong, and asking again cannot fix it.

**Deletion has to reach `raw_objects`.** Staging is an upsert, so a deleted comment stays
staged and every later `derive` rebuilds its document. A thread fetch is authoritative for
what the thread contains — prune what it did not return, scoped to the object types you
actually re-read, so a poll cannot delete the per-PR pass's work.

**Metadata is part of document identity.** `content_hash` covers the normalized text *and*
the metadata, because a review comment's file and line are part of what it is. Hash the
text alone and a comment whose line moved keeps its stale location for ever, and the lens
resolves the wrong symbol from it.

**But the hash is not the identity of the *row*.** `trust` is derived from the payload plus
`repo_authority`, so resolving an author's write access changes the tier without changing a
byte of text. A reconcile comparing hashes alone reports "unchanged" and the stale tier
stays for ever — so `reconcile_documents` compares the hash **and**
`repository.DERIVED_COLUMNS`. Add `enclosing_symbol` to that tuple when the lens lands; it
has the same shape of input, and the same failure without it.

**`MEMBER` means "we have not asked yet", not "no".** On an org-owned repository the real
maintainers report as `MEMBER` because their access comes through a team. Until `ghlored
authority` runs, every one of them is `reported` and the `authoritative` tier is **empty** —
which does not look like a bug, it looks like a repository with no maintainers. Run the pass
after any backfill of a new repository.

**A `since` on the bulk passes truncates threads, and `sample` exists because of it.**
`GET /issues/comments?since=` filters *individual comments*, so a date-sliced backfill
indexes a thread that moved inside the window **without the older comments that explain
it** — a hole inside the retrievable unit, and the discussion is what §10's failure slice
is ground truth for. `huggingface/transformers` #17611 is the shape of it: opened 2022,
updated last month, 124 documents, of which a bulk `since` walk would have staged two. So
`ghlored sample` fetches thread by thread (§5.5) — the expensive path per thread and the
cheap one at 2,000 threads. It checkpoints under **its own** pass name (`sample:issue`,
`sample:pr`) and never `threads`, because that mark is shared with the poll and the
backfill: moving it would tell them the years before the window were covered. And it
records its floor in `repo_sample`, which `status` prints — `high_water` says where a pass
*reached*, which on a sampled index reads as "covered" for ground it never walked, and
§10's numbers are only comparable against a baseline restricted to the same window.

**Two checkpoint rules a reimplementation gets wrong** (§5.2 rules 5 and 6). The mark stops
at the first failure in a pass — threads are processed in ascending *timestamp* order, so
advancing past a failure skips that thread for ever while the pass looks healthy. And a
capped walk bounds how far the mark may move, because the uncapped second list query can
surface a thread far newer than anything the capped one reached.

**A full `ghlored derive` derives the threads that are *staged*, not every staged
number.** A comment walk stages a comment under its thread number, and the thread can be
deleted or transferred upstream before the thread walk sees it — so a number can hold
comments and no issue. 20 of the 47,928 numbers staged for `huggingface/transformers` are
that, one of them 40 threads into the list. Because `derive` keeps no cursor, collecting
every staged number made the pass die at the same number on every restart: a Job that
restarts for ever and never progresses, which is the shape the checkpoint rules above
warn about. `sweep` already skipped these with a warning; `derive` now pre-filters on
`THREAD_HEAD_TYPES` and **reports the count** rather than skipping quietly.

**Two silent-success classes.** A run that stops early and exits 0 is the failure mode
here. Assert: the high-water mark advances only per *committed* thread; killing a pass
leaves the next pass re-covering the remainder; a thread sharing the high-water second is
not skipped; re-indexing an unchanged thread performs **zero** document writes.

**Empty is not an error.** No results is exit 0 with an empty result, always. Two places
this is easy to break: a daemon that is down must not read as an empty index (the client
tells the two apart and says which), and `ghlore map` with no provider installed used to
walk zero files and return an empty map at exit 0 — a missing install wearing a real
answer, which is what `registry.require_any` exists for.

**A filter inside an already-selected unit is not a filter.** `thread --focus` scored a
conjunction and *selected* on it, which is right for corpus-wide `search` — the AND is
what stops a pasted sentence matching everything — and wrong inside one thread, where the
thread *is* the admission decision. `--help` invites a question ("select the comments that
answer this") and any question specific enough to be useful matched nothing: measured on
`huggingface/transformers#28056`, 30 comments, `cache` → 10, `use_cache gradient` → 2,
`use_cache gradient checkpointing warning` → **0**. A focus now orders and never empties;
Postgres ranks the whole thread with the disjunction `unfiltered_fts_score` already builds,
FTS5 leads with its matches and fills from the chronological remainder, and
`focus_matched` reports how many carried every term.

**The envelope goes on after the scrub, never before.** The scrub cannot tell our
delimiters from an attacker's — that is what makes it a scrub — so rendering first strips
the envelope's own delimiters out of the rendered text, and the UI's "view as the model
sees it" then shows a page with the one layer a person is inspecting quietly removed.
`api/server._json` takes a render *callback* for exactly this reason.

**A signal filter has two sides, and the error term only works if both normalize.** §5.3
stores an error message with addresses, absolute paths and counts stripped out; a caller
pastes what their terminal printed. So `SearchQuery` runs the request through
`extract.error_query_form` — skip that and every `--error` query matches nothing, which is
indistinguishable from an index with nothing to say. The same pairing is why extraction
lives in `ingest/extract.py` rather than inline in `index_thread`: `search/` has to be able
to call it. `search/expansion.py` needs it on the error line it pulls out of a traceback,
and calls `extract_signals` itself for the same reason — a leg that normalized differently
would filter on a string the index does not hold.

**Extraction reads the *documents*, never `raw_objects`.** Redaction (§11.3) happens on the
way into `documents`, so extracting from the staged payload would let a secret that was
kept out of `body_text` reappear in `thread_symbols`. The one input that arrives unredacted
is a review comment's `diff_hunk` — it is metadata, stored verbatim — and `extract.py`
redacts it itself. It also runs in the *same transaction* as the documents it read, for the
reason §5.1 is one transaction: a thread whose signals describe a body it no longer has is
worse than one with no signals, because `--file` would answer from it.

**A definitive path is not run through the prose heuristic.** The path rule decides whether
an ambiguous *token* is a path, so it answers no for `Dockerfile` and `.gitignore`. GitHub's
changed-file list and an inline comment's own `path` are not guesses — filtering them cost
15 of 616 file rows on `huggingface/serge`, which was every extension-less file in the
repository.

**The result cap is a window function, not a trim after the fetch.** Fetching N rows and
capping them in Python caps the wrong set: a thread with forty matching comments fills the
fetch before a second thread is ever read, so the page is one thread whatever the cap says.
And the score has to be materialized in its own subquery first, because FTS5 refuses
`bm25()` inside a window's `ORDER BY` ("unable to use function bm25 in the requested
context").

**Machine-authored documents are excluded as a security property, not a ranking choice.**
The corpus contains the deployment's own agent's comments; returning one as prior discussion
makes the agent's unreviewed output its own evidence. `trust=machine` is a separate,
explicit question, never a lowered floor, and no query kind is a way through it.

**`ctags` on PATH proves nothing.** macOS ships BSD ctags as `/usr/bin/ctags`, which has no
`--output-format`; the provider probes for "Universal Ctags" and reports itself unavailable
otherwise, because the alternative is a silently empty definition list. Its tests skip by
name, the same way the Postgres half does.

**Error recovery has a ceiling and the repo map has a resolution limit.** An unclosed `(` is
a legitimate reading in which the rest of the file is one expression, so definitions below
it disappear — recovery is lower fidelity, not always complete. And the map still counts a
*written name*, so three classes each defining `info` share one count — which is now
printed as `matches / 3 definitions` and divided by that 3 for the ranking, because the
undivided count produced a map with no information in it: on `huggingface/transformers`, 35
of the top 40 were `__init__` at exactly 9,198 and the other 5 were `.to` at 12,637, with
`Rotation.to` in an openfold utility credited with every `.to(` in the repository. Dunders
are excluded outright and the count of them is reported. Both limits are declared in tests
rather than smoothed over.

**A reference is not only a call, and every occurrence says what it is.** `refs` matched
call sites alone until it was measured against a real sweep: 21 of ~400 occurrences of
`compute_default_rope_parameters`, missing
`rope_init_fn: Callable = self.compute_default_rope_parameters` — a value read through an
attribute, in the file being debugged. Twenty-one correctly formatted lines with no
denominator are worse than an error, because a caller in an unfamiliar repository has no
prior to check them against. So the engine reports `call`, `definition`, `attribute` and
`name`, keyed on the *name's byte span* so one occurrence is one row however many node
types describe it, and the CLI prints the counts per kind. The kinds and the node-type map
belong to the provider (rule 1); core groups and counts.

**Result caps are the contract, not a default.** `search` ≤10 hits and ≤400 chars of
snippet, `thread` never returnable in full, `precedent` ≤5. A client may ask for less,
never more.

*The opening post is the one exception, on request.* "Never in full" is about the
**comments**, which are unbounded; a body is one document whose length is whoever opened
the thread, and truncating it silently at 800 characters was the one gap that sent a
diagnosis session back to `git clone` — a `transformers` issue template spends its opening
on `### System Info` and `### Who can help?`, so `### Reproduction` started at almost
exactly the cut. `thread --full` serves all of it (every chunk, not chunk 0), and the
capped form reports `body_chars` so a caller knows there is a rest. Structure-aware
selection — dropping the template's boilerplate headings inside the same budget — would be
the cheaper 80% and is **not** core: it knows one project's conventions, so it is an
extension (section 9).

**Say what is missing, every time.** The four defects above share one shape: well-formed,
confident, silently incomplete output. A focus that emptied a thread, a `refs` sweep at 5%
of reality, a changed-file list truncated to the first API page, a body cut at a template
boundary. Each one produced a wrong conclusion that had to be walked back, and none of them
looked like a failure. So, for every verb that subsets or filters:
`returned` **and** `total`, name what was excluded, never return empty where a degraded
answer exists, and mirror both into `--json`. The sharpest form of the rule: **a truncated
list must never be able to answer a membership question** — `x in files` returning false
has to be distinguishable from "we did not collect that far", because a missing *entry*
reads as a negative fact where a short list of hits only reads as a weak positive.

## Do not add

Each was deferred **with a reason**; adding one "while nearby" undoes an argument, not an
oversight. To change one, change the plan first and say what measurement changed your mind.

| Not doing | Reasoning |
| --- | --- |
| A code table, code corpus, or `language` column | §1 — the extension already carries the language |
| An agent-write path | §11 |
| Vector search | §6.1, §10 — provisioned, unwritten, gated on a measured benchmark |
| LLM-generated summaries | §1 — v1 writes only mechanically-derived text |
| An MCP server | §1 — build it when a shell-less client exists |
| Ranking work before the benchmark | §10 — score against GitHub search *and* the agent's own `grep` |
| A **committed** code index (`.ghlore/` in the indexed repo) | §1 — it moves the staleness defect inside the client, and makes repo-supplied *structure* an untrusted input steering `defs`/`refs`. A **git-ignored** cache is a separate question with profiling behind it: 0.14 s over 73 files, ~10 s over 2,990 |

## Writing tests

Name the failure class, not the function. `tests/unit/` is fast and pure;
`tests/integration/` runs against `tests/fixtures/fake_github.py`, which serves through
`httpx.MockTransport` so the real client's pagination, 422 and rate-limit paths execute.
Mocking the client instead would test nothing about the limits above.

The load-bearing assertion is **idempotency**: index a fixture twice, assert zero mutations
on the second pass. Store tests are parametrized over both dialects and **skip** Postgres
when unconfigured — they never silently pass. Language providers share one conformance
suite, including a file with a syntax error.

**Prose is code here too.** Four surfaces described the verbs, one had a drift test, and
that was the only one still correct a release later — the skill was telling agents not to
use the two verbs that answered a real maintainer's question. `test_prose_surfaces.py`
parametrizes the invariant over all of them: every surface names every shipped verb in
command form, every runnable example of a repo-scoped verb carries `--repo`, and no page
calls a shipped verb unimplemented. Derive the verb list from the parser, never copy it.

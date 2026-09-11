# Worked examples

Setup, install and token wiring are in [`operations.md`](operations.md); the *why* is in the
build plan, which is held with the deployment that commissioned it. This page is neither —
it is what the queries actually look like, and the four ways they come back empty when the
index is healthy.

Every command below was run against a real index of `huggingface/serge`: 114 threads, 415
documents, Postgres backend.

**The sample output is elided.** A real response is wrapped in the untrusted-content
envelope, and inside it the quoted lines — a title, a snippet, a body — each carry a `>`
so that our counts and trust tiers cannot be mistaken for something a stranger wrote. The
blocks below drop both, to keep the shape of the answer readable.

---

## The verbs

| | |
| --- | --- |
| `search QUERY` | the index. Filters: `--error`, `--test`, `--file`, `--symbol`, `--label` (repeatable, ANDed with the text); `--kind failure\|precedent\|rationale`; `--trust authoritative\|machine`; `--repo`, `--since`, `--limit`, `--sort newest`, `--no-expand` |
| `thread N` | one thread. `--focus "…"` orders its comments and never empties them; `--full` serves the opening post whole, reproduction included |
| `inflight N` | is somebody already fixing this? Threads claiming to close `N`, open ones first |
| `status` | counts, per-source freshness, and which search backend answered |
| `map` / `defs` / `refs` | your local checkout, no server and no network |
| `why PATH:LINE` | the review comments left on this line's code when it was written — milestone 4, a stub today |
| `precedent` | completed units of work — milestone 4, a stub today |

Global: `--json` for machine-readable output, `--compact` to trim snippets, `--api` to
override `GHLORE_API`. `--json` and `--compact` are accepted on **either side** of the verb.
No results is exit 0 with an empty result, never nonzero — so an agent cannot mistake
"nothing in the index" for "the tool is broken".

---

## Query like this, not like that

**A query is an AND of every content term.** The backend uses `plainto_tsquery`
deliberately — it is the one thing FTS5 can also express, so both dialects answer the same
question instead of diverging quietly (§4.1). The practical consequence is the single
biggest cause of a disappointing result:

| query | hits |
| --- | --- |
| `429` | 3 |
| `timeout budget` | 2 |
| `rate limit` | 4 |
| `crash when the optional mask is missing` | **0** |

Two or three distinctive terms, not a sentence — that table is what one AND-ed sentence
costs you.

**A pasted traceback is the exception, and it used to be the worst case.** §6's query
expansion now fans one call out into capped error, test-id, symbol, file and free-text
legs and merges them, so the traceback's lines stop being required terms and become
separate questions. Either form works:

```bash
ghlore search "$(pbpaste)"             # the traceback, expanded server-side
ghlore search --error "$(pbpaste)"     # the traceback, as a structural filter
ghlore search "optional mask dtype" --no-expand   # exactly one question, no fan-out
```

Expansion also reads a bare identifier as one: `ghlore search "_maybe_import_sdnq"` asks
the symbol filter as well as the text, without you having to say so. It never *loses* a
result the plain query would have found — the caller's own query is always one of the legs
— and a query with no identifier, path or traceback in it expands to that single leg and
costs nothing extra.

## An error you just hit

The highest-value shape. It lands on the PR that fixed it:

```
$ ghlore --compact search "429" --limit 2

1. huggingface/serge#92 pr  [authoritative]  15d  body  @tarekziade
   Survive a rate limit instead of losing the task to it
   ## Why One 429 ended the `deepseek_vl` task on 2026-08-18 *after it had already
   spent 1.13M input tokens*…
   https://github.com/huggingface/serge/pull/92
```

`--compact` and `--json` are global flags, accepted on **either side** of the verb:

```bash
ghlore --compact search "429"      # both work
ghlore search "429" --compact
```

Every hit carries its **age** (`15d`) and its **trust tier** (`[authoritative]`). Age
changes what a model concludes; authority changes it more (§6.2).

## "Is this intentional?" — raise the trust floor

```
$ ghlore --compact search "repeat guard" --trust authoritative

2 hits · trust floor: authoritative
1. huggingface/serge#99 pr  [authoritative]  5d  @tarekziade
   Count what a session spent, and what ended it
```

A drive-by comment outranking a maintainer's one-line correction is *worse* than an empty
result: the agent acts on it and nobody checks. `--trust` may only **raise** the floor.

Better, pass `--kind` and let the **server** choose it — a caller then gets the policy right
without knowing it:

```bash
ghlore search "<terms>" --kind rationale   # floor: authoritative
ghlore search "<terms>" --kind failure     # floor: any human tier -- a stranger's
                                           # traceback is real evidence
ghlore search "<terms>" --kind precedent   # authoritative, or any human tier on a merged
                                           # PR: merging is the maintainer's act, so a
                                           # first-time contributor's merged change counts
```

`trust_floor` in the `--json` response reports what was actually applied.

Both are inert until `ghlored authority` has run: maintainers whose write access comes
through a team read as `MEMBER` and stay in `reported`, leaving the `authoritative` tier
empty.

## "What did our own bot claim here?"

Machine-authored documents are excluded from every default result set, because returning
one as prior discussion makes an agent's unreviewed output its own evidence (§11). Asking
for the tier explicitly is the only way to see them, and they arrive labelled:

```
$ ghlore search "review" --trust machine

1. huggingface/serge#26 pr  [MACHINE — our own bot, not evidence]  2mo  @sergereview
```

## One thread, without all of it

```
$ ghlore thread 92 --focus "backoff retry"
...
-- 10 of 30 comments, best first for 'backoff retry' (2 of 30 carry every term) --
(20 not shown: a thread is never returnable in full.)
```

`--focus` **orders** the comments, it never selects them: the thread is the admission
decision, and there is none left to make inside it. So a question that no single comment
answers word for word still comes back as that thread's ten most relevant comments, and the
count says how many carried every term — `0 of 30` next to ten comments means "nothing
matched your wording, here is the thread in order" rather than "nothing here".

**Without a focus you get a sample, and the page says so.** Ten of eighty-nine comments
cannot be a ranking when nothing was asked, so the unfocused page is the first two, the
last two, and six spread across everything between — reviews first, because a review with
a state is an act and `🤗` is not:

```
-- 10 of 89 comments, SAMPLED not ranked: the first and last few and a spread of the middle --
(79 not shown: a thread is never returnable in full. `--focus "<what you care about>"` ranks all of them.)
```

That wording is the fix for a real misreading: a bare `-- 10 of 89 comments --` was read as
the ten *best*, so the agent concluded the thread held nothing further — while the review
that answered its question sat at position 51 of 97. **If you have a question, pass
`--focus`;** the sample is for orientation, not for deciding.

**A long comment comes back as one hit, marked.** GitHub comments are chunked into several
indexed documents, and two chunks of one comment used to arrive as two hits with the same
URL, author and tier — which reads as two people agreeing. Now they collapse into one slot,
and a hit that is a piece of something longer says which piece:

```
1. huggingface/transformers#39847 pr  [authoritative]  12mo  issue_comment  @zucchini-nlp  (passage 4 of 7 in this comment)
```

A 200-comment thread has no full form; `search` is capped at 10 hits and 400 characters of
snippet. The caps are the contract, not a default (§6). The *body* is the exception, and
only on request: `--full` serves the opening post whole, because an issue template spends
its first several hundred characters on environment boilerplate and the reproduction
starts after it.

```bash
ghlore thread 48630 --full            # the whole opening post, reproduction included
```

### "Did this pull request touch that file?"

Read the right list. A thread's paths come from three places and they are three keys, never
one:

```
changed files: 100 of 323 collected. TRUNCATED: a path that is absent here may still have been touched
  src/transformers/modeling_rope_utils.py, …
  + 2 more the diff must contain, from the files inline review comments are anchored to (not part of the collected page)
mentioned in the discussion: 5 — named by somebody, NOT the diff. A bare filename here is not evidence the thread changed it
  config.json, modeling_rope_utils.py, …
```

- **changed** is the diff, collected 100 rows at a time by the per-PR pass. **Absence
  proves nothing** while it says TRUNCATED.
- **anchored** is also the diff — GitHub will not anchor an inline review comment anywhere
  else — and is the one source that can name a path the 100-row cap dropped.
- **mentioned** is prose: a bare basename, a traceback's path, a file somebody merely
  brought up. `config.json` above is named in `huggingface/transformers#39847`'s discussion
  and is **not** in its 323-file diff. Presence here is not evidence of a change.

Merged into one array — which is what it used to be — that last line answered a membership
question with a wrong yes, and the same file appeared twice in two different spellings.

**`--repo` is required on a bare number when more than one repository is in scope.** The
deployment indexes two, so `ghlore thread 47720` there answers
`400: pass repo=: more than one repository is in scope ['huggingface/serge',
'huggingface/transformers']`. That is deliberate — a number alone is ambiguous and guessing
would silently answer about the wrong project — and the message lists what to choose from.
It has nothing to do with authentication: an open daemon resolves the anonymous caller to
every indexed repository, so the same rule applies. `search` needs no `--repo` because it
spans the whole scope by design.

## "Is somebody already fixing this?"

Ask it **before** diagnosing. It is one hop through the `Fixes|Closes|Resolves #N` edge,
and it prevents an agent's most expensive mistake — writing a patch for something already
in review.

```
$ ghlore inflight 48630

1 thread claims to close huggingface/transformers#48630

1. huggingface/transformers#48672 pr  open  8h  closes  @somebody
   fix: respect partial_rotary_factor in GPTNeoXJapaneseRotaryEmbedding
   https://github.com/huggingface/transformers/pull/48672
```

Every row carries `state`, `draft` and whether it merged, because those imply opposite next
actions: an approved pull request means stop, a stale draft means supersede it, a merged one
means the fix has shipped and the issue may simply need closing.

The **empty** answer is the one to read carefully. `nothing in the index claims to close …`
is an answer; the same sentence followed by *"this repository has no relationship rows at
all"* is not — it means the index was derived before the edge existed, and `ghlored derive`
fills it. There is no trust floor here: the deployment's own bot having an open fix is
precisely the duplicate you must not create.

## The offline verbs

`map`, `defs` and `refs` need **no server, no index and no network** — they read the
checkout you are standing in, uncommitted edits included, which is the whole point (§1).

```
$ ghlore defs ghlore/ingest/authority.py
43-56        class     AuthorityResult
59-117       function  resolve_authority
120-156      function  _resolve_one

$ ghlore refs resolve_authority
./ghlore/daemon.py:293  name
./ghlore/daemon.py:298  call
./ghlore/ingest/authority.py:88  definition
./ghlore/ingest/backfill.py:117  call
-- 9 references in 95 files (5 call, 1 definition, 3 name)

$ ghlore map ghlore/ingest --limit 2
74 definitions in 12 files, top 2 by name matches per definition
  (a count of the written *name*: same-named definitions share it, so the definition
   count is how much this row overstates)
  parse_timestamp     function  16 matches / 1 definition   ghlore/ingest/timestamps.py:19
  iso_utc             function  11 matches / 1 definition   ghlore/ingest/timestamps.py:29
```

`refs` reports **every occurrence and what it is** — a call, a definition, an attribute
read, a bare mention — with the counts per kind. Calls alone missed 95% of the sites in a
real sweep, including `self.foo` assigned rather than called, and a short list with no
denominator reads as the whole truth. `attribute` and `name` rows are matched on the bare
name, so they are the last mention of the chain (`a.b.foo` matches `foo`): reading them is
part of the answer, not a promise that a type was resolved.

`map` counts a written *name*, so definitions sharing one share the count — which is why
both numbers are printed and the ranking divides by the second. Dunders are excluded: they
are the same name everywhere, and a top 40 of `__init__` is the same list for every Python
project.

---

## Empty results that are not faults

**No results is always exit 0 with an empty result.** So when something comes back empty,
it is one of these before it is a bug:

**1. The query was a sentence.** See the table above.

**2. A structural filter was involved.** `--file`, `--symbol`, `--error` and `--test` read
the signal tables that build-plan §5.3's extraction pass fills, and they *AND* with the text
query — so one of them narrows an otherwise-good search:

```
$ ghlore search "429"                              → 3 hits
$ ghlore search "429" --file reviewbot/llm.py      → 0 hits   # nothing said both
```

Three things make one match nothing on an index that does hold the answer. `--file` takes
the path **as the repository spells it**, not an absolute one from a traceback — mapping
those needs the working clone of milestone 4. `--test` takes a runner id
(`tests/test_x.py::test_y`, with or without its `[params]`), not a bare function name; a
bare name is a `--symbol`. And `--error` may be given a whole pasted traceback, which is
normalized into the form the index stores — but a *paraphrase* of an error is text, so pass
it as the query instead.

**3. A trust floor removed everything.** `--trust authoritative` — or `--kind rationale` /
`--kind precedent`, which imply it — on an index where `ghlored authority` never ran returns
nothing at all. Check `trust_floor` in the `--json` response.

**4. Two verbs are parsed but not built.** They say so rather than returning an empty
result that reads like an answer:

```
$ ghlore precedent --kind bug_fix
ghlore precedent: not implemented yet (milestone 4, the build plan section 13)
$ ghlore why ghlore/cli.py:10
ghlore why: not implemented yet (milestone 4, the build plan section 13)
```

A daemon that is *down* is a different message from an index that is empty, and the client
tells you which — that distinction is deliberate (§12).

## Checking the index rather than the query

```
$ ghlore status
version   0.3.0
backend   postgresql / ts_rank_cd  capabilities: fulltext
schema    applied [1, 2, 3], pending []
index     114 threads, 415 documents, 332 raw objects
  huggingface/serge [threads] high-water 2026-09-03T06:55:06+00:00 last-ok …
quota     1/60 per minute, 1/5000 today
```

`capabilities` is how you tell which engine answered. A SQLite index scores with `bm25` and
has no trigram or vector tier, so a result set from one says nothing about the other —
which is why `ghlored serve` refuses a SQLite URL without `--allow-sqlite`, and why §10's
benchmark refuses to mix backends.

`version` is the first line because the client and the daemon must be the *same* version.

## "your client is older than this ghlore daemon"

Not an outage and not something to work around:

```
$ ghlore search "429"
ghlore: client 0.2.4 is older than this ghlore daemon (0.3.0), so it would read an
out-of-date answer as a complete one. pip install --upgrade 'ghlore @ git+…'
```

The two ship together, so every request declares its version and a daemon refuses any
other — an old client would otherwise get a well-formed answer missing whatever it does
not know to ask for, which is the one failure nothing downstream can detect. Do what the
message says. If it instead says the daemon is **behind**, the client is fine and the
*deployment* is the stale thing: redeploy it rather than downgrading.

## Parsing the output

With no MCP server, stdout is the API, so what it prints is a contract rather than a
rendering — and the contract is the same whether a person or a pipe is reading. **There is
no TTY branch and there will not be one:** the text an agent reads and the text a person
inspects have to be the same string, which is the same reason the renderer is server-side
and shared with the web UI. A quiet mode for pipes would make the version nobody looks at
the version everybody consumes.

**Prefer `--json`.** It carries everything the text does and nothing a caller has to
un-format: the comments array, `body_chars`/`body_truncated`, the three file lists,
`files_total`/`files_collected`, `comments_total`, `selection`, per-hit `score` with its
`breakdown`, and each hit's `document_id`, `source_id`, `chunk_index` and `passages`.
`--json` and `--compact` are accepted on either side of the verb.

If you do read the text, these hold within a version — and the version is enforced on every
call, so "within a version" is something you can rely on rather than hope for:

- one blank-line-separated block per hit, `N. repo#number type  [tier]  age  source_type
  @author` first, then the quoted title and snippet, then the URL;
- every line of retrieved prose is prefixed `> `, and no line of ours ever is, so an
  unmarked line is always `ghlore` speaking;
- the whole page sits inside `<<<GHLORE-UNTRUSTED>>>` … `<<<GHLORE-UNTRUSTED-END>>>`;
- counts and caveats are prose on their own line (`-- 10 of 89 comments … --`,
  `changed files: …`, `(body truncated: …)`), and a caveat is never dropped for brevity;
- no score is printed. The order is the ranking, and the number's scale is a property of
  the backend — it is in `--json` for whoever is tuning weights.

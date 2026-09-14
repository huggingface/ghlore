# Why not GitHub search

For one-off human lookups `/search/issues` is often the right tool: authoritative,
instantly fresh, free, and it searches every repo at once. As an *agent's* retrieval layer
it falls down. Measured against `huggingface/transformers` (47,513 threads, 134,841 inline
review comments):

| | GitHub search | relore |
| --- | --- | --- |
| Comment-level results | searches comments but **returns the thread** — no snippet, no location | the matching document, with author, trust tier, age and URL |
| Ranking | `best-match`, opaque | explicit weighted terms, tunable |
| "which threads touched this file" | no qualifier exists | indexed join |
| "this bug and its merged fix, as one record" | not expressible | first-class `precedents` |
| Throughput | **30 req/min, shared across the token** | a database query |
| Determinism | ranking may drift | same query, same answer |

Two rows carry it.

**Comments.** Searching that repo for `_prepare_4d_causal_attention_mask` returns 26
threads — 19 matched in *comments*. Three quarters of the mentions of a technical symbol
live in comments, and for each one GitHub hands back a thread number, leaving the caller to
re-read the thread to find what matched.

**Throughput.** 30 req/min is shared by everything using the token — the same token the
triage dispatcher and review bot already spend. One serge task session makes 30–150 tool
calls. Once indexed, **`relore` answers every query from Postgres and makes no GitHub
request at all**; ten agents in parallel cost the same as one. The rate-limited surface is
confined to ingest. The trade is freshness: an answer is at most one poll interval behind.

The deeper reason is not text search: **GitHub search has no schema to join on.** Files,
symbols, test ids, bug↔fix pairs — no qualifier syntax reaches them.

## Where GitHub search wins

Use it, not this, when you need cross-repository reach, an answer that is fresh to the
second, or a repository nobody has indexed. `relore` is a per-repository index with a poll
interval; it is worth its operating cost only where the same history is queried many times
a day. That is also why GitHub search is the benchmark baseline in
[`ranking.md`](ranking.md): if `relore` does not clearly beat it, the index is not earning
its keep.

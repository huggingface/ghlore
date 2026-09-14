# Prior art

- **GitHub search** — complementary, and the benchmark baseline. If `relore` does not
  clearly beat it, the index is not earning its operating cost. See
  [`why-not-github-search.md`](why-not-github-search.md).
- **`opencode-semantic-memory` / GHMEM** — an MCP server with GitLab bulk ingest, the
  closest thing that exists. It embeds issue **title, description and labels**; comments,
  reviews and inline review comments are not ingested, which is the whole ballgame here.
  Forge data is a deliberate second-class citizen there and the only corpus here, its core
  loop is agent-*authored* memory where `relore` has no write path, and it is a
  per-developer local daemon on ~2 GB of `torch` where this is a shared service with a thin
  client. If what you want is "my agent should remember yesterday", use theirs.
- **[Funes](https://huggingface.co/blog/funes)** (`huggingface/funes`) — durable memory of
  **agent session traces**: vector + BM25 fused and cross-encoder reranked, embedded on your
  own machine into a Lance dataset, optionally published as a private HF dataset.
  Complementary rather than competing, and the cleanest illustration of this project's third
  invariant: Funes is append-only *because* recording what your agent did is the point, while
  `relore` has no write path *because* a shared corpus of a project's decisions is worth
  something only while every row has a human author you can check. Funes answers "have I been
  here before"; this answers "has the project been here before". See
  [`how-search-works.md`](how-search-works.md#versus-funes).
- **`yksanjo/gmem`** — agent-authored memory for a Solana workspace. Different corpus;
  worth reading for its append-only `(kind, natural_id, version)` entity model.

Borrowed, with credit: splitting fetch from derive (GHMEM), rendering a result's age,
secret redaction on ingest, LLM-free keyword extraction, progress visible during a
multi-hour backfill, and append-only entity versioning (`gmem`).

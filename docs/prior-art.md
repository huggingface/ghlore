# Prior art

- **GitHub search** — complementary, and the benchmark baseline. If `ghlore` does not
  clearly beat it, the index is not earning its operating cost. See
  [`why-not-github-search.md`](why-not-github-search.md).
- **`opencode-semantic-memory` / GHMEM** — an MCP server with GitLab bulk ingest, the
  closest thing that exists. It embeds issue **title, description and labels**; comments,
  reviews and inline review comments are not ingested, which is the whole ballgame here.
  Forge data is a deliberate second-class citizen there and the only corpus here, its core
  loop is agent-*authored* memory where `ghlore` has no write path, and it is a
  per-developer local daemon on ~2 GB of `torch` where this is a shared service with a thin
  client. If what you want is "my agent should remember yesterday", use theirs.
- **`yksanjo/gmem`** — agent-authored memory for a Solana workspace. Different corpus;
  worth reading for its append-only `(kind, natural_id, version)` entity model.

Borrowed, with credit: splitting fetch from derive (GHMEM), rendering a result's age,
secret redaction on ingest, LLM-free keyword extraction, progress visible during a
multi-hour backfill, and append-only entity versioning (`gmem`).

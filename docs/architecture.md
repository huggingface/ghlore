# Architecture

```text
  GitHub REST + GraphQL          a complete git clone
          |                              |
          |  poll (delta)                |  the lens: line -> name, at each
          |  + backfill (full history)   |  document's own commit; renames
          v                              v
     ghlored index  ------>  Postgres  (threads, documents, signals, precedents)
                                |
                                |  full-text + trigram + exact-token, weighted
                                v
                          ghlored serve  ->  HTTP JSON API + web UI
                                |
                  ghlore (CLI)  |  a browser
                        |
                        |  map / defs / refs: the same parser, run locally
                        v  against the working tree. No server, no network.
                  your checkout
```

## Indexed

Titles, bodies, every conversation comment, every review submission, every inline review
comment (with file and line), commit messages, changed-file lists for merged PRs, labels,
state, and the issue↔PR links connecting a report to the change that closed it.

## Extracted as exactly-matchable signals

Because for technical retrieval the discriminating token is usually exact rather than
semantic: exception types and normalized error messages, file paths, **symbols**, test ids,
commit shas. They live in the signal tables that §5.3's extraction pass fills. These are what `--error`, `--file`, `--symbol`, `--test` filter on, and they
are why an agent can ask *what has been said about this function* without grepping a
checkout for a symbol whose discussion is not in the checkout at all.

The filter flags are repeatable — a traceback names more than one file — and **they AND
with the text query**, so passing one narrows an otherwise-good search rather than widening
it. `--error` takes a whole pasted traceback: the request is normalized into the same form
the index stores.

## Not indexed: your source code

No source text is stored server-side and no API response returns any. Code is nevertheless
*parsed*, because a line number is not a durable address and a name is:

- **Server-side, at index time — the lens.** Reading the tree at a document's own commit
  turns `foo.py:412` into `Gemma3Model.forward`, so "every review comment ever left inside
  this function" survives the file being edited. Also gives rename chains, and is what
  `ghlore why <path:line>` will read — that verb is a milestone-4 stub today.
  Footprint: two nullable columns and a rename table.
- **Client-side — `map` / `defs` / `refs`.** The same parser against your dirty checkout.
  Local and offline, and therefore correct about the branch you are on, which a remote
  index never is.

## Languages are plugins

A provider claims file patterns and declares what it can do (`defs`, `extents`, `refs`).
tree-sitter providers are the good path — they handle the broken files an agent is halfway
through editing — and a `ctags --output-format=json` provider is the breadth fallback, so an
unsupported language degrades to definitions rather than nothing. Python ships first.

The fallback needs **universal**-ctags: the BSD ctags macOS ships as `/usr/bin/ctags` has no
`--output-format`, so the provider probes for the real thing and reports itself unavailable
rather than returning an empty list.

## Vector search

Deliberately **not** in v1: the column and extension are provisioned, nothing writes them,
and turning them on is gated on a measured benchmark. What is used instead — and what it
does not cover — is in [`how-search-works.md`](how-search-works.md).

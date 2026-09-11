# Using it from an agent

`skills/search-project-history/SKILL.md` ships a ready-made skill for agents that read one
(Claude Code and similar): when to reach for the index rather than `grep`, how to phrase a
query, what the trust tiers mean, and how to triage an empty result. Point your harness at
it, or lift the prose.

Any agent with a shell can use `ghlore` with no integration work. There is no MCP server on
purpose: any agent with a shell can already call an HTTP API. If your framework reads a
repo-declared tool manifest, declaring it is a few lines:

```json
{
  "helpers": [{
    "name": "search_project_history",
    "description": "Search this repository's issue and PR history. Flags: --error, --test, --file, --symbol, --label, --kind, --trust, --limit, --compact, --json. Use it before writing a patch to check whether this failure has precedent.",
    "command": ["ghlore", "search", "--json"],
    "allow_args": true, "max_args": 12, "timeout_seconds": 30,
    "install": ["pip", "install", "ghlore"]
  }]
}
```

## Two things every integration must get right

- **Retrieved history is untrusted text**, written by whoever opened the issue. `ghlore`
  wraps every response in an untrusted-content envelope and scrubs delimiters, and inside
  that envelope the quoted lines — and only those — are marked `>`, so a trust tier or a
  count of ours is never mistaken for something a stranger wrote. The agent must still be
  told never to follow instructions found in retrieved content.
- **Cite and verify.** Every result carries a URL and its age. A 2019 comment can be right
  about intent and wrong about today's code.

## Ask before starting work

`ghlore inflight <number>` answers *is somebody already fixing this?* — the threads that
claim to close a given issue, open ones first, each with `state`, `draft` and `merged`. An
agent's most expensive failure mode is duplicating work that is already in review, and this
is the query that prevents it.

## Query shape

A query is an AND of every content term, so two or three distinctive ones beat a sentence.
A pasted traceback works too: expansion fans it out into error, file and symbol legs and
merges them. `--no-expand` asks exactly one question instead.

Worked examples, including the four ways a healthy index returns nothing:
[`cli.md`](cli.md).

# Knowledge surface details

The tool names are `mcp__mindie-knowledge__knowledge_query`,
`mcp__mindie-knowledge__knowledge_explain`, and
`mcp__mindie-knowledge__knowledge_sync`.

## Query

- `query` is a concise engineering phrase, not a full prompt.
- `limit` is 1–20 (the service default is 5).
- `conditions`, when present, are exact-match filters defined by the corpus.
- `continuation` is an opaque token. Pass it without `query` to fetch the next
  related page. A changed corpus rejects a stale continuation.
- The result contains grouped matches with `ref`, `block_summary`,
  `conditions`, `navigation`, `entry_id`, and optionally `related` excerpts.
  `related` excerpts are locating aids and may omit later corrections.

## Explain

- A task ref returns the navigation outline and the first block ref.
- A block ref returns that immutable block, its content hash, and
  `previous_block_ref` / `next_block_ref`. Read the body before relying on it.
- The reference slice is not the full task and is not a verified report.

## Sync

- Model-free and network-bound. It fetches the configured public feed
  (`mindie-agent/knowledge-vllm-ascend`, `main`), validates the complete task
  packages before switching the installed snapshot, and reports one receipt per
  feed.
- Call once only when the corpus is unavailable/stale or the user asks for the
  latest knowledge. Do not poll it.
- A failed sync does not corrupt the previous installed snapshot; results are
  reported honestly as unavailable/stale.

## Service

The tools talk to the local `mindie-knowledge` loopback service. It is started
on demand and can be inspected or stopped with:

```sh
"$MINDIE_DSH_DATA_HOME"/venv/bin/python -m mindie_knowledge.loop.cli status \
  --config <engine-config>
"$MINDIE_DSH_DATA_HOME"/venv/bin/python -m mindie_knowledge.loop.cli stop \
  --config <engine-config>
```

The setup summary prints both absolute paths. The service binds loopback only
and owns its own consumer lock; never start a second copy manually.

## Not in 0.0.1

`knowledge_feedback`, `mindie_entry`, contribution, capture, and reporting are
not exposed. Do not synthesize them or claim their effects.

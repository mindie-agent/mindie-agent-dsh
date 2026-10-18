# Changelog

## 0.0.2 — 2026-10-18

- DSH is a native mindie-bench harness (benchmark PR #2): fresh headless
  profile, broker model route, MCP/knowledge/skill rows and headless JSON
  trajectory parsing.
- G0/G1/G2 CPU L2 canaries pass; G3 native capture remains Codex/Claude-only.
- Version/compatibility metadata refresh; runtime baseline remains 0.0.1.

## 0.0.1 — 2026-10-17

- Native `/mindie-agent` Host entry: init/status/choose/deactivate, sharing and
  reporting controls; first activation asks contribution once.
- DSH v4 public-transcript Stop capture, deterministic redaction, durable
  `mindie-knowledge` material and DSH-model summary indexing.
- Community contribution with default upstream
  `mindie-agent/knowledge-vllm-ascend`, automatic fork resolution/creation and
  explicit failed/unknown publication outcomes.
- Session-bound `knowledge_query`, `knowledge_explain`, `knowledge_feedback`
  and `knowledge_sync`; feedback and diagnostics reporting, including local
  incident recording and the optional reporting worker.
- Stable-only composition: no experimental DSH package and no shipped
  row/preset override.
- Pinned runtime: knowledge `0.11.0`, remote-dev `0.9.5`, diagnostics `0.4.1`.
- Silent auto-update: stable launcher + immutable generation pointer, formal
  release selection, SHA256 verification, live child-plugin swap, rollback,
  update status/check/enable/disable.

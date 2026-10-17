# Changelog

## 0.0.1 — 2026-10-17

- Native `/mindie-agent` Host entry: init/status/choose/deactivate, sharing and
  reporting controls; first activation asks contribution once.
- DSH v4 public-transcript Stop capture, deterministic redaction, durable
  `mindie-knowledge` material and DSH-model summary indexing.
- Community contribution with default upstream
  `mindie-agent/knowledge-vllm-ascend`, automatic fork resolution/creation and
  explicit failed/unknown publication outcomes.
- Session-bound `knowledge_query`, `knowledge_explain`, `knowledge_feedback`
  and `knowledge_sync`; feedback and diagnostics reporting.
- Stable-only composition: no experimental DSH package and no shipped
  row/preset override.
- Pinned runtime: knowledge `0.11.0`, remote-dev `0.9.5`, diagnostics `0.4.1`.

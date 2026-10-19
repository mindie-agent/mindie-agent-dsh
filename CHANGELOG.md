# Changelog

## 0.0.2 — 2026-10-19

- Upgrade pinned shared runtime components to `mindie-knowledge 0.12.2`
  (`4e328305323eafc7fd43ecc0c53eb3cc0541fdef`), `remote-dev 0.10.1`
  (`a6649222aae4851deec5774513f8319aece09051`), and `mindie-diagnostics 0.4.3`
  (`1b5a0eeb79d498c39002fc1853271c17e0f26e66`).
- Unify community knowledge sharing and automatic sanitized tool fault reporting
  under the single persistent switch in `mindie-consent.json` (preserving legacy
  `disabled`/`later`/`read-only` choices and requiring a one-time unified
  re-confirmation for legacy knowledge-only `contribute` records), and install
  persistent background worker scripts under `state_dir/bin/`.
- Supersede the earlier `0.0.2` build
  (`828c375d1f546ed653250526465b8075c1691390`) via an authorized one-time
  same-version replacement; existing `0.0.2` installations require an explicit
  runtime + bundle SHA refresh (`docs/same-version-refresh.md`) and a full DSH
  process restart while preserving user consent and local data.
- Publish reproducible CPU microbenchmark assets (`benchmarks/release.json` —
  `public-transcript` / `public_100_message_parse_latency` and
  `plugin-manifest-hygiene`, `us/op`) alongside `mindie-bench` native DSH
  harness support (PR #2, L1/L2 G0–G2).

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

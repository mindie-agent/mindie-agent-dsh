# Changelog

## 0.1.8 — 2026-10-29

- Make knowledge loading depend on missing task information, permitting direct
  local fixes without mandatory query/explain calls after manual entry.
- Pair with `mindie-knowledge 0.13.6` for bounded prerequisite-closure loading
  and shared standard component command discovery.
- Preserve external standard component version floors during native updates,
  rebind managed commands after idle activation, and retain custom wrappers.
- Complete core-only preparation from older updaters before activating an
  optional-component binding; retain concurrent binding and active-session
  protections.
- Keep the installed-profile timer and native reload boundary explicit. The
  development pilot's complex-task non-regression gate did not pass; this
  release does not claim a general agent cost improvement.

## 0.1.4 — 2026-10-25

- Pin remote-dev `0.10.7` (`4c40cb5d302ddb8400ff6abb7dc47873b931eaa9`) and
  align `mindie-diagnostics` at `0.5.4`
  (`5ae643e19223f2181ecdbaa3edaf464137c4805e`); the recorder claims first/free
  log slots before reader coordination, fixing the old ~101 ms setup CI
  regression. Keep the 660000 ms remote host timeout and bounded true
  head/tail log previews with raw-log refs.
- Preserve real command exit/result when a secondary preview read degrades;
  mark `preview_degraded` in structured output and native text.
- Document the natural host reload boundary: native package updates set
  `needs_host_reload=true`; `host.mjs` itself is not hot-replaced and only a
  host restart/new process activates launcher changes, while host3 generation
  switching remains idle-gated.
- Run the DSH contract checks on Ubuntu, macOS, and Windows.

## 0.1.3 — 2026-10-25

- Align the shared runtime with the released `mindie-diagnostics 0.5.3`,
  preserving local-only visibility for network, credential, cancellation, and
  local precondition failures without replaying uncertain external writes.
- Route updater-internal failures through the shared diagnostic recorder.

## 0.1.2 — 2026-10-24

- Add independent formal GitHub Release auto-update discovery and side-by-side
  `uv` runtime builds for shared runtime components (`scripts/component_update.py`),
  updating the canonical adapter binding (`paths.configFile`) when idle without
  reinstalling native DSH plugin definitions on component-only updates and
  preserving the installed component version floor across plugin upgrades.
- Mount both `./host3.mjs` and the `@deepseek-ai/dsh-mcp-client` `remote-dev`
  stdio MCP bridge inside a single `mindie-agent-generation` child Cordis fiber
  (`cordis.patch.yml`, `host.mjs`), where `entryFor()` prioritizes `package_dir`
  in `paths.configFile` as the canonical adapter binding (`current.json` only
  serves as a fallback for legacy seeds without `package_dir`), both `remote-dev`
  Python and `host3.mjs` follow the canonical adapter binding, and the child
  fiber hot-swaps when no agent session is `running`.
- Verify `peerDependencies["@deepseek-ai/dsh"]` against `dsh --version`, install
  plugin-level updates via `dsh plugin --profile <profile> add link:<package> --ignore-scripts`
  with `dsh plugin list --json` readback verification, record `stage="unknown"`
  without blind overwrite/rollback on unconfirmed native writes, and use OS
  file locking on `check.lock` (`scripts/native_update.py`, `scripts/updater.py`).
- Pass `mindie-knowledge` exploration outputs straight through without host-side
  rewriting while preserving the manual per-session `/mindie-agent` gate.
- Target the formal releases of `mindie-knowledge 0.13.3`
  (`38a42f6f778cfa555d8918fdcacd12cee4a181f5`), `remote-dev 0.10.6`
  (`0463a13fce9618ec9a51904869d61acb0cdb8538`), and `mindie-diagnostics 0.5.2`
  (`801a01b2789aed7f58e971a909205a7d40b61077`).

## 0.0.3 — 2026-10-20

- Upgrade the pinned shared runtime to `mindie-knowledge 0.13.0`
  (`d18e64f4e79e003c1fb898ca91ecf7587b1e0d32`), `remote-dev 0.10.3`
  (`f441d52f8dcb227ad31adeeeb1d66da4a28915bb`), and `mindie-diagnostics 0.5.0`
  (`6460cde3a50a7f91c9ded38557a7f921654f0c6a`).
- Adopt the single-line `mindie://<domain>/units/<id>@<revision>` graded
  reference contract on the content repositories' `main` branch. References and
  feedback bind a stable `unit_id` to an integer `revision`; no content,
  closure or contract digest is required from callers.
- Wire the graded execution loop into the `/mindie-agent` skill: `L1` + `L2`
  Foundation baseline (shared `_common` axioms and triage SOPs mounted through
  `inherited_rules` / `route: "foundation_baseline"`), `L3` Hermes staged
  convergence with milestone distillation and feedback, and `L4` RSI de-biased
  exploration after two stalled routine attempts with Gate 1–4 enforcement and
  `experiment-ledger.jsonl` / `PROGRESS.md` updates.
- Add a contract test that rejects legacy graded reference terminology and
  asserts the documented loop markers.

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
  release selection, live child-plugin swap, rollback,
  update status/check/enable/disable.

# mindie-agent-dsh 0.1.7

`mindie-agent-dsh 0.1.7` (paired with `mindie-agent 0.1.3`) completes the native `mindie-tools` MCP server registration and component CLI tool surface for DeepSeek Harness alongside the persistent SSH authentication integration from `remote-dev 0.11.1`.

## 1. Key Changes & Behavior

### 1. Native `mindie-tools` MCP Server & Component CLI Bridge (PR `#23`)

- `host.mjs` and `cordis.patch.yml` mount both `remote-dev` and `mindie-tools` (`scripts/component_tools_mcp.py`) inside the `mindie-agent-generation` child fiber with `toolCallTimeoutMs=660000` (`11 minutes`), maintaining `outer_timeout >= inner_timeout (600s) + cleanup_grace`.
- `mindie-tools` exposes 5 always-on tools without requiring `/mindie-agent` session activation:
  - `diagnostics_status` and `diagnostics_bundle` (backed directly by the bundled `mindie-diagnostics 0.5.4` runtime);
  - `coordinator`, `profiler`, and `sim` CLI bridges (discovering optional `mindie-coordinator`, `mindie-profiler`, and `mindie-sim` commands in the configured Python environment, `PATH`, or `component_commands`, with bounded output budgets and process-group termination on timeout).
- The `/mindie-agent` knowledge and skill tools (`mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_distill`, `knowledge_feedback`, `knowledge_sync`) remain strictly gated by explicit per-session activation in `host3.mjs`.

### 2. Active Runtime & Contract Component Pins

| Component | Role | Version / Tag | Source Commit |
| --- | --- | --- | --- |
| `mindie-knowledge` | Runtime dependency | `0.13.4` / `v0.13.4` | `6e921b205c00d592c25f849653cbfb72b73fe318` |
| `remote-dev` | Runtime dependency | `0.11.1` / `v0.11.1` | `2a750ec8c74d7f053410d48dd820aeb542e7d3b1` |
| `mindie-diagnostics` | Runtime dependency | `0.5.4` / `v0.5.4` | `5ae643e19223f2181ecdbaa3edaf464137c4805e` |
| `mindie-profiler` | `component-contracts` & optional CLI pin | `0.5.1` / `v0.5.1` | `25c789cd42389c893234c5db20eca67e63b8878a` |
| `mindie-sim` | `component-contracts` & optional CLI pin | `0.7.1` / `v0.7.1` | `15f93f3f0247b2653d0e9441eae56959e4d16d4e` |
| `mindie-coordinator` | `component-contracts` & optional CLI pin | `0.7.0` / `v0.7.0` | `e4a86c972a99c442c78ad7f7ba627af7da5ef7f2` |

## 2. Verification & Evidence Scope

- Verified via the locked multi-platform CI matrix (Ubuntu, macOS, Windows), Python contract tests (`tests/test_component_tools_mcp.py`, `tests/test_dsh_plugin.py`), and Node host/generation launcher suites (`tests/test_summary_endpoint.mjs`, `tests/test_manual_entry_host.mjs`, `tests/test_generation_launcher.mjs`).
- Formal tag release workflows attach `benchmark-results.json`, `benchmark-results.md`, and `SHA256SUMS` built from the exact tagged commit.

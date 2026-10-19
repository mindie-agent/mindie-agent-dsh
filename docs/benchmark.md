# DSH Benchmarks (`0.0.2`)

`mindie-agent-dsh` participates in two distinct benchmark layers with separate verification scopes:

## 1. Reproducible Release Microbenchmarks (`benchmarks/release.json`)

Every formal GitHub Release publishes `benchmark-results.json`, `benchmark-results.md` generated from [`benchmarks/release.json`](../benchmarks/release.json) and [`benchmarks/release.py`](../benchmarks/release.py):

- **Workload Measured**:
  - `public-transcript` (`public_100_message_parse_latency`, `us/op`): measures deterministic per-operation latency for decompressing a 100-message DSH v4 `session.v4.jsonl.zstd` stream via `dsh_transcript.read_material`, extracting visible user/assistant text, and filtering non-public `reasoning` blocks.
- **What Release Microbenchmarks Prove vs. Do Not Prove**:
  - **Proves**: local CPU parser latency and reasoning-exclusion invariants for the declared synthetic input.
  - **Does Not Prove**: standing Ascend A2/A3 NPU hardware integration (`@pytest.mark.live_npu`) or end-to-end Agent task productivity on real engineering problems.

## 2. End-to-End `mindie-bench` Evaluation Harness

`mindie-bench` (PR #2) integrates DSH as a native evaluation harness for L1/L2 G0–G2 tasks, declared in [`harness-compatibility.json`](../harness-compatibility.json).

Per trial, `mindie-bench` provisions an isolated `DSH_HOME/profiles/bench` profile, routes the `llm-pi-ai` model configuration through the benchmark broker using OpenAI Responses, mounts the selected MCP servers and skill directory, and invokes:

```sh
dsh --profile bench --json
```

The outer benchmark sandbox governs process isolation (`DSH_PERMISSION_MODE=danger-full-access` avoids nested macOS Seatbelt conflicts), and model credentials remain inside the benchmark gateway.

### Verified Canary Results (`cpu-rope-rotary-dim-64` L2)

- **G0**: `completed_pass` (11 turns, native bash/read/glob tools, verified by the task test suite).
- **G1**: `completed_pass` (`remote-dev` MCP server started and passed preflight).
- **G2**: `completed_pass` (`knowledge_query`, `knowledge_explain`, and `/mindie-agent` skill activation observed).
- **G3**: Native capture evaluation in `mindie-bench` currently targets the Codex/Claude Stop transcript protocols and is not yet wired for DSH.

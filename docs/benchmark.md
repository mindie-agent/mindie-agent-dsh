# DSH benchmark harness

`mindie-bench` (PR #2) treats DSH as a native harness for L1/L2 G0-G2. The
integration lives in the benchmark repository; this package declares the
compatible surface in `harness-compatibility.json`.

The harness builds a fresh `DSH_HOME/profiles/bench` per trial, overrides the
existing `llm-pi-ai` row with an OpenAI Responses route through the benchmark
model broker, inserts the selected MCP servers and optional skill directory,
and runs:

```sh
dsh --profile bench --json
```

The outer benchmark sandbox is authoritative, so the trial sets
`DSH_PERMISSION_MODE=danger-full-access`; a nested DSH Seatbelt would fail on
macOS. The model credential stays in the gateway; DSH receives a broker-only
placeholder.

Canaries on `cpu-rope-rotary-dim-64` L2:

- G0: completed pass, 11 turns, native bash/read/glob tools.
- G1: completed pass; remote-dev MCP started and preflighted; no remote call
  was selected by the model in the CPU task.
- G2: completed pass; 1 knowledge query, 1 explain and a skill call observed.

G3 native capture is not supported for DSH in this release. The benchmark
capture adapter currently consumes the Codex/Claude Stop and transcript
protocols.

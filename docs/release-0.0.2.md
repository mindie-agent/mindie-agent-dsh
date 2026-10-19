# MindIE Agent for DeepSeek Harness `0.0.2`

For an existing installation of the original `0.0.2`, follow
[the explicit same-version refresh](same-version-refresh.md). The historical
acceptance below describes the original October 18 release; it does not replace
the new candidate's benchmark and platform acceptance.

Released 2026-10-18. GitHub Release:
`mindie-agent/mindie-agent-dsh@v0.0.2` (`draft=false`, `prerelease=false`).

`0.0.2` is the benchmark-compatibility release. The DSH adapter is now a
first-class native harness in `mindie-bench` (PR #2): a fresh headless profile,
the benchmark model broker, real MCP/knowledge rows and the MindIE skill have
been exercised through L2 G0/G1/G2 canaries.

## What changed

- `mindie-bench` accepts `harness.name = "dsh"` and builds an owned
  `DSH_HOME/profiles/bench` profile per trial. The profile routes an OpenAI
  Responses model through the benchmark broker, starts the selected MCP
  servers, and mounts the optional skill directory.
- The DSH headless JSON stream supplies native session/turn/tool/token facts;
  the benchmark driver normalizes native and MCP tool names.
- G0, G1 and G2 L2 canaries passed on the CPU repair task with real verifier
  judgments. G3 native capture remains Codex/Claude-only.
- DSH 0.0.1 remains the runtime baseline; 0.0.2 changes only version metadata
  and the benchmark compatibility contract. Auto-update and the stable launcher
  from 0.0.1 are retained.

## Install

```bash
git clone --depth 1 --branch v0.0.2 \
  https://github.com/mindie-agent/mindie-agent-dsh.git /tmp/mindie-agent-dsh
uv run --quiet --no-project --python 3.11 /tmp/mindie-agent-dsh/scripts/setup.py
```

Install/enable
`git+https://github.com/mindie-agent/mindie-agent-dsh.git#v0.0.2` in DSH, open
a new session, and type `/mindie-agent`.

## Benchmark acceptance

See [benchmark.md](benchmark.md) and
[`mindie-bench` PR #2](https://github.com/mindie-agent/mindie-bench/pull/2).
Measured on macOS with DSH `0.2.0-rc.2`, task `cpu-rope-rotary-dim-64` L2:

| Group | Result |
| --- | --- |
| G0 | `completed_pass` (11 turns) |
| G1 | `completed_pass`; remote-dev MCP preflighted |
| G2 | `completed_pass`; knowledge query/explain and skill use observed |

## Known limits

- G3 native capture is unsupported for DSH; see the benchmark limitation.
- The G1 CPU canary did not exercise a real SSH/NPU remote; remote-dev was
  started and preflighted, but the model chose the local path.
- No silent OS-level updater for a stopped DSH; background checks run while the
  DSH Host is alive.
- The benchmark CI baseline on GitHub Ubuntu/macOS runners currently fails its
  bwrap/runtime-helper sandbox checks; local full suite is 804 passed / 16
  skipped, and the DSH canaries pass.

## Assets

- `mindie-agent-dsh-0.0.2.tgz`
- `SHA256SUMS`

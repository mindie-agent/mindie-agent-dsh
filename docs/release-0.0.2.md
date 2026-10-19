# MindIE Agent for DeepSeek Harness `0.0.2`

GitHub Release: `mindie-agent/mindie-agent-dsh@v0.0.2` (`draft=false`, `prerelease=false`).

> **Existing `0.0.2` installations**: This release is an authorized one-time same-version replacement of the earlier `0.0.2` build (`828c375d1f546ed653250526465b8075c1691390`). Because the legacy updater only selects versions strictly greater (`>`) than the installed version string, existing `0.0.2` installations do not auto-upgrade to a new commit with the same version. Follow [the explicit same-version refresh guide](same-version-refresh.md) to refresh both the Python runtime and plugin bundle by exact SHA and restart the full DSH process.

## What Changed in `0.0.2`

- **Pinned Shared Runtime Components**:
  - `mindie-knowledge 0.12.2` (`4e328305323eafc7fd43ecc0c53eb3cc0541fdef`)
  - `remote-dev 0.10.1` (`a6649222aae4851deec5774513f8319aece09051`)
  - `mindie-diagnostics 0.4.3` (`1b5a0eeb79d498c39002fc1853271c17e0f26e66`)
  - `scripts/setup.py` installs persistent transcript, organizer, and path helper scripts under `state_dir/bin/` so background workers remain self-contained after temporary setup checkouts are removed.
- **Unified Contribution & Fault-Reporting Switch (`mindie-consent.json`)**:
  - Community knowledge sharing and automatic sanitized tool fault reporting share one persistent switch (`mindie-consent.json`).
  - Explicitly choosing `contribute` configures scoped public knowledge sharing and starts the native background fault reporter; choosing `disabled` turns both off and withdraws unsent reports.
  - Existing `disabled`, `later`, and `read-only` settings stay off across upgrades. Legacy knowledge-only `contribute` records lacking the unified consent revision (`choice_revision` / marked `needs_choice`) do not automatically enable fault uploads on upgrade until explicitly re-confirmed once by the user.
  - State reporting keeps three states strictly distinct: (1) local consent configuration written, (2) background reporter or organizer worker running, and (3) external GitHub Issue or knowledge PR created.
- **Reproducible Release Benchmarks & `mindie-bench` Harness**:
  - Every formal release publishes `benchmark-results.json`, `benchmark-results.md` from [`benchmarks/release.json`](../benchmarks/release.json) (`public-transcript` / `public_100_message_parse_latency` in `us/op`).
  - `mindie-bench` (PR #2) supports DSH (`harness.name = "dsh"`) for L1/L2 G0–G2 evaluation using an isolated headless profile (`DSH_HOME/profiles/bench`).

## Install (Fresh Installation)

```bash
git clone --depth 1 --branch v0.0.2 \
  https://github.com/mindie-agent/mindie-agent-dsh.git /tmp/mindie-agent-dsh
uv run --quiet --no-project --python 3.11 /tmp/mindie-agent-dsh/scripts/setup.py
```

Then install and enable `git+https://github.com/mindie-agent/mindie-agent-dsh.git#v0.0.2` in DSH, open a new session, and invoke `/mindie-agent`. (If refreshing an earlier `0.0.2` installation from `828c375d1f546ed653250526465b8075c1691390`, follow [same-version-refresh.md](same-version-refresh.md) instead.)

## Benchmark & Canary Summary

See [benchmark.md](benchmark.md) and [verification.md](verification.md) for full details:

- **Release CPU Microbenchmark (`benchmarks/release.json`)**: Measures deterministic per-operation latency (`us/op`) for extracting public user/assistant messages and stripping hidden reasoning from a 100-message DSH v4 `session.v4.jsonl.zstd` transcript (`public-transcript` / `public_100_message_parse_latency`).
- **`mindie-bench` L2 Canary (`cpu-rope-rotary-dim-64`, DSH `0.2.0-rc.2`)**:

| Group | Result |
| --- | --- |
| G0 | `completed_pass` (11 turns, native bash/read/glob tools) |
| G1 | `completed_pass` (`remote-dev` MCP started and preflighted) |
| G2 | `completed_pass` (`knowledge_query`, `knowledge_explain`, and skill invocation verified) |

## Known Limits

- G3 native capture evaluation in `mindie-bench` is not yet wired for DSH (DSH's own native `agent/turn-stopping` zstd v4 capture is verified in [verification.md](verification.md)).
- Background release checks run while the DSH Host process is active; no OS-level scheduler runs while DSH is stopped.
- Existing `0.0.2` installations (`828c375d1f546ed653250526465b8075c1691390`) require the one-time explicit SHA refresh and full DSH restart described in [same-version-refresh.md](same-version-refresh.md).

## Release Assets

- `mindie-agent-dsh-0.0.2.tgz`
- `benchmark-results.json`
- `benchmark-results.md`

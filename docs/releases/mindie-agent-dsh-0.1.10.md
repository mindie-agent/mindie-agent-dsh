# mindie-agent-dsh 0.1.10

Paired with MindIE Agent `0.1.4`, this release widens the declared DeepSeek
Harness compatibility from the exact `0.2.0-rc.2` pin to the certified `0.2.0`
and `0.2.1` lines. It retains the explicit `/mindie-agent` session gate, the
native release updater and the pinned runtime components.

## Behavior

- `package.json` declares the optional peer `@deepseek-ai/dsh` as
  `>=0.2.0-rc.2 <0.2.2-0`. DSH evaluates plugin peers with prereleases
  included, so DSH `0.2.1-rc.5` is admitted without an exact-version exemption,
  while `0.2.2-*` and later stay outside the declared range.
- The native updater checks a candidate's DSH requirement with npm-semver
  precedence instead of string equality. `engines.dsh` decides alone when
  present, matching DSH's own precedence; a missing or unsupported requirement
  fails loudly before any profile write.
- `scripts/semver_range.py` is a bounded, dependency-free evaluator for the
  comparator syntax the adapter declares. `setup.py` stages it with the updater
  scripts so an installed profile can import it from the update directory.

## Interface audit

`docs/verification.md` records the Host-interface audit on Windows 11 with DSH
`0.2.1-rc.5`: the bundle patch, skill filesystem configuration, MCP stdio
configuration, `ctx.tools.register`, the `agent/pre-step` and
`agent/turn-stopping` dispatchers, `agent.options`, `ctx.llm.stream` chunks and
the v4 `session.v4.jsonl.zstd` transcript all keep the contracts the adapter
uses. The installed `desktop` profile mounted the adapter
(`mounted ... host3.mjs`) without an exact-version exemption.

## Runtime and host compatibility

The runtime pins are unchanged from `0.1.9`; the exact source pins are recorded
in `uv.lock`, `runtime-requirements.txt` and the current `runtime_components`
entry of `harness-compatibility.json`. Historical `verified` entries retain
their original versions and evidence.

| Component | Version / Tag | Role | Source commit |
| --- | --- | --- | --- |
| `mindie-knowledge` | `0.13.6` / `v0.13.6` | Runtime dependency | `9af5a82ad557e2120277693854a0571613c8a115` |
| `remote-dev` | `0.11.1` / `v0.11.1` | Runtime dependency | `2a750ec8c74d7f053410d48dd820aeb542e7d3b1` |
| `mindie-diagnostics` | `0.5.4` / `v0.5.4` | Runtime dependency | `5ae643e19223f2181ecdbaa3edaf464137c4805e` |
| `mindie-profiler` | `0.5.1` / `v0.5.1` | Optional component contract | `25c789cd42389c893234c5db20eca67e63b8878a` |
| `mindie-sim` | `0.7.1` / `v0.7.1` | Optional component contract | `15f93f3f0247b2653d0e9441eae56959e4d16d4e` |
| `mindie-coordinator` | `0.7.0` / `v0.7.0` | Optional component contract | `e4a86c972a99c442c78ad7f7ba627af7da5ef7f2` |

The declared native host range is DeepSeek Harness `>=0.2.0-rc.2 <0.2.2-0`,
certified on `0.2.0-rc.2` (historical evidence) and `0.2.1-rc.5` (this release),
with Node.js `>=22` and Python `>=3.11`.

## Verification scope

- The release workflow installs the exact lock, validates formal first-party
  dependencies, runs Python and Node contracts, and builds the npm archive.
  CI checks the adapter on Ubuntu, macOS and Windows.
- The `0.2.1-rc.5` audit covers composition and protocol surfaces; every
  model-facing knowledge path was not re-exercised end to end on that runtime.
- Installed-profile auto-update and native runtime readback are separate
  post-publication acceptance steps. No model request is needed to trigger the
  existing idle update timer or inspect the resulting binding.

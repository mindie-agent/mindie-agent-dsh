# mindie-agent-dsh 0.1.2 Release Notes

[中文](https://github.com/mindie-agent/mindie-agent-dsh/blob/v0.1.2/docs/releases/mindie-agent-dsh-0.1.2.md) | **English**

`mindie-agent-dsh 0.1.2` is a maintenance release for DeepSeek Harness (DSH) (paired with `mindie-agent 0.1.1`, while preserving the existing immutable `0.1.1` plugin tag). It introduces independent formal GitHub Release auto-updates for shared runtime components, co-generational `host` + `remote-dev` mounting, real `dsh plugin` readback verification, and OS-level updater lock recovery.

## Highlights

- **Independent Component Auto-Update & Co-Generational Child Mounting**:
  - `cordis.patch.yml` and `host.mjs` mount both the native Host plugin (`host3.mjs`) and the `remote-dev` stdio MCP client inside a single `mindie-agent-generation` child Cordis fiber, hot-swapping both together from `current.json` only when no agent session is `running` (and falling back to the previous generation on mount failure).
  - When no newer plugin Release is selected, the updater discovers formal GitHub Releases directly from official component repositories, builds a non-downgrading side-by-side `uv` generation with a verified `uv.lock`, and switches `current.json` when idle **without reinstalling native DSH plugin definitions**. Plugin-level upgrades also inherit the installed component floor.
- **Verified Native `dsh plugin` Registration & Automatic Lock Recovery**:
  - Plugin-level updates verify `peerDependencies["@deepseek-ai/dsh"]` against `dsh --version`, run `dsh plugin --profile <profile> add link:<package> --ignore-scripts`, and confirm registration via `dsh plugin --profile <profile> list --json` readback; if cleanup or readback is unconfirmed, the state is recorded as `stage="unknown"` without blind retry or rollback. Native profile updates set `needs_host_reload=True` and require a host reload to take effect.
  - `updater.py` uses OS file locking on `check.lock` so a terminated updater releases its lock automatically.
- **Exploration Payload Pass-Through & Explicit Session Gate**:
  - Passes `mindie-knowledge` exploration outputs straight through without host-side rewriting. Updates never trigger corpus sync or grant session knowledge authority; knowledge features activate only after manually invoking `/mindie-agent` in the current session.

## Runtime Components & Verification Scope

- **Paired Runtime Components**: Targets the formal releases of `mindie-knowledge 0.13.3`, `remote-dev 0.10.6` (`0463a13fce9618ec9a51904869d61acb0cdb8538`), and `mindie-diagnostics 0.5.2` (`801a01b2789aed7f58e971a909205a7d40b61077`). Exact commit SHAs are recorded in `uv.lock`, `runtime-requirements.txt`, and `harness-compatibility.json`.
- **Verification & Acceptance Boundary**: Passed the existing update/install contract suite, new native plugin update tests (`3 passed`), killed-updater lock release process contract tests, and the co-generational `host` + `remote-dev` mounting Node unit contract test (`tests/test_generation_launcher.mjs`, `1 passed` — note this is a Node unit contract test rather than live DSH host acceptance). Cross-host end-to-end native new-session acceptance after formal package publication is tracked with the main product release gate.

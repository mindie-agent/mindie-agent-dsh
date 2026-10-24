# mindie-agent-dsh 0.1.2 Release Notes

[中文](https://github.com/mindie-agent/mindie-agent-dsh/blob/v0.1.2/docs/releases/mindie-agent-dsh-0.1.2.md) | **English**

`mindie-agent-dsh 0.1.2` is a maintenance release for DeepSeek Harness (DSH) (paired with `mindie-agent 0.1.1`, while preserving the existing immutable `0.1.1` plugin tag). It introduces independent formal GitHub Release auto-updates for shared runtime components, co-generational `host` + `remote-dev` mounting, real `dsh plugin` readback verification, and OS-level updater lock recovery.

## Highlights

- **Independent Component Auto-Update & Co-Generational Child Mounting**:
  - `cordis.patch.yml` and `host.mjs` mount both the native Host plugin (`host3.mjs`) and the `remote-dev` stdio MCP client inside a single `mindie-agent-generation` child Cordis fiber: `entryFor()` in `host.mjs` prioritizes reading `package_dir` from `paths.configFile` (`config.json`) as the canonical adapter binding (`current.json` is used only as a compatibility fallback for legacy seeds lacking `package_dir`), both the `remote-dev` Python interpreter and `host3.mjs` are governed by that canonical adapter binding, and both hot-swap together only when no agent session is `running` (falling back to the previous binding on mount failure).
  - When no newer plugin Release is selected, the updater discovers formal GitHub Releases directly from official component repositories, builds a non-downgrading side-by-side `uv` generation with a verified `uv.lock`, and updates the canonical adapter binding when idle while retaining the native bundle (**without reinstalling native DSH plugin definitions**). Plugin-level upgrades also inherit the installed component floor.
- **Verified Native `dsh plugin` Registration & Automatic Lock Recovery**:
  - Plugin-level updates verify `peerDependencies["@deepseek-ai/dsh"]` against `dsh --version`, run `dsh plugin --profile <profile> add link:<package> --ignore-scripts`, and confirm registration via `dsh plugin --profile <profile> list --json` readback; if cleanup or readback is unconfirmed, the state is recorded as `stage="unknown"` without blind retry or rollback. Native profile updates set `needs_host_reload=True` and require a host reload to take effect.
  - `updater.py` uses OS file locking on `check.lock` so a terminated updater releases its lock automatically.
- **Exploration Payload Pass-Through & Explicit Session Gate**:
  - Passes `mindie-knowledge` exploration outputs straight through without host-side rewriting. Updates never trigger corpus sync or grant session knowledge authority; knowledge features activate only after manually invoking `/mindie-agent` in the current session.

## Runtime Components & Verification Scope

- **Paired Runtime Components**: Locks formal releases of `mindie-knowledge 0.13.3` (`38a42f6f778cfa555d8918fdcacd12cee4a181f5`), `remote-dev 0.10.6` (`0463a13fce9618ec9a51904869d61acb0cdb8538`), and `mindie-diagnostics 0.5.2` (`801a01b2789aed7f58e971a909205a7d40b61077`). Exact commit SHAs are recorded in `uv.lock`, `runtime-requirements.txt`, and `harness-compatibility.json`.
- **Verification & Acceptance Hierarchy**:
  1. **macOS `arm64` Native Candidate Verification (DSH `0.2.0-rc.2`)**: On a default fresh session, `knowledge_query` is rejected before execution without initializing the knowledge service or retrying; manual `/mindie-agent` activation sets `entry_enabled=1`, is inherited across same-session continuation turns, and is not inherited by an independent fresh session; default `remote_job_status` executes through the deterministic remote path (returning a normal caller/not-found error for a missing `job_id` without being blocked by the knowledge gate). Independent component update verification confirmed exact alignment with `mindie-knowledge 0.13.3`, `remote-dev 0.10.6`, and `mindie-diagnostics 0.5.2` while preserving the native bundle, and a fresh post-update native profile reached `READY`.
  2. **Automated Suites & Multi-OS CI**: Passed the existing update/install contract suite, new native plugin update tests (`3 passed`), killed-updater lock release process contract tests, and the co-generational `host` + `remote-dev` mounting Node unit contract test (`tests/test_generation_launcher.mjs`, `1 passed` — Node unit contract test), with cross-OS contracts covered by per-OS CI workflows.
  3. **Post-Publication Local Update Readback**: Final local auto-update readback against published `0.1.2` GitHub Release packages remains pending until the four host `0.1.2` tags are formally published.

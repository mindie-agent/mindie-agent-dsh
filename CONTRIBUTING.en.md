# Contributing to `mindie-agent-dsh`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

Thank you for contributing to `mindie-agent-dsh`, the native MindIE Agent plugin for DeepSeek Harness (`DSH`). Please follow the architectural rules and verification steps below before opening a pull request.

## 1. Architecture & Host Boundaries

1. **Thin Host Adapter Scope**: Keep this repository focused on DSH Cordis mounting (`host.mjs`, `host3.mjs`, `paths.cjs`), DSH v4 `zstd` transcript extraction (`scripts/dsh_transcript.py`), loopback model summary routing (`scripts/dsh_organizer.py`), warm JSON-line sidecar (`scripts/mindie_sidecar.py`), and MCP bridges. Shared process supervision, generation leases, and RSI helpers delegate to `mindie_harness` (`assets/mindie-harness-seed.pyz`).
2. **Windows Zero-Popup Iron Rule**: Every Node `spawn` call must pass `windowsHide: true`, and background spawns must use `backgroundSpawnOptions()` (`detached: process.platform !== 'win32'` — never `detached: true` on `win32`) plus `buildChildEnv()` to normalize case-insensitive `PATH` keys on Windows. Python child spawns must pass `CREATE_NO_WINDOW`.
3. **Admission & Honesty Invariants**:
   - Deterministic MCP servers (`remote-dev` and concurrent resident `component_mcp.py`) and `knowledge_*` tools (`host3.mjs` + `knowledge_mcp.py`) run by default without pre-call session activation, sharing `state/remote` across parent and subagent sessions while auto-provisioning `not_before=0.0` leases on demand for mutating calls and gating `Stop` capture on `mindie-consent.json` + `project_roots` before opening SQLite;
   - Always precede `except OSError:` with `except TimeoutError: raise`, and allow valid symlinks on bounded reads (zero `O_NOFOLLOW` hostility).
4. **Module Structure & Line Endings**: Keep every runtime `.py`, `.mjs`, and `.cjs` module `< 800` lines with `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines, using Unix `LF` (`\n`) line endings only.

## 2. Isolated Verification Commands

Run all Python and Node checks in isolated environments without touching `$HOME` or `~/.dsh`:

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -ra -m "not live_npu"
node --check paths.cjs && node --check host.mjs && node --check host3.mjs
node tests/test_summary_endpoint.mjs
node --test tests/test_manual_entry_host.mjs tests/test_update_host.mjs \
  tests/test_generation_launcher.mjs tests/test_remote_isolation.mjs \
  tests/test_log_rotation.mjs tests/test_summary_host.mjs \
  tests/test_win32_spawn_and_gating.mjs
```

## 3. Pre-PR Checklist (`host-plugin`)

Complete all four `host-plugin` items (`HOST-NATIVE`, `HOST-WIRE`, `HOST-ADMISSION`, `HOST-UPGRADE`) in [`.github/pull_request_template.md`](.github/pull_request_template.md) with concrete verification evidence (or `not-applicable: <specific reason>`):

```bash
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr-body.md>
```

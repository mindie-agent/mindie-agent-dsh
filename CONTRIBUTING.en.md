# Contributing to `mindie-agent-dsh`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

Thank you for contributing to `mindie-agent-dsh`, the native MindIE Agent plugin for DeepSeek Harness (`DSH`). Please follow the architectural rules and verification steps below before opening a pull request.

## 1. Architecture & Host Boundaries

1. **Thin Host Adapter Scope**: `v0.1.26` keeps only six production files — the Host boundary (`paths.cjs`, `host.mjs`, `host3.mjs`) and `scripts/{mindie_launch.py,dsh_transcript.py,setup.py}`. MCP routing, runtime preparation, sharing/update, locks, storage GC, and host adaptation delegate to `mindie-harness` (`API_VERSION = 1`, bundled `assets/mindie-harness-seed.pyz`). The six files must total `<= 1,200` lines.
2. **Windows Zero-Popup Iron Rule**: Every Node `spawn` call must pass `windowsHide: true`, and background spawns must use `backgroundSpawnOptions()` (`detached: process.platform !== 'win32'` — never `detached: true` on `win32`) plus `buildChildEnv()` to normalize case-insensitive `PATH` keys on Windows. Python child spawns must pass `CREATE_NO_WINDOW`.
3. **Admission & Honesty Invariants**:
   - Each session mounts `remote-dev` (14 `dsh` tools) and `mindie-tools` (deterministic components + `mindie_doctor` + the 5 `knowledge_*` tools) by default, sharing `<state>/remote` for parent/subagent `job_id` control. `Stop` capture checks `mindie-consent.json` (`contribute`/`local-only`) and `mindie-community.json` `project_roots` in pure Node before `mindie_launch.py` re-checks the runtime lease and preserves explicit `Admission` revocation;
   - Always precede `except OSError:` with `except TimeoutError: raise`, and allow valid symlinks on bounded reads (zero `O_NOFOLLOW` hostility).
4. **Module Structure & Line Endings**: Keep every runtime `.py`, `.mjs`, and `.cjs` module `< 800` lines with `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines, using Unix `LF` (`\n`) line endings only.

## 2. Isolated Verification Commands

Run all Python and Node checks in isolated environments without touching `$HOME` or `~/.dsh`:

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -ra -m "not live_npu"
node --check paths.cjs && node --check host.mjs && node --check host3.mjs
node --test tests/test_dsh_host.mjs
wc -l paths.cjs host.mjs host3.mjs scripts/*.py
```

## 3. Pre-PR Checklist (`host-plugin`)

Complete all four `host-plugin` items (`HOST-NATIVE`, `HOST-WIRE`, `HOST-ADMISSION`, `HOST-UPGRADE`) in [`.github/pull_request_template.md`](.github/pull_request_template.md) with concrete verification evidence (or `not-applicable: <specific reason>`):

```bash
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr-body.md>
```

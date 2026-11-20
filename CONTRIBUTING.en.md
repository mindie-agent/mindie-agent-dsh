# Contributing to `mindie-agent-dsh`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

Thank you for contributing to `mindie-agent-dsh` (`v0.1.28`), the native MindIE Agent plugin for DeepSeek Harness (`DSH`). Please follow the architectural rules and verification steps below before opening a pull request.

## 1. Architecture & Host Boundaries

1. **Thin Host Adapter Scope**: `v0.1.28` keeps only six production files — the Host boundary (`paths.cjs`, `host.mjs`, `host3.mjs`) and `scripts/{mindie_launch.py,dsh_transcript.py,setup.py}` (`1,466` total lines `<= 1,500`, zero `_knowledge_fallback` shadow shim). All 9 runtime packages (including `npu-top 0.3.5` and `mindie-harness >= 0.1.5`) are pinned across `pyproject.toml`, `runtime-requirements.txt`, `uv.lock`, and `product-contract.json`. MCP routing, 9-domain content-addressed bindings, sharing/update, locks, storage GC, and host adaptation delegate to `mindie-harness` (`API_VERSION = 1`); cold-seed bootstrap (`assets/mindie-harness-seed.pyz`) isolates `stdin=DEVNULL` and `stdout=DEVNULL` so byte 0 of MCP JSON-RPC `stdout` stays pure.
2. **Windows Zero-Popup & Python Resolution Iron Rule**: Every Node `spawn` call must pass `windowsHide: true`, and background spawns must use `backgroundSpawnOptions()` (`detached: process.platform !== 'win32'` — never `detached: true` on `win32`) plus `buildChildEnv()` to normalize case-insensitive `PATH` keys on Windows and `resolvePythonCommand` to exclude `WindowsApps` / 0-byte stubs (never returning bare `'python'` on `win32`). Strip UTF-8 BOM before JSON parsing, and pass `CREATE_NO_WINDOW` on every Python child spawn.
3. **Admission, Spool Identity & Honesty Invariants**:
   - `host.mjs` resolves `sessionId` once per mount (caching `dsh-conn-<uuid>` when omitted), tracks `closed`-guarded fiber records across `await` boundaries to dispose late/partial mounts cleanly, and mounts `remote-dev` (14 `dsh` tools) plus `mindie-tools` (11 component/status tools + the 5 `knowledge_*` tools), sharing `<state>/remote` for parent/subagent `job_id` control;
   - `host3.mjs` requires a non-blank `turn` and checks `mindie-consent.json` (`contribute`/`local-only`) plus `mindie-community.json` `project_roots` in pure Node; `dsh_transcript.py` projects `session.v4.jsonl.zstd` into a unified byte-addressed UTF-8 spool (`identify(native_zstd) == identify(spool_path)`, verifying `cwd` containment and evicting cold spools `<= 64` under non-blocking `.lock` checks); `setup.py init` migrates and preserves `inactive` revoked sessions across `admission_path`;
   - Always precede `except OSError:` with `except TimeoutError: raise`, and allow valid symlinks on bounded reads (zero `O_NOFOLLOW` hostility).
4. **Module Structure & Line Endings**: Keep every runtime `.py`, `.mjs`, and `.cjs` module `< 800` lines with `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines, using Unix `LF` (`\n`) line endings only; CI enforces native host acceptance via `verify-host-plugin.yml` (`host: dsh`).

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

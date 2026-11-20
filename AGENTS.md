# `mindie-agent-dsh`

Native MindIE Agent plugin for **DeepSeek Harness (`DSH` `>=0.2.0-rc.2 <1.0.0`)**.

`v0.1.28` is a **thin plugin** (`Great Subtraction`, `1,466` total production lines `<= 1,500`): the Host boundary is only `paths.cjs`, `host.mjs`, and `host3.mjs`; `scripts/` keeps only `mindie_launch.py`, `dsh_transcript.py`, and `setup.py`. All 9 runtime packages (including `npu-top 0.3.5` and `mindie-harness >= 0.1.5`) are locked in `pyproject.toml`, `runtime-requirements.txt`, `uv.lock`, and `product-contract.json`. MCP routing, runtime preparation, 9-domain knowledge bindings, sharing/update, locking, and storage GC delegate directly to `mindie-harness` (`API_VERSION = 1`, zero `_knowledge_fallback` shadow shim); cold start verifies `assets/mindie-harness-seed.pyz` against `assets/seed-lock.json` with `stdin=DEVNULL` and `stdout=DEVNULL` so MCP JSON-RPC byte 0 stays pure.

## 1. Core Operating Rules (Always Active)

- **Thin Plugin Boundary (`mindie_harness` Substrate)**:
  - Keep DSH-specific code to Cordis lifecycle (`host.mjs`, `host3.mjs`), DSH v4 zstd transcript spooling (`scripts/dsh_transcript.py`), loopback summary routing, and the `mindie_launch.py` MCP/hook/summary delegator.
  - `paths.cjs` strips UTF-8 BOM before JSON parsing, resolves canonical `<config-base>/mindie-agent/dsh.json` first (then legacy `MINDIE_DSH_CONFIG_HOME/config.json` or `<config-base>/mindie-agent/dsh/config.json`), and exports `resolvePythonCommand` (`venvPython` -> `MINDIE_DSH_PYTHON` -> `uv` -> `PATH` Python, rejecting `WindowsApps` / 0-byte stubs and never returning bare `'python'` on `win32`).
  - `setup.py init` runs under `.dsh.json.update.lock`, migrates explicitly revoked (`inactive`) sessions across `admission_path`, calls `ensure_knowledge_binding(..., retire_previous=True, already_locked=True)` to provision all 9 content-addressed `engines/dsh/<20hex>/<domain>.engine.json` configs with DSH's loopback `summary_command`, runs `maintain_runtime` GC, and enforces mutually exclusive `sharing` flags (`exit 2` on conflict).
- **Zero Windows Console Popups (`Windows Zero-Popup Iron Rule`)**:
  - Every Node `spawn` call MUST include `windowsHide: true`, and background spawns MUST use `backgroundSpawnOptions()` (`detached: process.platform !== 'win32'`) — **NEVER** literal `detached: true`.
  - Normalize case-insensitive `PATH`/`Path` via `buildChildEnv()` on Windows, and enforce `CREATE_NO_WINDOW` (`0x08000000`) on every Python child without unguarded `os.kill(pid, 0)`.
- **Default-On MCP Tools, Disposal-Safe Mounts + Consent-First `Stop` Capture**:
  - Per agent session `host.mjs` resolves `sessionId` once (caching a per-agent `dsh-conn-<uuid>` fallback when omitted), tracks a `closed`-guarded fiber record across `await` boundaries to dispose partial/late mounts cleanly, and attaches `remote-dev` (14 `dsh` tools via `mindie_launch.py --config <dsh.json> mcp remote`) plus `mindie-tools` (11 deterministic/component tools + 5 `knowledge_*` via `mcp tools`), both backed directly by `ManagedMCPAdapter("dsh")` without `_meta`, `request_nonce`, or `context_file` gates.
  - Parent and subagent sessions share `REMOTE_DEV_STATE_DIR` (`<state>/remote`) so subagents can poll/stop parent `job_id`s, while `REMOTE_DEV_SESSION_ID` isolates read ledgers.
  - `host3.mjs` enforces a non-blank `turn` guard and checks `mindie-community.json` (`enabled`, `project_roots`) plus `mindie-consent.json` (`choice in {contribute, local-only}`) in pure Node before locating a transcript or spawning a child; `dsh_transcript.py` projects `session.v4.jsonl.zstd` into `<state_dir>/spools/<24hex>.jsonl` (`identify(native_zstd) == identify(spool_path)`, `cwd` containment check, cold eviction `<= 64`); `mindie_launch.py hook stop` delegates to `run_stop_capture` while preserving `inactive` revocation.
- **Non-Blocking Task Continuity & Receipt Honesty**:
  - MindIE tools (`remote-dev`, `coordinator`, `knowledge`, `mindie_doctor`) are accelerators, never roadblocks. On failure, report the concrete error once (reconciling uncertain remote writes first) and continue via native `ssh`/`scp` or local scripts.
  - Distinguish `completed`, `failed`, and `unknown`; precede broad `except OSError:` / `except Exception:` with `except TimeoutError: raise`; keep `isError=False` plus `receipt_warning` when only local accounting fails after primary success.
- **Cohesive Modules & Zero Host Pollution (`Principles 16–18`)**:
  - The sum of `paths.cjs` + `host.mjs` + `host3.mjs` + every `.py`/`.mjs`/`.cjs`/`.js` under `scripts/` MUST stay `<= 1,500` lines; every production module stays `< 800` lines and carries `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines.
  - Isolate `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `DSH_HOME`, `MINDIE_*`, `REMOTE_DEV_AUTH_DIR`, `UV_CACHE_DIR`, and `TMPDIR` in all tests; `.github/workflows/ci.yml` gates PRs with `verify-host-plugin.yml` (`host: dsh`).

## 2. Progressive Disclosure Map

- **Active Documentation Index** → [`docs/README.md`](docs/README.md) & [`docs/README.en.md`](docs/README.en.md)
- **Host Verification Matrix & DSH SDK Audit** → [`docs/verification.md`](docs/verification.md)
- **Contributor Workflow & Pre-PR Gate (`host-plugin`)** → [`CONTRIBUTING.md`](CONTRIBUTING.md), [`.github/pull_request_template.md`](.github/pull_request_template.md), and `python3 .github/scripts/check_lf_eol.py`

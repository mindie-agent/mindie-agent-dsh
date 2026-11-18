# `mindie-agent-dsh`

Native MindIE Agent plugin for **DeepSeek Harness (`DSH` `>=0.2.0-rc.2 <1.0.0`)**.

`v0.1.26` is a **thin plugin** (`Great Subtraction`): the Host boundary is only `paths.cjs`, `host.mjs`, and `host3.mjs`; `scripts/` keeps only `mindie_launch.py`, `dsh_transcript.py`, and `setup.py`. MCP routing, runtime preparation, sharing/update, locking, and storage GC delegate to `mindie-harness` (`API_VERSION = 1`); cold start verifies `assets/mindie-harness-seed.pyz` against `assets/seed-lock.json`.

## 1. Core Operating Rules (Always Active)

- **Thin Plugin Boundary (`mindie_harness` Substrate)**:
  - Keep DSH-specific code to Cordis lifecycle (`host.mjs`, `host3.mjs`), DSH v4 zstd transcript parsing (`scripts/dsh_transcript.py`), loopback summary routing, and the `mindie_launch.py` MCP/hook/summary delegator.
  - `paths.cjs` resolves the canonical `<config-base>/mindie-agent/dsh.json` first, then legacy `MINDIE_DSH_CONFIG_HOME/config.json` or `<config-base>/mindie-agent/dsh/config.json`; config overrides must be absolute.
  - `setup.py init` writes `dsh.json`/`dsh.engine.json` (absolute `transcript_adapter`, `redactor_executable`, loopback `summary_command`) and delegates `sharing`, `doctor`, `update`, `release-metadata` to `mindie_harness`.
- **Zero Windows Console Popups (`Windows Zero-Popup Iron Rule`)**:
  - Every Node `spawn` call MUST include `windowsHide: true`, and background spawns MUST use `backgroundSpawnOptions()` (`detached: process.platform !== 'win32'`) — **NEVER** literal `detached: true`.
  - Normalize case-insensitive `PATH`/`Path` via `buildChildEnv()` on Windows, and enforce `CREATE_NO_WINDOW` (`0x08000000`) on every Python child without unguarded `os.kill(pid, 0)`.
- **Default-On MCP Tools + Consent-First `Stop` Capture**:
  - Per agent session `host.mjs` attaches exactly two stdio servers: `remote-dev` (14 `dsh` tools via `mindie_launch.py --config <dsh.json> mcp remote`) and `mindie-tools` (deterministic tools + `mindie_doctor` + 5 `knowledge_*` via `mcp tools`), both backed by `ManagedMCPAdapter("dsh")` without `_meta`, `request_nonce`, or `context_file` gates.
  - Parent and subagent sessions share `REMOTE_DEV_STATE_DIR` (`<state>/remote`) so subagents can poll/stop parent `job_id`s, while `REMOTE_DEV_SESSION_ID` isolates read ledgers.
  - `host3.mjs` checks `mindie-community.json` (`enabled`, `project_roots`) and `mindie-consent.json` (`choice in {contribute, local-only}`) in pure Node before locating a transcript or spawning a child; `mindie_launch.py hook stop` re-checks the runtime tier and preserves explicit `Admission` revocation.
- **Non-Blocking Task Continuity & Receipt Honesty**:
  - MindIE tools (`remote-dev`, `coordinator`, `knowledge`, `mindie_doctor`) are accelerators, never roadblocks. On failure, report the concrete error once (reconciling uncertain remote writes first) and continue via native `ssh`/`scp` or local scripts.
  - Distinguish `completed`, `failed`, and `unknown`; precede broad `except OSError:` / `except Exception:` with `except TimeoutError: raise`; keep `isError=False` plus `receipt_warning` when only local accounting fails after primary success.
- **Cohesive Modules & Zero Host Pollution (`Principles 16–18`)**:
  - The sum of `paths.cjs` + `host.mjs` + `host3.mjs` + every `.py`/`.mjs`/`.cjs`/`.js` under `scripts/` MUST stay `<= 1,200` lines; every production module stays `< 800` lines and carries `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines.
  - Isolate `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `DSH_HOME`, `MINDIE_*`, `REMOTE_DEV_AUTH_DIR`, `UV_CACHE_DIR`, and `TMPDIR` in all tests.

## 2. Progressive Disclosure Map

- **Active Documentation Index** → [`docs/README.md`](docs/README.md) & [`docs/README.en.md`](docs/README.en.md)
- **Host Verification Matrix & DSH SDK Audit** → [`docs/verification.md`](docs/verification.md)
- **Contributor Workflow & Pre-PR Gate (`host-plugin`)** → [`CONTRIBUTING.md`](CONTRIBUTING.md), [`.github/pull_request_template.md`](.github/pull_request_template.md), and `python3 .github/scripts/check_lf_eol.py`

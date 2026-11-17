# `mindie-agent-dsh`

Native MindIE Agent plugin for **DeepSeek Harness (`DSH` `>=0.2.0-rc.2 <1.0.0`)**.
This repository owns the thin Cordis host launcher (`host.mjs`), generation fiber (`host3.mjs`), path/spawn boundary (`paths.cjs`), DSH v4 zstd transcript projection (`scripts/dsh_transcript.py`), loopback model summary adapter (`scripts/dsh_organizer.py`), warm JSON-line sidecar (`scripts/mindie_sidecar.py`), and deterministic/knowledge MCP bridges backed by `mindie-harness` (`assets/mindie-harness-seed.pyz`).

## 1. Core Operating Rules (Always Active)

- **Thin Host Plugin Boundary (`mindie_harness` Substrate)**:
  - Keep DSH-specific code limited to Cordis lifecycle (`host.mjs`, `host3.mjs`), DSH v4 zstd transcript parsing, loopback summary routing, and MCP/sidecar wiring.
  - Shared process supervision, generation leases, storage GC, and RSI helpers (`scripts/{rsi_loop.py, rsi_explore.py, experiment_ledger.py}`) delegate to `mindie_harness`.
- **Zero Windows Console Popups (`Windows Zero-Popup Iron Rule`)**:
  - Every Node `spawn` call MUST pass `windowsHide: true`, and background spawns (`startUpdateCheck`, `spawnCapture`) MUST use `backgroundSpawnOptions()` (`detached: process.platform !== 'win32'`) — **NEVER** pass `detached: true` on `win32` (which creates a visible console window in Node/libuv).
  - Normalize case-insensitive `PATH`/`Path` environment keys via `buildChildEnv()` on Windows, and enforce `CREATE_NO_WINDOW` (`0x08000000`) on all Python child launches without unguarded `os.kill(pid, 0)`.
- **Zero Pre-Call Gates on Deterministic Tools (`P2.7`) vs. Strict `/mindie-agent` Knowledge Admission (`P2.1`)**:
  - Deterministic MCP servers (`remote-dev` and `mindie-tools` via `scripts/component_mcp.py`) work out-of-the-box in every session and subagent without `/mindie-agent`, `request_nonce`, or `context_file`.
  - Parent and subagent sessions share `REMOTE_DEV_STATE_DIR` (`state/remote`) so subagents can poll or stop parent `job_id`s (`AD-02` fixed), while `REMOTE_DEV_SESSION_ID` isolates per-session read ledgers.
  - Resident `component_mcp.py` dispatches concurrent `tools/call` requests without single-flight `-32000` blocking (`AD-03` fixed).
  - Unactivated default sessions register ONLY `mindie_entry`; `knowledge_query`, `knowledge_explain`, `knowledge_distill`, `knowledge_feedback`, and `knowledge_sync` are mounted ONLY after `/mindie-agent` activates or resumes a session lease and are disposed on `deactivate` or `agent/disposed` (`AD-11` fixed).
- **State & Receipt Honesty + Symlink Compatibility (`Principles 9, 10, 14` & `AD-07`)**:
  - Distinguish `completed`, `failed`, and `unknown` external effects; precede broad `except OSError:` with `except TimeoutError: raise`; enforce `outer_timeout >= sum(inner_budgets) + cleanup_grace` (`660s >= 600s + 60s`).
  - Keep `isError=False` with `receipt_warning` / `local_error` when a primary remote or model operation succeeds and only local receipt accounting fails.
  - Allow valid symlinks on bounded diagnostic and metadata reads (zero `O_NOFOLLOW` hostility).
- **Cohesive Modules & Zero Host Pollution (`Principles 16–18`)**:
  - Every runtime `.py`, `.mjs`, and `.cjs` module MUST stay `< 800` lines and carry `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines.
  - Isolate `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `DSH_HOME`, `MINDIE_*`, `REMOTE_DEV_AUTH_DIR`, `UV_CACHE_DIR`, and `TMPDIR` in all tests.

## 2. Progressive Disclosure Map

- **Active Documentation Index** → [`docs/README.md`](docs/README.md) & [`docs/README.en.md`](docs/README.en.md)
- **Host Verification Matrix & DSH SDK Audit** → [`docs/verification.md`](docs/verification.md)
- **Contributor Workflow & Pre-PR Gate (`host-plugin`)** → [`CONTRIBUTING.md`](CONTRIBUTING.md), [`.github/pull_request_template.md`](.github/pull_request_template.md), and `python3 .github/scripts/check_lf_eol.py`

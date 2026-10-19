# mindie-agent-dsh (`v0.0.2` Agent-Native Guide)

Native DeepSeek Harness (DSH / Cordis) plugin adapter for MindIE Agent (`host.mjs`, `host3.mjs`, `cordis.patch.yml`, `paths.cjs`, `scripts/`).
Provides the `/mindie-agent` entry command/skill, warm JSON-line sidecar (`mindie_sidecar.py`), detached Stop capture handoff (`capture_stop.py`), loopback summary worker (`dsh_organizer.py`), zstd session-log transcript adapter (`dsh_transcript.py`), and silent release updater (`updater.py`).

## 1. Script Module Navigation & Responsibilities

| Module | Role | Key Invariants & Side Effects |
| :--- | :--- | :--- |
| `scripts/mindie_paths.py` | Canonical path authority mirroring `paths.cjs` | Pure path resolution (`data_home`, `config_home`, `config_file`, `engine_config`, `state_dir`, `venv_dir`). Must stay byte-for-byte aligned with `paths.cjs`. |
| `scripts/mindie_sidecar.py` | Warm JSON-line sidecar for DSH Host plugin | Dispatches `activate`, `status`, `deactivate`, `configure`, `reporting`, `rpc`, `sync`. Requires session lease token for `rpc`; never retries failed RPCs. |
| `scripts/knowledge_mcp.py` | Thin stdio MCP bridge (`knowledge_query`, `knowledge_explain`, `knowledge_sync`) | Re-executes into configured venv Python (`MINDIE_DSH_NO_REEXEC=1` disables re-exec); strictly read-only plus explicit feed sync. |
| `scripts/capture_stop.py` | Detached Stop-hook capture handoff | Reads one Stop envelope (`<= 256 KiB`) from stdin, calls `capture_hook`, appends receipt to `capture.log` (`1 MiB` rotation), never emits to stdout. |
| `scripts/dsh_organizer.py` | Loopback summary worker (`mindie-summary-outcome/1`) | Calls active DSH session's loopback summary endpoint (`summary-endpoint.json`); returns `returned`, `failed`, or `outcome_unknown` without retrying uncertain calls. |
| `scripts/dsh_transcript.py` | DSH v4 `.jsonl.zstd` public-transcript reader | Streams concatenated zstd frames (`<= 128 MiB`), extracts only `user/message` (`source.kind == "user"`) and `assistant/message` `text` blocks, maintains monotonic byte-offset cursors. |
| `scripts/setup.py` | One-step locked runtime provisioner | Runs `uv sync --frozen`, probes SQLite FTS5 + component imports, installs transcript redactor, writes `engine.json` / `config.json` via `os.replace`. |
| `scripts/updater.py` | Background GitHub Release updater | Stages candidate under `update/generations/<tag>`, provisions candidate before switching `current.json`. |

## 2. Host Interaction Protocols & State Invariants

- **JS ↔ Python Path Parity**: `paths.cjs` and `scripts/mindie_paths.py` resolve identical paths across POSIX (`XDG_DATA_HOME` / `XDG_CONFIG_HOME`) and Windows (`LOCALAPPDATA` / `APPDATA`), verified by `tests/test_paths.py`.
- **Warm Sidecar Protocol**: `host3.mjs` communicates with `scripts/mindie_sidecar.py` over line-delimited JSON (`{"id", "op", "args"}` → `{"id", "ok", "result"|"error"}`).
- **Explicit Consent & Community Authority**: `op_activate` reads the existing profile choice without creating consent. `op_configure` requires `choice in {"contribute", "disabled"}` and writes the shared authority; contribute enables both knowledge sharing and sanitized failure reporting. Missing or legacy consent returns `needs_choice` until an explicit choice. All producers and the reporter use `config_home()/diagnostics.json`.
- **Loopback Summary Routing**: `host3.mjs` writes `state_dir()/summary-endpoint.json` (`http://127.0.0.1:<port>`, bearer token, model, provider). `dsh_organizer.py` posts the batch once and maps HTTP/timeout failures after transmission to `status="outcome_unknown"` (`billing_status="unknown"`).

## 3. Timeout Budgets & `v0.0.2` Anti-Over-Defensive Iron Rules

- **Timeout Arithmetic**:
  - Sidecar / MCP knowledge RPCs: `30.0s` (`query`, `explain`, `feedback`); feed sync: `120.0s`.
  - Loopback organizer HTTP POST: `DEADLINE_SECONDS = 300s` (`MAX_REQUEST_BYTES = MAX_RESPONSE_BYTES = 256 KiB`).
  - Transcript incremental scan: `max_seconds = 2.0s`, `max_text_bytes = 48 KiB`, `MAX_DECOMPRESSED_BYTES = 128 MiB`.
  - Setup & Updater: standalone `uv sync` has a `900s` stage budget and redactor installation `300s`. Updater supplies one `600s` total candidate setup deadline and reserves `10s` for cleanup. GitHub API lookup `30s`, asset download `60s`. Explicit contribution configuration has a `600s` outer budget including community operations and the shared reporter's `75s` startup; ordinary sidecar calls use `120s`.
- **Four `v0.0.2` Iron Rules**:
  1. Lightweight SQLite connections (WAL + busy timeout set once on open; no per-query schema reflection or `.owner` `lstat` ceremony).
  2. Reserve `unknown` (`outcome_unknown`) strictly for irreversible external model/network writes; never brick local state on transient errors.
  3. Use `os.replace()` for atomic file updates (`write_private`, `copy_private`, `atomic_json`); no Win32 `icacls`/`ctypes` ACL ceremonies or recursive symlink paranoia.
  4. Every script carries a top-of-file `[Role]`, `[Invariants]`, `[Timeouts & Locks]`, `[Side-Effect Policy]` contract docstring.

## 4. Verification Commands

```bash
# Run full unit & contract test suite (< 5s)
/Volumes/data/code/mindie-release-20261014/codex-interrupt-final-env-20261016/bin/python -m pytest
```

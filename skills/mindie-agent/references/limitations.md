# Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.1.28`) is a thin DeepSeek Harness adapter (`1,466` total lines `<= 1,500` across `paths.cjs`, `host.mjs`, `host3.mjs`, and `scripts/{mindie_launch,dsh_transcript,setup}.py`). `cordis.patch.yml`
registers two Host rows using stable DSH packages and the local `host.mjs`:

| Row ID | Package / Entry | Purpose |
| --- | --- | --- |
| `mindie-agent-skill` | `@deepseek-ai/dsh-skill-filesystem` | Exposes the `mindie-agent` workflow skill to every preset (`user-invocable: false`, silently default-on) |
| `mindie-agent-host` | `./host.mjs` (mounts `./host3.mjs` and two per-session stdio MCP servers) | Attaches `remote-dev` (14 `dsh` tools) and `mindie-tools` (11 component/status tools + 5 `knowledge_*`), serves the loopback summary bridge, and runs consent-first `Stop` capture |

`scripts/` retains only `mindie_launch.py`, `dsh_transcript.py`, and `setup.py`
(with zero `_knowledge_fallback` shadow shims); all shared runtime, 9-domain
binding, MCP routing, sharing, update, lock, and GC behavior delegates to
`mindie-harness` (`API_VERSION = 1`).

## Paths

Defaults (override with the corresponding `MINDIE_DSH_*` environment variable):

| Item | Default |
| --- | --- |
| data | `mindie-agent` under `$XDG_DATA_HOME` (`MINDIE_DSH_DATA_HOME` / `MINDIE_DATA_HOME`) |
| state | `<data>/state/dsh` (`MINDIE_DSH_STATE_DIR`) |
| transcript spools | `<data>/state/dsh/spools/<24hex>.jsonl` (bounded `<= 64` cold spools) |
| config | `mindie-agent/dsh.json` under `$XDG_CONFIG_HOME` (canonical; legacy `dsh/config.json` under the same base remains readable; UTF-8 BOM stripped) |
| engine configs | content-addressed `<config>/engines/dsh/<20hex>/<domain>.engine.json` across 9 domains (plus fallback `<config>/dsh.engine.json`) |
| runtime interpreter | `resolvePythonCommand`: absolute `python` in `dsh.json` -> `MINDIE_DSH_PYTHON` -> `uv` -> verified `PATH` Python (`WindowsApps` / 0-byte stubs excluded) |
| unified consent authority | `<config>/mindie-consent.json` |
| diagnostics policy | `<config>/diagnostics.json` |
| knowledge root | `<data>/knowledge` (engine `root` across registered `domain_configs`) |
| remote-dev state | `<data>/state/dsh/remote` (shared across parent/subagent sessions) |

`MINDIE_AGENT_CONFIG` / `MINDIE_DSH_CONFIG` select an explicit config file;
`MINDIE_DSH_CONFIG_HOME` keeps the legacy `<dir>/config.json` layout.

## Pinned `0.1.28` Runtime Components

The locked Python runtime (`pyproject.toml`, `runtime-requirements.txt`, `uv.lock`,
and `product-contract.json`) declares all 9 runtime packages and keeps the
background knowledge service idle at setup time (`knowledge.status` is not
started until a `knowledge_*` call or an authorized `Stop` capture):

- `mindie-harness` `>= 0.1.5` (`99962f4138f1402024a6d7b4b2596baaa36a4b00`)
- `mindie-knowledge` `0.13.25` (`9befde292ea0e6c79e9e5f475c266cbcf845d59a`)
- `remote-dev` `0.11.15` (`784fc2ca1db172f683889a6d47543c37141b3278`)
- `mindie-diagnostics` `0.5.17` (`7464cbb4381e9dc996871d192eb3f05f61dfdb28`)
- `mindie-debugger` `0.1.4` (`e15c2ed38d8f928db67c5bb1a94269f62207227c`)
- `mindie-profiler` `0.5.16` (`b702893129724b852c5abcb8971724b8b0a6e870`)
- `mindie-sim` `0.9.18` (`83fb8288aa998e0a6ad345ca7a16269f0181545b`)
- `npu-top` `0.3.5` (`72654fe6047df6ccc67746c242b3ce18bcabc945`)
- `mindie-coordinator` `0.7.14` (`79df7c373bf35fda2a94907d1f3beb51c6462357`)

## Unified Contribution Switch

- `mindie-consent.json` controls `contribute`, `local-only`, and `read-only` /
  `disabled`; `setup.py sharing --status|--enable|--local-only|--disable`
  (mutually exclusive flags; `exit 2` on conflict) is the only supported switch.
- Existing `disabled`, `later`, and `read-only` choices stay off across upgrades,
  and explicitly revoked (`inactive`) sessions are preserved across
  `admission_path` migrations during `setup.py init`. Legacy knowledge-only
  `contribute` records lacking `choice_revision` require one explicit
  confirmation before fault uploads begin.
- Keep three states distinct: (1) local consent configuration written,
  (2) background worker running, and (3) external GitHub Issue or knowledge PR
  created.

## Operational Notes

- `remote-dev` job records under `<data>/state/dsh/remote` are shared so
  subagents can inspect/stop parent jobs; `REMOTE_DEV_SESSION_ID` isolates
  per-session read ledgers.
- `host3.mjs` schedules `mindie_launch.py update --background` only while DSH is
  alive and no agent is `running`; nothing runs as an OS-level scheduled task.
- `mindie_doctor` is read-only and bounded; a failing check is reported once and
  never blocks the task.

## Uninstall

1. Disable `mindie-agent-dsh` in the DSH Plugins page (or via `plugin_manager`).
2. Optionally stop the local knowledge service with the configured `python` from
   `dsh.json`: `<python> -m mindie_knowledge.loop.cli stop --config <config>/dsh.engine.json`.
3. Remove the DSH-specific profile and state paths (`<config>/dsh.json`,
   `<config>/dsh.engine.json`, `<data>/state/dsh`, and `<data>/runtimes/dsh`) so
   shared `mindie-agent` profiles for other hosts remain intact.

# Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.1.27`) is a thin DeepSeek Harness adapter. `cordis.patch.yml`
registers two Host rows using stable DSH packages and the local `host.mjs`:

| Row ID | Package / Entry | Purpose |
| --- | --- | --- |
| `mindie-agent-skill` | `@deepseek-ai/dsh-skill-filesystem` | Exposes the `mindie-agent` workflow skill to every preset (`user-invocable: false`, silently default-on) |
| `mindie-agent-host` | `./host.mjs` (mounts `./host3.mjs` and two per-session stdio MCP servers) | Attaches `remote-dev` (14 `dsh` tools) and `mindie-tools` (deterministic tools + `mindie_doctor` + 5 `knowledge_*`), serves the loopback summary bridge, and runs consent-first `Stop` capture |

`scripts/` retains only `mindie_launch.py`, `dsh_transcript.py`, and `setup.py`;
all shared runtime, MCP routing, sharing, update, lock, and GC behavior delegates
to `mindie-harness` (`API_VERSION = 1`).

## Paths

Defaults (override with the corresponding `MINDIE_DSH_*` environment variable):

| Item | Default |
| --- | --- |
| data | `mindie-agent` under `$XDG_DATA_HOME` (`MINDIE_DSH_DATA_HOME` / `MINDIE_DATA_HOME`) |
| state | `<data>/state/dsh` (`MINDIE_DSH_STATE_DIR`) |
| config | `mindie-agent/dsh.json` under `$XDG_CONFIG_HOME` (canonical; legacy `dsh/config.json` under the same base remains readable) |
| engine config | the `engine_config` binding in `dsh.json` (default `<config>/dsh.engine.json`) |
| runtime interpreter | the absolute `python` binding in `dsh.json` (fallback `<data>/venv/bin/python`) |
| unified consent authority | `<config>/mindie-consent.json` |
| diagnostics policy | `<config>/diagnostics.json` |
| knowledge state | `<data>/vllm-ascend` (engine `root`/`domain`) |
| remote-dev state | `<data>/state/dsh/remote` (shared across parent/subagent sessions) |

`MINDIE_AGENT_CONFIG` / `MINDIE_DSH_CONFIG` select an explicit config file;
`MINDIE_DSH_CONFIG_HOME` keeps the legacy `<dir>/config.json` layout.

## Pinned `0.1.27` Runtime Components

The locked Python runtime keeps the background knowledge service idle at setup
time (`knowledge.status` is not started until a `knowledge_*` call or an
authorized `Stop` capture). Component pins are unchanged from the prior release:

- `mindie-harness` `0.1.0` (`f05569947dbf0131061d8698bb779d024df0c43b`)
- `mindie-knowledge` `0.13.23` (`8fe8455669e88ef6963314332422653841518900`)
- `remote-dev` `0.11.13` (`64c5a8d0ca9fe344e7af05f89fc0de6249f2368a`)
- `mindie-diagnostics` `0.5.16` (`54ac7c03c78f9e02f379051d1613a4427559e10d`)
- `mindie-debugger` `0.1.4` (`e15c2ed38d8f928db67c5bb1a94269f62207227c`)
- `mindie-profiler` `0.5.16` (`b702893129724b852c5abcb8971724b8b0a6e870`)
- `mindie-sim` `0.9.18` (`83fb8288aa998e0a6ad345ca7a16269f0181545b`)
- `mindie-coordinator` `0.7.13` (`a3c7336faa0bc14ebdb8f2fd75606c82e96422e9`)

## Unified Contribution Switch

- `mindie-consent.json` controls `contribute`, `local-only`, and `read-only` /
  `disabled`; `setup.py sharing --status|--enable|--local-only|--disable` is the
  only supported switch.
- Existing `disabled`, `later`, and `read-only` choices stay off across upgrades.
  Legacy knowledge-only `contribute` records lacking `choice_revision` require
  one explicit confirmation before fault uploads begin.
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

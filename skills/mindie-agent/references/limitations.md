# Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.1.25`) is a DeepSeek Harness adapter that registers Host rows in `cordis.patch.yml` using standard DSH packages and the local Host plugin launcher:

| Row ID | Package / Entry | Purpose |
| --- | --- | --- |
| `mindie-agent-skill` | `@deepseek-ai/dsh-skill-filesystem` | Exposes the semantic `mindie-agent` workflow skill to every preset |
| `mindie-agent-host` | `./host.mjs` (mounts `./host3.mjs` plus per-session `remote-dev` and `mindie-tools` MCP servers) | Registers default-enabled `mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_distill`, `knowledge_feedback`, and `knowledge_sync`; handles opt-in `agent/turn-stopping` zstd v4 capture handoff, model summary bridge, and idle generation hot-reload |

## Paths

Defaults (override with the corresponding `MINDIE_DSH_*` environment variable):

| Item | Default |
| --- | --- |
| data / runtime | `$XDG_DATA_HOME/mindie-agent/dsh` (`~/.local/share/mindie-agent/dsh`) |
| config | `$XDG_CONFIG_HOME/mindie-agent/dsh` (`~/.config/mindie-agent/dsh`) |
| runtime interpreter | the `python` binding in `<config>/config.json` (`<data>/runtimes/<key>/venv/bin/python` or `update/component-runtimes/<id>/runtime/.venv/bin/python`) |
| persistent worker scripts | `<data>/state/bin/` |
| engine config | the `engine_config` binding in `<config>/config.json` |
| adapter config | `<config>/config.json` |
| unified consent authority | `<config>/mindie-consent.json` |
| diagnostics policy | `<config>/diagnostics.json` |
| knowledge state | `<data>/vllm-ascend` |
| remote-dev state | `<data>/state/remote` |

## Pinned `0.1.25` Runtime Components

The locked Python runtime keeps the background knowledge service idle at setup time (`knowledge.status: "inactive"` until a knowledge tool or opted-in `Stop` capture runs).

Current runtime pins:

- `mindie-harness` `0.1.0` (`f05569947dbf0131061d8698bb779d024df0c43b`)
- `mindie-knowledge` `0.13.23` (`8fe8455669e88ef6963314332422653841518900`)
- `remote-dev` `0.11.13` (`64c5a8d0ca9fe344e7af05f89fc0de6249f2368a`)
- `mindie-diagnostics` `0.5.16` (`54ac7c03c78f9e02f379051d1613a4427559e10d`)
- `mindie-debugger` `0.1.4` (`e15c2ed38d8f928db67c5bb1a94269f62207227c`)
- `mindie-profiler` `0.5.16` (`b702893129724b852c5abcb8971724b8b0a6e870`)
- `mindie-sim` `0.9.18` (`83fb8288aa998e0a6ad345ca7a16269f0181545b`)
- `mindie-coordinator` `0.7.13` (`a3c7336faa0bc14ebdb8f2fd75606c82e96422e9`)

## Unified Contribution Switch

- Community knowledge sharing and automatic sanitized tool fault reporting share the persistent switch in `mindie-consent.json` (`contribute` enables both; `disabled` turns both off).
- Existing `disabled`, `later`, and `read-only` choices stay off across upgrades. Legacy knowledge-only `contribute` records lacking `choice_revision` require one explicit user confirmation before fault uploads begin.
- Keep three states distinct: (1) local consent configuration written, (2) background organizer/reporter worker running, and (3) external GitHub Issue or knowledge PR created.

## Operational Notes

- `remote-dev` job records under `<data>/state/remote` are isolated per session (`REMOTE_DEV_SESSION_ID`).
- Background release and flexible component updates run while the DSH Host process is alive and swap generations when all agents are idle.

## Uninstall

1. Disable `mindie-agent-dsh` in the DSH Plugins page (or via `plugin_manager`).
2. Optionally stop the local knowledge service:
   `<data>/venv/bin/python -m mindie_knowledge.loop.cli stop --config <config>/engine.json`
3. Remove `mindie-agent/dsh` under both the data and config roots if you also want to delete local runtime files, knowledge snapshots, and saved settings.

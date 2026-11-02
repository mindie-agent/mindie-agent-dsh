# Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.1.12`) is a DeepSeek Harness adapter that registers Host rows in `cordis.patch.yml` using standard DSH packages and the local Host plugin launcher:

| Row ID | Package / Entry | Purpose |
| --- | --- | --- |
| `mindie-agent-skill` | `@deepseek-ai/dsh-skill-filesystem` | Exposes `/mindie-agent` (`disable-model-invocation: true`) to every preset |
| `mindie-agent-host` | `./host.mjs` (mounts `./host3.mjs` plus per-session `remote-dev` and `mindie-tools` MCP servers) | Registers `mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_distill`, `knowledge_feedback`, and `knowledge_sync`; handles `agent/turn-stopping` zstd v4 capture handoff, model summary bridge, and idle generation hot-reload |

## Paths

Defaults (override with the corresponding `MINDIE_DSH_*` environment variable):

| Item | Default |
| --- | --- |
| data / runtime | `$XDG_DATA_HOME/mindie-agent/dsh` (`~/.local/share/mindie-agent/dsh`) |
| config | `$XDG_CONFIG_HOME/mindie-agent/dsh` (`~/.config/mindie-agent/dsh`) |
| runtime interpreter | `<data>/venv/bin/python` |
| persistent worker scripts | `<data>/state/bin/` |
| engine config | `<config>/engine.json` |
| adapter config | `<config>/config.json` |
| unified consent authority | `<config>/mindie-consent.json` |
| diagnostics policy | `<config>/diagnostics.json` |
| knowledge state | `<data>/vllm-ascend` |
| remote-dev state | `<data>/state/remote` |

## Pinned `0.1.12` Runtime Components

`scripts/setup.py` runs `uv sync --locked` against `uv.lock`, installs persistent worker scripts, and writes `engine.json` and `diagnostics.json` without starting the knowledge service or syncing feeds during installation (`knowledge.status: "inactive"` until `/mindie-agent` is invoked).

Current runtime pins:

- `mindie-knowledge` `0.13.10` (`7936a4fda67f6808d8fa73824363a4d7345766e5`)
- `remote-dev` `0.11.4` (`7c67876d716feb3949e4dbf33eb5e06980ff0d36`)
- `mindie-diagnostics` `0.5.5` (`f2f2f946c172afc99342a84c0c1ed244f6d44e01`)
- `mindie-debugger` `0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`)
- `mindie-profiler` `0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`)
- `mindie-sim` `0.9.2` (`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`)
- `mindie-coordinator` `0.7.3` (`79a8b28eef72d57f965a01c6660849f024fbb1c0`)
- `npu-top` `0.3.1` (`8f0cb3ec66393812e90e118cd73704070c6027a2`)

## Unified Contribution Switch

- Community knowledge sharing and automatic sanitized tool fault reporting share the persistent switch in `mindie-consent.json` (`contribute` enables both; `disabled` turns both off).
- Existing `disabled`, `later`, and `read-only` choices stay off across upgrades. Legacy knowledge-only `contribute` records lacking `choice_revision` require one explicit user confirmation before fault uploads begin.
- Keep three states distinct: (1) local consent configuration written, (2) background organizer/reporter worker running, and (3) external GitHub Issue or knowledge PR created. See [the contribution notice](user-notice.md).

## Operational Notes

- `remote-dev` job records under `<data>/state/remote` are isolated per session (`REMOTE_DEV_SESSION_ID`).
- Background release and flexible component updates run while the DSH Host process is alive and swap generations when all agents are idle.

## Uninstall

1. Disable `mindie-agent-dsh` in the DSH Plugins page (or via `plugin_manager`).
2. Optionally stop the local knowledge service:
   `<data>/venv/bin/python -m mindie_knowledge.loop.cli stop --config <config>/engine.json`
3. Remove `mindie-agent/dsh` under both the data and config roots if you also want to delete local runtime files, knowledge snapshots, and saved settings.

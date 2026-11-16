# Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.1.22`) is a DeepSeek Harness adapter that registers Host rows in `cordis.patch.yml` using standard DSH packages and the local Host plugin launcher:

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
| runtime interpreter | the `python` binding in `<config>/config.json` (`<data>/runtimes/<key>/venv/bin/python` or `update/component-runtimes/<id>/runtime/.venv/bin/python`) |
| persistent worker scripts | `<data>/state/bin/` |
| engine config | the `engine_config` binding in `<config>/config.json` |
| adapter config | `<config>/config.json` |
| unified consent authority | `<config>/mindie-consent.json` |
| diagnostics policy | `<config>/diagnostics.json` |
| knowledge state | `<data>/vllm-ascend` |
| remote-dev state | `<data>/state/remote` |

## Pinned `0.1.22` Runtime Components

`scripts/setup.py` runs `uv sync --locked` against `uv.lock`, installs persistent worker scripts, and writes `engine.json` and `diagnostics.json` without starting the knowledge service or syncing feeds during installation (`knowledge.status: "inactive"` until `/mindie-agent` is invoked).

Current runtime pins:

- `mindie-knowledge` `0.13.22` (`c991b64a1ead277b5ce91b7835337b01aed03998`)
- `remote-dev` `0.11.12` (`c4d38f99c1ef5aace1c088af07647941adc2d394`)
- `mindie-diagnostics` `0.5.14` (`cd16baa406070bcd41bbe39a9e488cc8753c60c5`)
- `mindie-debugger` `0.1.3` (`33052a2fc10c7475e4b819577870ca822efcebd0`)
- `mindie-profiler` `0.5.14` (`889b6ef161862938cc07b1b4123c57e302965d48`)
- `mindie-sim` `0.9.15` (`9dbab02f10843c7b829d6fda7e7b9ad89e4f92e5`)
- `mindie-coordinator` `0.7.11` (`33e2c6966d21d62969a2148145e74885dc826e3c`)

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

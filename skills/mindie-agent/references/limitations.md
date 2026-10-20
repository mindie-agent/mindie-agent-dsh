# `0.0.3` Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.0.3`) is a thin DeepSeek Harness adapter that inserts three Host rows in `cordis.patch.yml` using only stable DSH packages and the local Host plugin launcher:

| Row ID | Package / Entry | Purpose |
| --- | --- | --- |
| `mindie-agent-skill` | `@deepseek-ai/dsh-skill-filesystem` | Exposes only this skill bundle (`/mindie-agent`, `disable-model-invocation: true`) to every preset |
| `mindie-agent-host` | `./host.mjs` (mounts `./host3.mjs`) | Registers `mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, and `knowledge_sync`; handles `agent/turn-stopping` zstd v4 capture handoff, model summary bridge, and generation swaps |
| `mindie-agent-remote-mcp` | `@deepseek-ai/dsh-mcp-client` | Launches the official `remote-dev` MCP server (`remote_dev.mcp.server`) |

No `@deepseek-ai/dsh-experimental-*` package is used and no shipped row or preset is overridden. Because all three rows are inserted at the Host layer, the shipped `standard` preset and other presets inherit them directly.

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

## Pinned `0.0.3` Runtime Components

`scripts/setup.py` is idempotent: it runs `uv sync --frozen` against `uv.lock`, installs persistent worker scripts under `state_dir/bin/`, updates `engine.json` and `diagnostics.json`, and performs an incremental knowledge feed sync.

Current development runtime pins:

- `mindie-knowledge` `0.13.0` — integration commit `704f75375210dccb034b8827959c028c25149061`
- `remote-dev` `0.10.3` — integration commit `0b84ac8a9e2fdccb66107bab21b59cdf59748aec`
- `mindie-diagnostics` `0.5.0` — integration commit `c32a2a0d5b3a593cb2e68fc40b37fe3c8ca02be6`

**Upgrade Boundary**: `0.0.3` is a normal semantic-version upgrade selected by the updater. The historical `0.0.2` same-version refresh note remains in `docs/same-version-refresh.md` for installations that stay on `0.0.2`; upgrading to `0.0.3` follows the tag above and still requires a full DSH process restart to load the new plugin generation.

## Unified Contribution Switch & Three-State Separation

- Community knowledge sharing and automatic sanitized tool fault reporting share the single persistent switch in `mindie-consent.json` (`contribute` enables both; `disabled` turns both off).
- Existing `disabled`, `later`, and `read-only` choices stay off across upgrades. Legacy knowledge-only `contribute` records lacking the unified consent revision (`choice_revision` / `needs_choice`) require one explicit user confirmation before fault uploads begin.
- Keep three states strictly separate: (1) local consent configuration written, (2) background organizer/reporter worker running, and (3) external GitHub Issue or knowledge PR created. See [the contribution notice](user-notice.md).

## Operational Limits

- `remote-dev` job records under `<data>/state/remote` are shared at the Host level across sessions.
- Background release checks run while the DSH Host process is alive; no OS-level scheduler runs while DSH is stopped.
- In `mindie-bench`, DSH is supported for L1/L2 G0–G2 evaluation; G3 native capture evaluation in `mindie-bench` is not yet wired for DSH.

## Uninstall

1. Disable `mindie-agent-dsh` in the DSH Plugins page (or via `plugin_manager`).
2. Optionally stop the local knowledge service:
   `<data>/venv/bin/python -m mindie_knowledge.loop.cli stop --config <config>/engine.json`
3. Remove `mindie-agent/dsh` under both the data and config roots if you also want to delete local runtime files, knowledge snapshots, and saved settings. Uninstalling the bundle does not delete user data directories automatically.

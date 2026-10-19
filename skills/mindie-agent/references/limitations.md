# `0.0.2` Scope, Paths, and Operational Boundaries

## Composition & Host Rows

`mindie-agent-dsh` (`0.0.2`) is a thin DeepSeek Harness adapter that inserts three Host rows in `cordis.patch.yml` using only stable DSH packages and the local Host plugin launcher:

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

## Pinned `0.0.2` Runtime Components & Same-Version Refresh

`scripts/setup.py` is idempotent: it runs `uv sync --frozen` against `uv.lock`, installs persistent worker scripts under `state_dir/bin/`, updates `engine.json` and `diagnostics.json`, and performs an incremental knowledge feed sync.

The `0.0.2` runtime pins:

- `mindie-knowledge` `0.12.2` — release commit `4e328305323eafc7fd43ecc0c53eb3cc0541fdef`
- `remote-dev` `0.10.1` — release commit `a6649222aae4851deec5774513f8319aece09051`
- `mindie-diagnostics` `0.4.3` — release commit `1b5a0eeb79d498c39002fc1853271c17e0f26e66`

**Same-Version Refresh Boundary**: `0.0.2` replaces the earlier `0.0.2` commit (`828c375d1f546ed653250526465b8075c1691390`). Because the legacy updater only selects versions strictly greater (`>`) than the installed version string, existing `0.0.2` installations must explicitly refresh both `scripts/setup.py` and the DSH bundle by exact SHA per `docs/same-version-refresh.md` and restart the full DSH process.

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

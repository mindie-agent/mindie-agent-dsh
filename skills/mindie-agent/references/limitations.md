# 0.0.1 scope, paths, and limits

## What 0.0.1 is

A thin DeepSeek Harness adapter that composes three Host rows from stable DSH
packages:

| Row | Package | Purpose |
| --- | --- | --- |
| `mindie-agent-skill` | `@deepseek-ai/dsh-skill-filesystem` | exposes only this skill bundle to every preset |
| `mindie-agent-knowledge-mcp` | `@deepseek-ai/dsh-mcp-client` | read-only knowledge tools + explicit feed sync |
| `mindie-agent-remote-mcp` | `@deepseek-ai/dsh-mcp-client` | the published `remote-dev` MCP server |

No `@deepseek-ai/dsh-experimental-*` package is used and no shipped row or
preset is overridden. The rows are Host rows, so they are inherited by the
shipped `standard` preset and any other preset without a mode switch.

## Paths

Defaults (override with the corresponding `MINDIE_DSH_*` environment variable):

| Item | Default |
| --- | --- |
| data / runtime | `$XDG_DATA_HOME/mindie-agent/dsh` (`~/.local/share/...`) |
| config | `$XDG_CONFIG_HOME/mindie-agent/dsh` (`~/.config/...`) |
| runtime interpreter | `<data>/venv/bin/python` |
| engine config | `<config>/engine.json` |
| adapter config | `<config>/config.json` |
| knowledge state | `<data>/vllm-ascend` |
| remote-dev state | `<data>/state/remote` |

## Install and upgrade

`setup.py` is idempotent: it re-runs `uv sync --frozen` against the committed
lock, regenerates the engine configuration, and performs an incremental feed
sync. Re-run it after upgrading the bundle or changing the data/config roots.

The runtime pins:

- `mindie-knowledge` `0.11.0` — release commit `81fb4e222f0cba5cffe50824ffd55b33d312283d`
- `remote-dev` `0.9.5` — release commit `46a91a5b409506760ea530f7037a3f9f04d9a2d7`
- `mindie-diagnostics` `0.4.1` — release commit `10cc3391ed358d7b7e0e1acb99ca5be4d1934b47`

## Deliberately absent in 0.0.1

- Community contribution / knowledge PR publication.
- Session-Stop transcript capture. The engine config carries a capture module
  that fails loudly if invoked; it is not silently empty.
- Usefulness voting (`knowledge_feedback`).
- Automatic GitHub issue reporting.
- Per-session knowledge identity; reads use the Host-level `dsh` identity.

## Uninstall

1. Disable `mindie-agent-dsh` in the DSH Plugins page (or the `plugin_manager`
   tool).
2. Optionally stop the knowledge service:
   `<data>/venv/bin/python -m mindie_knowledge.loop.cli stop --config <config>/engine.json`
3. Remove `mindie-agent/dsh` under both the data and config roots if the user
   also wants to delete local runtime, knowledge snapshots, and settings.
   Uninstalling the bundle never deletes these directories automatically.

## Privacy

Knowledge reads use local public content synchronized from
`mindie-agent/knowledge-vllm-ascend`; no conversation is uploaded. The
`remote-dev` tools act on endpoints the user selects. 0.0.1 has no capture or
community publication path.

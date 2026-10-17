# MindIE Agent for DeepSeek Harness `0.0.1`

Released 2026-10-17. GitHub Release: `mindie-agent/mindie-agent-dsh@v0.0.1`
(`draft=false`, `prerelease=false`).

`0.0.1` is the first DSH adapter for MindIE Agent. It composes stable DSH
packages only and works inside the shipped `standard` preset without a mode
switch.

## Highlights

- **`/mindie-agent` entry**: a user-invocable, model-hidden skill that injects
  the MindIE workflow only on explicit invocation.
- **Read-only Ascend knowledge**: `knowledge_query`, `knowledge_explain`, and
  an explicit `knowledge_sync` against a pinned local `mindie-knowledge`
  service.
- **Official remote-dev tools**: the published `remote-dev` MCP server,
  including remote file tools, bash, jobs, and artifacts.
- **Stability by composition**: only
  `@deepseek-ai/dsh-skill-filesystem` and `@deepseek-ai/dsh-mcp-client`; no
  `@deepseek-ai/dsh-experimental-*` package; no shipped row or preset
  overridden.

## Install

```bash
git clone --depth 1 --branch v0.0.1 \
  https://github.com/mindie-agent/mindie-agent-dsh.git /tmp/mindie-agent-dsh
uv run --quiet --no-project --python 3.11 /tmp/mindie-agent-dsh/scripts/setup.py
```

Then install and enable
`git+https://github.com/mindie-agent/mindie-agent-dsh.git#v0.0.1` in the DSH
Plugins page, open a new session, and type `/mindie-agent`.

## Paired components

| Component | Release | Exact commit |
| --- | --- | --- |
| `mindie-agent/knowledge` | `v0.11.0` | `81fb4e222f0cba5cffe50824ffd55b33d312283d` |
| `mindie-agent/remote-dev` | `v0.9.5` | `46a91a5b409506760ea530f7037a3f9f04d9a2d7` |
| `mindie-agent/diagnostics` | `v0.4.1` | `10cc3391ed358d7b7e0e1acb99ca5be4d1934b47` |

The complete transitive lock is `uv.lock`. All three Releases are ordinary
(non-draft, non-prerelease); their tag commits, locked commits, wheel/sdist
metadata, and `SHA256SUMS` were verified. See
[`verification.md`](verification.md).

## Verified environment

macOS 27.0.1 arm64, DSH `0.2.0-rc.2`, Node `22.22.1`, uv `0.10.8`, runtime
Python `3.11.15`, SQLite `3.50.4`. Offline contract tests: 12 passed.

## Known limits

- No Session-Stop capture, community contribution, feedback, or automatic
  fault reporting.
- Knowledge identity is Host-level (`dsh`), not per-session.
- remote-dev state is Host-level and shared.
- Real remote Ascend execution, Windows, and Linux were not part of this
  acceptance run.
- No automatic updater; re-run `setup.py` and switch tags explicitly.

## Assets

- `mindie-agent-dsh-0.0.1.tgz` — installable package contents
- `SHA256SUMS` — checksums for the release asset

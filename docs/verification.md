# 0.0.1 verification record

Date: 2026-10-17. This file records what was actually executed, not a target
design. Anything not listed here is unverified.

## Environment

| Item | Value |
| --- | --- |
| Host | macOS 27.0.1 arm64 (Darwin 27.0.0) |
| DSH | `0.2.0-rc.2` |
| Node | `v22.22.1` |
| pnpm | `11.25.0` |
| uv | `0.10.8` |
| Runtime Python | `3.11.15` (uv-managed, venv) |
| SQLite | `3.50.4` (FTS5 `contentless_delete=1` probe passed) |
| git | `2.54.0` |

## Paired components

Every component Release is `draft=false` and `prerelease=false`. Each tag was
dereferenced through the GitHub API; the tag commit matches the lock exactly.
Both wheel and sdist were downloaded with `gh release download` and verified
against the publication's `SHA256SUMS`.

| Component | Release | Tag commit | Lock pin | Artifact check |
| --- | --- | --- | --- | --- |
| `mindie-agent/knowledge` | `v0.11.0` | `81fb4e222f0cba5cffe50824ffd55b33d312283d` | same | wheel + sdist `OK`, wheel metadata `0.11.0` |
| `mindie-agent/remote-dev` | `v0.9.5` | `46a91a5b409506760ea530f7037a3f9f04d9a2d7` | same | wheel + sdist `OK`, wheel metadata `0.9.5` |
| `mindie-agent/diagnostics` | `v0.4.1` | `10cc3391ed358d7b7e0e1acb99ca5be4d1934b47` | same (transitive) | wheel + sdist `OK`, wheel metadata `0.4.1` |

Observed command:

```text
$ for d in knowledge remote diagnostics; do (cd "$d" && shasum -a 256 -c SHA256SUMS); done
mindie_knowledge-0.11.0-py3-none-any.whl: OK
mindie_knowledge-0.11.0.tar.gz: OK
remote_dev-0.9.5-py3-none-any.whl: OK
remote_dev-0.9.5.tar.gz: OK
mindie_diagnostics-0.4.1-py3-none-any.whl: OK
mindie_diagnostics-0.4.1.tar.gz: OK
```

## Runtime setup

`uv run --quiet --no-project --python 3.11 scripts/setup.py` completed in
18.49s with the runtime already cached:

```json
{
  "status": "ready",
  "components": {
    "mindie-knowledge": "0.11.0",
    "remote-dev": "0.9.5",
    "mindie-diagnostics": "0.4.1"
  },
  "knowledge": {
    "status": "ready",
    "feeds": [
      { "repository": "mindie-agent/knowledge-vllm-ascend", "status": "synced" }
    ]
  }
}
```

The synced feed commit reported by the service was
`cec3249ccd75e7a73cf2f2f776117e0ec385795b` with 661 entries.

## Bundle installation

The bundle was installed with the `plugin_manager` tool against the DSH Web
profile:

```text
install_bundle(target=/Users/maoxx241/code/mindie-agent-dsh)
-> stage=enable, enabled=true, application=applied
```

`list_plugins` reported all three rows `active`:

- `include:mindie-agent-skill` → `@deepseek-ai/dsh-skill-filesystem`
- `include:mindie-agent-knowledge-mcp` → `@deepseek-ai/dsh-mcp-client`
- `include:mindie-agent-remote-mcp` → `@deepseek-ai/dsh-mcp-client`

`dsh --profile web --dump-config` contains the same three inserted rows, with
no shipped row or preset overridden.

## Skill discovery

The skill is present but deliberately excluded from model invocation:

```text
$ skill(name="mindie-agent")
Error: skill "mindie-agent" is not available for model invocation
```

This proves the filesystem provider discovered the skill and honored
`disable-model-invocation: true`. The documented `/mindie-agent` user gesture
is the only injection path; it was not executed by the agent during acceptance,
so the visual `/` menu rendering remains for the user to confirm.

## Knowledge tools

The MCP server is reached through the real DSH bridge
(`mcp__mindie-knowledge__*`), not only through a manual stdio probe.

| Call | Result |
| --- | --- |
| `knowledge_query({"query": "vllm-ascend 自定义算子 精度测试", "limit": 2})` | returned 2 citation groups with refs and `related_next` |
| `knowledge_explain({"ref": "mindie://vllm-ascend/b8565f0a...c0d63"})` | returned the task navigation and `first_block_ref` |
| `knowledge_sync({})` | returned a `synced` receipt for `knowledge-vllm-ascend` at `cec3249c...` |

An earlier manual stdio probe also exercised initialize, tools/list,
tools/call, explain, and sync before the bundle was installed.

## Remote-dev tools

`mcp__remote-dev__*` tools registered from the component's own
`remote_dev.mcp.server`. A deliberately unreachable endpoint produced a
visible failure rather than a fake success:

```text
mcp__remote-dev__remote_probe({"host": "127.0.0.1", "port": 1, "connect_timeout_ms": 2000})
Error: Remote tool failed (failed).
category=connection_unavailable submission_state=not_sent
SSH RPC unavailable; request was not sent: ssh: connect to host 127.0.0.1 port 1: Connection refused
```

No real Ascend endpoint was contacted in this acceptance run; remote execution
against actual NPU hardware remains unverified for 0.0.1.

## Offline tests

```text
python3 -m unittest discover -s tests -v
Ran 12 tests ... OK
```

Covered: manifest/bundle contract, stable-package-only patch, skill frontmatter
and size, release pin consistency, JS/Python path agreement, setup dry-run
write-freedom, capture stub failure, MCP initialize/tools list, actionable
setup errors, unknown tools, and rejected identity arguments.

## Not established

- `/mindie-agent` visual slash-menu rendering in the Web client.
- Real remote Ascend execution.
- Windows and Linux behavior.
- Session capture, feedback, contribution, and reporting (not implemented).

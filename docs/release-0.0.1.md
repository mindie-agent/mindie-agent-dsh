# MindIE Agent for DeepSeek Harness `0.0.1`

Released 2026-10-17. GitHub Release: `mindie-agent/mindie-agent-dsh@v0.0.1`
(`draft=false`, `prerelease=false`).

`0.0.1` is the parity release of the DSH adapter. It composes stable DSH
packages only and works inside the shipped `standard` preset without a mode
switch.

## Highlights

- **Native `/mindie-agent` entry**: a user-invocable skill plus Host tools for
  `mindie_entry` init/status/choose/deactivate, sharing and reporting controls.
- **Stop capture and indexing**: the DSH v4 transcript adapter reads public
  user/assistant text only, the Stop helper writes one bounded capture, and
  deterministic redaction feeds the real `mindie-knowledge` service.
- **Background DSH-model summary**: a loopback summary endpoint backed by the
  active agent route; one identity, one model invocation, one validated
  `mindie-summary-outcome/1` envelope.
- **Community contribution**: the install-level choice is asked once at first
  activation; `contribute` defaults to `mindie-agent/knowledge-vllm-ascend`,
  resolves or creates the account fork, and publishes a PR after the idle
  outbox flush. Failed and unknown outcomes stay visible.
- **Knowledge feedback**: query/explain/feedback/sync with the exact
  `feedback_ref`; feedback is non-publishable while sharing is off.
- **Optional diagnostics reporting**: status/enable/disable through the shared
  `mindie-diagnostics` component, independent of contribution.
- **Stability by composition**: only
  `@deepseek-ai/dsh-skill-filesystem`, `@deepseek-ai/dsh-mcp-client` and this
  package's own Host plugin; no `@deepseek-ai/dsh-experimental-*` package; no
  shipped row or preset overridden.

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

| Platform | DSH | Node | Python | Result |
| --- | --- | --- | --- | --- |
| macOS 27.0.1 arm64 | `0.2.0-rc.2` | 22.22.1 | 3.11.15 | live model, capture, redaction, summary, contribution PR |
| 4090 WSL Ubuntu 24.04 | `0.2.0-rc.2` | 22.22.1 | 3.11.15 | live model, capture, summary, plugin/MCP |
| Lenovo native Windows | `0.2.0-rc.2` | 24.19.0 | 3.11.16 | live model, capture, summary, plugin/MCP |

Offline tests: 18 Python tests and the summary-endpoint Node test pass.
`tests/live_capture_smoke.py` passed on all three platforms.

## Known limits

- The successful contribution was a test capture; PR #51 was merged into the
  canonical knowledge repository by automation. The test content is disclosed
  in `verification.md`; no attempt was made to remove it from the public feed.
- The Lenovo knowledge feed sync is blocked by that machine's local git proxy
  configuration; capture, summary, plugin activation and MCP are independently
  verified. Keep the feed synced for retrieval on that host.
- There is no silent self-updater in 0.0.1. Installation/update is the DSH
  plugin manager plus `scripts/setup.py`; switch tags explicitly.
- Knowledge identity is per-session at the Host plugin boundary; remote-dev
  state remains Host-level and shared.

## Assets

- `mindie-agent-dsh-0.0.1.tgz` — source/installable package contents
- `SHA256SUMS` — checksum for the release asset

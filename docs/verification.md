# Verification Architecture & Evidence (`0.0.2`)

Host target: DeepSeek Harness `>=0.2.0-rc.2 <0.2.2-0`; certified on `0.2.0-rc.2`
and `0.2.1-rc.5`.

This document summarizes the verification scope and acceptance evidence for `mindie-agent-dsh` across local contract tests, cross-platform host integration, unified consent and fault-reporting checks, and `mindie-bench` evaluation.

## 1. Pinned `0.0.2` Component Baseline

- `mindie-knowledge 0.12.2` (`4e328305323eafc7fd43ecc0c53eb3cc0541fdef`)
- `remote-dev 0.10.1` (`a6649222aae4851deec5774513f8319aece09051`)
- `mindie-diagnostics 0.4.3` (`1b5a0eeb79d498c39002fc1853271c17e0f26e66`)

## 2. Core Functional & Protocol Verification

- **Host Entry & Unified Consent (`mindie_entry`)**:
  - `mindie_entry` supports `init`, `status`, `choose`, `deactivate`, and `reporting-status`.
  - Knowledge operations (`init`, `choose`, `knowledge_query`, `knowledge_explain`, `knowledge_distill`, `knowledge_feedback`, `knowledge_sync`), lazy loopback summary server startup (`ensureSummaryServer`), and `agent/turn-stopping` capture require an explicit `/mindie-agent` invocation in the current session (`knowledge_lease` with `entry_enabled = 1`), whereas `remote-dev` MCP tools and factual `mindie_entry(op="status" | "reporting-status")` work without `/mindie-agent`.
  - Community knowledge sharing and automatic sanitized fault reporting share a single persistent switch in `mindie-consent.json`.
  - Explicitly selecting `contribute` configures public knowledge sharing (defaulting to `mindie-agent/knowledge-vllm-ascend` with on-demand personal fork creation/binding) and starts the native background fault-reporting worker; selecting `disabled` turns both off and withdraws unsent reports.
  - Legacy `disabled`, `later`, and `read-only` records remain off across upgrades. Legacy knowledge-only `contribute` records lacking the unified consent revision (`choice_revision` / `needs_choice`) stay inactive for fault uploads until explicitly re-confirmed once.
  - Three states are verified and reported independently: (1) local consent configuration written, (2) background worker running, and (3) external GitHub Issue or PR created.
- **DSH v4 Transcript Capture & Redaction**:
  - `scripts/dsh_transcript.py` parses concatenated zstd frames from `session.v4.jsonl.zstd`, extracting only public user and assistant text while excluding hidden reasoning and tool payloads.
  - Deterministic credential/IPv4 redaction and Gitleaks scanning run before local material indexing and community PR publication.
- **Diagnostic Reporting & Cross-Platform Deduplication**:
  - Local incidents record structured allowlisted metadata without prompts, transcripts, command arguments, credentials, or private paths.
  - Synthetic fault-reporting verification confirmed end-to-end issue creation, cross-platform fingerprint deduplication against `mindie-agent/mindie-agent`, and immediate withdrawal when contribution is set to `disabled`.
- **Same-Version `0.0.2` Refresh (`828c375d1f546ed653250526465b8075c1691390` → `0.0.2`)**:
  - Explicit refresh via `scripts/setup.py` and exact-SHA bundle installation preserves existing knowledge materials, `mindie-consent.json` and community settings, `disabled` preferences, and active session leases.
  - Persistent worker scripts copied to `state_dir/bin/` continue serving `--identity` and loopback summary requests after the temporary setup checkout is removed.

## 3. Cross-Platform Host Verification Matrix

| Platform | Environment | Verified Scope |
| --- | --- | --- |
| **macOS arm64** | macOS 27.0.1, DSH `0.2.0-rc.2`, Node 22+, Python 3.11+ | Full `/mindie-agent` entry lifecycle, `knowledge_query` / `knowledge_explain` / `knowledge_feedback` / `knowledge_sync`, v4 zstd `Stop` capture & model summary indexing, auto-fork PR publication, unified consent fault reporting, and live launcher generation swap |
| **Ubuntu 24.04 WSL** | Linux (WSL2), DSH `0.2.0-rc.2`, Node 22.22, Python 3.11, SQLite 3.50 | Runtime provisioning, knowledge feed sync, v4 zstd public extraction & redaction, live `mindie_entry(init)` & `Stop` summary indexing, `remote_dev.mcp.server` startup, and live launcher generation swap |
| **Windows 11 Native** | Windows 11, DSH `0.2.0-rc.2`, Node 24.19, Python 3.11+, SQLite 3.53 | Non-elevated Task Scheduler knowledge service startup, ASCII-safe stdio bridge, v4 zstd `Stop` capture & summary indexing, same-version SHA refresh preservation, and live launcher generation swap |

## 4. `mindie-bench` Harness & Release Benchmark Evidence

- **Release CPU Microbenchmark (`benchmarks/release.json`)**:
  - Executes `benchmarks/release.py` (`public-transcript` / `public_100_message_parse_latency` in `us/op`), publishing `benchmark-results.json`, `benchmark-results.md` with every formal release.
- **`mindie-bench` L2 Canary (`cpu-rope-rotary-dim-64`)**:
  - `mindie-bench` PR #2 integrates DSH as a native headless evaluation harness (`dsh --profile bench --json`):
    - **G0**: `completed_pass` (11 turns, native bash/read/glob tools, real verifier).
    - **G1**: `completed_pass` (`remote-dev` MCP started and passed preflight).
    - **G2**: `completed_pass` (`knowledge_query`, `knowledge_explain`, and skill activation observed).
    - **G3**: Native capture evaluation in `mindie-bench` is currently scoped to Codex/Claude protocols and returns an explicit unsupported status on DSH rather than silently skipping.

## 5. Known Operational Boundaries

- **Same-Version Refresh**: Upgrading from the earlier `0.0.2` commit (`828c375d1f546ed653250526465b8075c1691390`) requires the explicit SHA refresh and full DSH process restart documented in [`docs/same-version-refresh.md`](same-version-refresh.md).
- **Host-Scoped Auto-Update**: Background update checks run while the DSH Host process is alive and swap child plugin generations (`host3.mjs`); skill files and the root `cordis.patch.yml` require a bundle update and DSH restart when changed.

## 6. DSH `0.2.1-rc.5` Interface Audit

The optional peer range `>=0.2.0-rc.2 <0.2.2-0` is checked against the exact
contracts this adapter uses. On Windows 11 with DSH `0.2.1-rc.5`
(`@deepseek-ai/dsh@0.2.1-rc.5`, Node 24.21.0, Python 3.11.4), the `0.1.10`
adapter mounted in the installed `desktop` profile without an exact-version
exemption: the MindIE data directory's `update/launcher.log` (on Windows
`%LOCALAPPDATA%\mindie-agent\dsh\update\launcher.log`) records
`mounted .../profiles/desktop/node_modules/mindie-agent-dsh/host3.mjs`.

| Interface | DSH `0.2.1-rc.5` evidence | Result |
| --- | --- | --- |
| Bundle patch | The running profile recomposed and loaded both inserted Host rows after installation. | pass |
| Skill filesystem | `customSkillDirs`, `providerName`, `includeDefaultRoots` and `watch` remain documented configuration fields; the skill root resolved through `createRequire(baseUrl)`. | pass |
| MCP stdio | `@deepseek-ai/dsh-mcp-client` still accepts `command`/`args`/`env`/`cwd`/`toolCallTimeoutMs`/`failOnStartupError`; both `remote_dev.mcp.server` and `component_mcp.py` completed an MCP initialize and tools/list exchange. | pass |
| Host tools | `ctx.tools.register` still takes `{name, description, parameters, output: {schema, render}, execute(args, exec)}`; `exec.agent` and `exec.signal` are present, and registration completed during the live mount. | pass |
| Agent events | `agent/pre-step` remains a waterfall receiving `{agent, messages, turn, step, signal}` plus `next`; `agent/turn-stopping` remains a serial event whose payload carries `{agent, turn, signal}` because the fused dispatcher injects `agent`. | pass |
| Agent route | `agent.options.provider` and `agent.options.model` still describe the session route. | pass |
| LLM stream | `ctx.llm.stream` still yields `text-delta`, `usage` (`inputTokens`/`outputTokens`/`cacheReadTokens`/`cacheWriteTokens`) and one terminal `finish` with `error`, `aborted` or `max-tokens` reasons. | pass |
| v4 transcript | Sessions are written as `session.v4.jsonl.zstd`; headers carry `cwd` and `delegationDepth`; `user/message` keeps `source.kind == "user"` and `assistant/message` keeps text blocks. | pass |
| Agent scope | `agent.ctx` remains available for per-session `ctx.plugin` registration of both MCP servers. | pass |

Known non-blocking subset note: DSH enforces `type`/`properties`/`required`/
`additionalProperties`/`items`/`enum`/`const` and ignores `minimum`, `maximum`
and `pattern` in tool parameters. The adapter keeps those annotations as
provider-side hints; the DSH tool layer does not enforce them.

# Verification Architecture & Evidence (`0.1.26`)

Host target: DeepSeek Harness `>=0.2.0-rc.2 <1.0.0`; certified on `0.2.0-rc.2`
and `0.2.1-rc.5`. This document summarizes the thin-plugin contract evidence for
`mindie-agent-dsh`.

## 1. Thin-Plugin Baseline

`0.1.26` keeps only six production files at the Host boundary:

| File | Contract |
| --- | --- |
| `paths.cjs` | canonical `dsh.json` vs legacy `config.json` precedence, data/state resolution, `backgroundSpawnOptions`, `buildChildEnv` |
| `host.mjs` | mounts `host3.mjs`; attaches `remote-dev` + `mindie-tools` per session through `@deepseek-ai/dsh-mcp-client`; disposes the first attach if the second fails |
| `host3.mjs` | loopback `/summary` (Bearer + 512 KiB), idle windowless update, pure-Node consent/project-root gate before transcript lookup or spawn |
| `scripts/mindie_launch.py` | pure-stdlib cold start, streaming seed verification, passive hook fast paths, `ManagedMCPAdapter("dsh")` + knowledge RPC fallback, summary HTTP client |
| `scripts/dsh_transcript.py` | DSH v4 concatenated-zstd public transcript projection (`FileIdentity`, `identify`, `read_material`) |
| `scripts/setup.py` | `init`, `sharing`, `doctor`, `update`, `release-metadata` delegation to `mindie_harness` |

The sum of these files stays `<= 1,200` lines; component Git SHAs are unchanged
from the prior release and remain pinned in `pyproject.toml`, `uv.lock`,
`runtime-requirements.txt`, and `harness-compatibility.json`.

## 2. Functional and Protocol Evidence

- **Two MCP surfaces, zero pre-call gates**:
  - `remote-dev` exposes exactly the 14 `dsh` tools (`remote_read` …
    `remote_artifact_push`) and no cross-harness aliases.
  - `mindie-tools` exposes deterministic components (`mindie_status`,
    `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`, `rsi_loop`,
    `experiment_ledger`, `diagnostics_status`, `diagnostics_bundle`), read-only
    `mindie_doctor`, and the 5 `knowledge_*` tools.
  - No `_meta`, `request_nonce`, `context_file`, or slash-command activation is
    required. `knowledge_query` runs against an initialized local domain in the
    contract suite.
- **Consent-first `Stop` capture**:
  - `host3.mjs` skips subagents (`delegationDepth > 0`), then checks
    `mindie-community.json` (`enabled === true`, non-empty absolute
    `project_roots`) and `mindie-consent.json`
    (`choice in {contribute, local-only}`) before any filesystem transcript
    lookup or child spawn.
  - `mindie_launch.py hook stop` re-checks the runtime `sharing_status` and
    `Admission.inspect(...).status`; an explicit `inactive` lease is never
    reactivated. Off/out-of-scope fast paths print `{}` in `< 100ms` with zero
    SQLite.
- **Summary bridge**:
  - `/summary` binds `127.0.0.1:0`, requires the bearer token, caps the body at
    512 KiB, serves `mindie-summary-request/1` and
    `mindie-distill-request/1`, and rejects digest-invalid input.
  - `summarizeWithLlm` maps DSH `usage` (`inputTokens`, `cacheReadTokens`,
    `cacheWriteTokens`, `outputTokens`) without claiming constrained decoding;
    C6 schemas travel in prompt text.
  - `summary-endpoint.json` is written atomically under `stateDir` and removed
    on `ctx.effect` disposal.
- **Windowless lifecycle**:
  - Every Node `spawn` includes `windowsHide: true`; background children use
    `backgroundSpawnOptions()` (`detached: platform !== 'win32'`).
  - Python children pass `CREATE_NO_WINDOW`; no `DETACHED_PROCESS` or
    `CREATE_NEW_CONSOLE` is referenced.
- **Sharing and privacy tiers**:
  - `setup.py sharing --status|--enable|--local-only|--disable` delegates to
    `mindie_harness.sharing`; `setup.py doctor` delegates to
    `mindie_harness.api.run_host`; `setup.py update` delegates to
    `mindie_harness.runtime.wheelhouse.update_runtime`.
  - Three states remain distinct: local consent written, worker running, and
    external GitHub Issue/PR created.

## 3. Isolated Contract Suite

| Test | Coverage |
| --- | --- |
| `tests/test_dsh_thin_plugin.py` | seed-lock streaming SHA-256/size, `dsh.json` vs legacy precedence, `<100ms` SQLite-free `Stop`, `local-only`/`contribute` parity, explicit `Admission` revocation, 14 remote tools + zero-`_meta` `knowledge_query`, `setup.py init/sharing/release-metadata`, compatibility registry |
| `tests/test_dsh_host.mjs` | `backgroundSpawnOptions('win32')`, Windows PATH normalization, canonical/legacy config precedence, `/summary` Bearer/512 KiB/route checks, windowless update/capture spawn flags |
| `tests/test_dsh_transcript.py` | public user/assistant extraction, reasoning/context exclusion, `[start, end)` cursor alignment, append/truncate identity |
| `tests/conftest.py` | full `HOME`/`APPDATA`/`XDG_*`/`MINDIE_*`/`REMOTE_DEV_AUTH_DIR`/`UV_CACHE_DIR`/`TMPDIR` isolation |

`setup.py release-metadata --source . --tests tests --revision <sha>` validates
`mindie-harness-compatibility/1`, every registered `code_paths`/`test_ids`, and
emits `mindie-plugin-release/1` for
`mindie-agent/mindie-agent-dsh`.

## 4. `mindie-bench` & Release Benchmark Evidence

- `benchmarks/release.py` (`public-transcript` /
  `public_100_message_parse_latency` in `us/op`) measures DSH v4
  `session.v4.jsonl.zstd` public-message extraction and hidden-reasoning
  filtering; every formal release publishes `benchmark-results.json` and
  `benchmark-results.md`.
- `mindie-bench` integrates DSH as a headless evaluation harness:
  **G0** `completed_pass` (native tools), **G1** `completed_pass`
  (`remote-dev` preflight), **G2** `completed_pass` (`knowledge_query` +
  `knowledge_explain`), and **G3** remains explicitly unsupported on DSH rather
  than silently skipped.
- CPU microbenchmarks measure local parser latency only; they are not an
  end-to-end productivity claim.

## 5. DSH `0.2.1-rc.5` Interface Audit

| Interface | Evidence | Result |
| --- | --- | --- |
| Bundle patch | The running profile recomposed and loaded both inserted Host rows. | pass |
| Skill filesystem | `customSkillDirs`, `providerName`, `includeDefaultRoots`, and `watch` remain valid; the skill root resolves through `createRequire(baseUrl)`. | pass |
| MCP stdio | `@deepseek-ai/dsh-mcp-client` accepts `command`/`args`/`env`/`cwd`/`toolCallTimeoutMs`/`failOnStartupError`; both `mindie_launch.py mcp` surfaces complete initialize and tools/list. | pass |
| Agent events | `agent/created`, `agent/turn-stopping`, and `agent/disposed` payloads carry `agent` (and `turn` on stop); `agent/pre-step` remains a waterfall. | pass |
| Agent route | `agent.options.provider` / `agent.options.model` describe the session route and drive summary routing. | pass |
| LLM stream | `ctx.llm.stream` yields `text-delta`, `usage`, and one terminal `finish` (`error` / `aborted` / `max-tokens`). | pass |
| v4 transcript | `session.v4.jsonl.zstd` headers carry `cwd` and `delegationDepth`; `user/message` keeps `source.kind == "user"` and `assistant/message` keeps text blocks. | pass |
| Agent scope | `agent.ctx.plugin(...)` registers both stdio MCP clients per session. | pass |

Known non-blocking subset note: DSH enforces
`type`/`properties`/`required`/`additionalProperties`/`items`/`enum`/`const` and
ignores `minimum`, `maximum`, and `pattern` in tool parameters. Those remain
provider-side hints.

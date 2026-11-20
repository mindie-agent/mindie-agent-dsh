# Verification Architecture & Evidence (`0.1.28`)

Host target: DeepSeek Harness `>=0.2.0-rc.2 <1.0.0`; certified on `0.2.0-rc.2`
and `0.2.1-rc.5`, and gated in CI via
`mindie-agent/mindie-bench/.github/workflows/verify-host-plugin.yml@main`
(`host: dsh`). This document summarizes the thin-plugin contract evidence for
`mindie-agent-dsh`.

## 1. Thin-Plugin Baseline

`0.1.28` keeps only six production files at the Host boundary (`1,466` total
lines `<= 1,500`):

| File | Contract |
| --- | --- |
| `paths.cjs` | UTF-8 BOM stripping, canonical `dsh.json` vs legacy `config.json` precedence, data/state resolution, `resolvePythonCommand` (`uv` / `WindowsApps` stub rejection without bare `'python'` on `win32`), `backgroundSpawnOptions`, `buildChildEnv` |
| `host.mjs` | mounts `host3.mjs`; resolves `sessionId` once per mount (caching `dsh-conn-<uuid>` when omitted); attaches `remote-dev` + `mindie-tools` per session through `@deepseek-ai/dsh-mcp-client` with `closed`-guarded mid-mount fiber disposal |
| `host3.mjs` | loopback `/summary` (Bearer + 512 KiB), idle windowless update, non-blank `turn` guard + pure-Node consent/project-root gate before transcript lookup or spawn |
| `scripts/mindie_launch.py` | pure-stdlib cold start, streaming seed verification, `stdin=DEVNULL` & `stdout=DEVNULL` seed bootstrap (byte-0 MCP JSON-RPC purity), passive hook fast paths, direct `ManagedMCPAdapter("dsh")` (zero `_knowledge_fallback` shadow shim), summary HTTP client |
| `scripts/dsh_transcript.py` | DSH v4 concatenated-zstd projection into byte-addressed UTF-8 spools (`identify(native_zstd) == identify(spool_path)`, native `cwd` containment, `32 MiB` / `5.0s` cap, locked cold eviction `<= 64`) |
| `scripts/setup.py` | single-lock `.dsh.json.update.lock` `init` (`inactive` revocation migration across `admission_path` + 9-domain `ensure_knowledge_binding(..., retire_previous=True, already_locked=True)`), mutually exclusive `sharing` flags, `doctor`, `update`, `release-metadata` |

All 9 runtime packages (`mindie-harness >= 0.1.5`, `mindie-knowledge 0.13.24`,
`remote-dev 0.11.14`, `mindie-diagnostics 0.5.17`, `mindie-coordinator 0.7.13`,
`mindie-debugger 0.1.4`, `mindie-profiler 0.5.16`, `mindie-sim 0.9.18`, and
`npu-top 0.3.5`) are declared and pinned across `pyproject.toml`,
`runtime-requirements.txt`, `uv.lock`, and `product-contract.json`.

## 2. Functional and Protocol Evidence

- **Two MCP surfaces, zero pre-call gates, byte-0 JSON-RPC purity**:
  - `remote-dev` exposes exactly the 14 `dsh` tools (`remote_read` …
    `remote_artifact_push`) and no cross-harness aliases.
  - `mindie-tools` exposes the 11 deterministic/component tools (`mindie_status`,
    `mindie_doctor`, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`,
    `rsi_loop`, `experiment_ledger`, `diagnostics_status`,
    `diagnostics_bundle`) and the 5 `knowledge_*` tools.
  - No `_meta`, `request_nonce`, `context_file`, or slash-command activation is
    required; cold-seed provisioning runs with `stdin=DEVNULL` and
    `stdout=DEVNULL` so live MCP `stdout` never receives seed banner JSON.
- **Consent-first `Stop` capture, revocation permanence & unified UTF-8 spools**:
  - `host3.mjs` skips subagents (`delegationDepth > 0`) and blank/omitted `turn`
    notifications, then checks `mindie-community.json` (`enabled === true`,
    non-empty absolute `project_roots`) and `mindie-consent.json`
    (`choice in {contribute, local-only}`) before any filesystem transcript
    lookup or child spawn.
  - `dsh_transcript.py` projects `session.v4.jsonl.zstd` into
    `<state_dir>/spools/<24hex>.jsonl` so `identify(native_zstd) == identify(spool_path)`
    across incremental turns, verifies native session `cwd` containment inside
    the authorized `project_root`, and bounds cold spools at `<= 64` without
    evicting locked active readers.
  - `mindie_launch.py hook stop` delegates to `run_stop_capture(..., timeout_sec=6.0)`
    while preserving explicit `inactive` revocations (including revocations
    migrated across `admission_path` by `setup.py init`). Off/out-of-scope fast
    paths print `{}` in `< 100ms` with zero SQLite.
- **Summary bridge**:
  - `/summary` binds `127.0.0.1:0`, requires the bearer token, caps the body at
    512 KiB, serves `mindie-summary-request/1` and
    `mindie-distill-request/1`, and rejects digest-invalid input or mid-flight
    route changes (`409`).
  - `summarizeWithLlm` maps DSH `usage` (`inputTokens`, `cacheReadTokens`,
    `cacheWriteTokens`, `outputTokens`) without claiming constrained decoding;
    C6 schemas travel in prompt text.
  - `summary-endpoint.json` is written atomically under `stateDir` and removed
    on `ctx.effect` disposal.
- **Windowless lifecycle & Windows Python resolution**:
  - Every Node `spawn` includes `windowsHide: true`; background children use
    `backgroundSpawnOptions()` (`detached: platform !== 'win32'`).
  - `resolvePythonCommand` rejects `Microsoft\WindowsApps` and 0-byte stubs
    across `dsh.json`, `MINDIE_DSH_PYTHON`, and `PATH`, preferring `uv` when
    unconfigured and never returning bare `'python'` on `win32`.
  - Python children pass `CREATE_NO_WINDOW`; no `DETACHED_PROCESS` or
    `CREATE_NEW_CONSOLE` is referenced.
- **Sharing and privacy tiers**:
  - `setup.py sharing --status|--enable|--local-only|--disable` enforces
    mutually exclusive flags (`exit 2` on conflict) and delegates to
    `mindie_harness.sharing`; `setup.py doctor` delegates to
    `mindie_harness.api.run_host`; `setup.py update` delegates to
    `mindie_harness.runtime.wheelhouse.update_runtime`.
  - Three states remain distinct: local consent written, worker running, and
    external GitHub Issue/PR created.

## 3. Isolated Contract Suite

| Test | Coverage |
| --- | --- |
| `tests/test_dsh_thin_plugin.py` | seed-lock streaming SHA-256/size, cold-seed MCP stdout JSON-RPC purity, `dsh.json` vs legacy precedence, `<100ms` SQLite-free `Stop`, `local-only`/`contribute` parity, explicit `Admission` revocation + `admission_path` migration, 9-domain `setup.py init`, mutually exclusive `sharing` flags, 14 remote tools + zero-`_meta` `knowledge_query`, `product-contract.json` & compatibility registry |
| `tests/test_dsh_host.mjs` | `backgroundSpawnOptions('win32')`, `resolvePythonCommand` (`uv` / `WindowsApps` stub rejection), UTF-8 BOM + canonical/legacy config precedence, omitted-`sessionId` fallback & mid-mount disposal race safety, `/summary` Bearer/512 KiB/route checks, non-blank `turn` guard & windowless spawn flags |
| `tests/test_dsh_transcript.py` | public user/assistant extraction, reasoning/context exclusion, unified `identify(zstd) == identify(spool)` cursor alignment across multi-turn appends, native `cwd` containment, locked cold spool eviction (`<= 64`) |
| `tests/test_legacy_self_upgrade.py` | pre-`0.1.26` (`v0.1.25 -> v0.1.28`) two-phase `setup_candidate` (`--prepare-only` + apply), legacy `dsh/config.json` migration, `npm pack` file list |
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

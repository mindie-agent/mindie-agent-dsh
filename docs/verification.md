# 0.0.1-preview verification record

> **Preview notice**: `v0.0.1` is marked `prerelease` and is **not the formal
> 0.0.1 release**. The preview already contains the rebuilt core capabilities,
> but a Windows model turn and a successful contribution pull request were not
> executed in this environment. The formal tag will be re-cut after those two
> acceptance items are recorded.

Date: 2026-10-17. DSH `0.2.0-rc.2`.

## What was rebuilt

- Native Host plugin: `mindie_entry` (init/status/choose/deactivate,
  sharing/reporting controls), `knowledge_query`, `knowledge_explain`,
  `knowledge_feedback`, `knowledge_sync`.
- DSH v4 Stop capture adapter: concatenated zstd frames, public user/assistant
  text only, incremental byte pages, activation boundary, fork/wrong-task
  checks.
- Bounded Stop helper commits one capture row; no model or network work in the
  hook.
- DSH model-backed summary worker: the Host plugin exposes a loopback endpoint
  backed by `ctx.llm` and the active agent route; the organizer implements the
  exact `mindie-summary-outcome/1` contract.
- Deterministic redaction and the real `mindie-knowledge` service for material
  commit, indexing, feedback and community publication.
- Diagnostics reporting status/enable/disable through `mindie-diagnostics`.
- Native install/update boundary: install from the public tag through the DSH
  plugin manager; no silent self-updater.

## macOS (live model)

Commands and results:

- `/mindie-agent` activated the task; `mindie_entry(init)` returned the saved
  `later`/disabled choice; `mindie_entry(choose disabled)` persisted the choice.
- `knowledge_query` returned 10 result groups (`reme-bm25`) and
  `knowledge_explain` returned 36 blocks for the selected entry.
- `knowledge_feedback` recorded one `up` vote; it remained non-publishable
  while sharing was off.
- A contribution test enabled `maoxx241/mindie-dsh-test` and completed a turn.
  The Stop helper committed capture
  `947a510fb549a26eed287acccab43c7194c2b8cd01d0758e68715560ab82950a`;
  the material was redacted, organized, and indexed by a real DSH model call
  (`summary_status=complete`, `material_batches.status=complete`).
- The outbox then attempted a real GitHub PR to the deliberately nonexistent
  test repository and recorded a clear 404 failure. It did **not** report
  success and did not create a PR. A successful PR against a real repository
  remains unverified.
- The first capture attempt exposed a real bug: the helper passed the adapter
  config instead of the engine config (`stage=unavailable`, `reason=configuration`).
  It was fixed and the next turn produced `accepted-runtime` and a durable
  capture. The first summary attempt also exposed a model-schema mismatch
  (`block_indexes`); the worker now accepts only the two unambiguous aliases
  and states the exact `blocks` shape. The next batch completed.
- Reporting status/enable/disable was exercised through `mindie-diagnostics`;
  the test ended with reporting disabled.

## 4090 WSL (Ubuntu 24.04)

- DSH `0.2.0-rc.2`, Node 22.22.1, Python 3.11.15, SQLite 3.50.4.
- Setup pinned `mindie-knowledge 0.11.0`, `remote-dev 0.9.5`,
  `mindie-diagnostics 0.4.1`; knowledge feed synced.
- Transcript unit tests: 5/5 passed.
- `tests/live_capture_smoke.py` passed: public-only extraction, secret and IPv4
  redaction, service material commit (`status=organized`).
- The bundle booted in a fresh profile; the Host plugin registered the summary
  endpoint and the official `remote_dev.mcp.server` child.
- Not run: a model turn, because the test profile had no model credential.

## Lenovo native Windows

- DSH `0.2.0-rc.2`, Node 24.19.0, Python 3.11.16, SQLite 3.53.1.
- Setup pinned the same three components; the real knowledge service started
  through the Windows Task Scheduler host.
- `tests/live_capture_smoke.py` passed: public-only extraction, secret and IPv4
  redaction, service material commit (`status=organized`).
- The bundle booted in a fresh profile; the summary endpoint listened on
  loopback and the official `remote_dev.mcp.server` child ran.
- A model turn failed before any tool call with `MISSING_CREDENTIAL` for
  `deepseek-official`; no API key was present in the test profile. This is an
  environment credential gap, not a code path. The Windows feed sync also
  failed because the machine's git proxy pointed at a different port than the
  running proxy; the setup, capture, service and plugin boot are still verified.
- No claim is made that a Windows model query/summary succeeded.

## Known limits

- A successful contribution pull request (as opposed to an explicit GitHub
  failure) has not been executed; the test target did not exist.
- Windows/WSL model turns were not run because the test profiles had no model
  credential. Capture, redaction, service, plugin activation and MCP boot were
  run on both.
- The preview release is not the formal 0.0.1. The tag will be re-cut after the
  two missing acceptance items above are recorded.

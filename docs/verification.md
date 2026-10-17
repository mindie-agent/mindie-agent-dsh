# 0.0.1 verification record

Date: 2026-10-17. Host: DeepSeek Harness `0.2.0-rc.2`.

`mindie-agent-dsh` 0.0.1 is the parity rebuild after the read-only preview.
This record keeps implementation status and real acceptance evidence separate.

## Capabilities

- Native `/mindie-agent` Host entry: `mindie_entry` init/status/choose/
  deactivate, sharing and reporting controls; the first undecided activation
  asks once, then persists `contribute` or `disabled`.
- DSH v4 Stop adapter: concatenated zstd frames, public user/assistant text
  only, incremental byte pages, activation boundary and fork/wrong-task checks.
- Deterministic redaction (credential/IPv4 rules), real `mindie-knowledge`
  service, background DSH-model summary indexing and community publication.
- Contribution: default upstream `mindie-agent/knowledge-vllm-ascend`,
  automatic fork resolution/creation under the public account, PR from the
  fork, explicit unknown/failed outcomes.
- `knowledge_query`/`explain`/`feedback`/`sync`, diagnostics reporting
  status/enable/disable, native DSH plugin-manager install boundary.

## macOS (live model)

- `/mindie-agent` activated; `mindie_entry(init)` reported the saved choice;
  `choose disabled` and later `choose contribute` both persisted correctly.
- `knowledge_query` returned 10 groups (`reme-bm25`); `knowledge_explain`
  returned 36 blocks; `knowledge_feedback` stored an `up` vote (non-publishable
  while sharing was off).
- Stop capture `947a510fb549a26eed287acccab43c7194c2b8cd01d0758e68715560ab82950a`
  produced a redacted, organized material package. The DSH model summary
  completed (`summary_status=complete`, batch `complete`) and the entry was
  indexed.
- A contribution test opened/updated PR #51 in
  `mindie-agent/knowledge-vllm-ascend`; the repository automation merged it.
  This verifies the normal accept-at-init → auto-fork → publish path. The PR
  contains a test capture and is now part of the canonical corpus; that test
  content is called out here rather than hidden.
- Diagnostics reporting status/enable/disable was exercised through
  `mindie-diagnostics`; reporting was returned to disabled.
- Bugs found and fixed during acceptance: the Stop helper initially passed the
  adapter config instead of the engine config (`stage=unavailable`); the model
  initially returned `block_indexes`/string navigation (now only unambiguous
  aliases are accepted and the exact `blocks` shape is stated); a stopped
  service could leave a stale connection file (now probed once and discarded
  only if refused); Windows stdout used locale encoding (now ASCII-safe).

## 4090 WSL (Ubuntu 24.04)

- DSH `0.2.0-rc.2`, Node 22.22.1, Python 3.11.15, SQLite 3.50.4.
- Setup pinned `mindie-knowledge 0.11.0`, `remote-dev 0.9.5` and
  `mindie-diagnostics 0.4.1`; the knowledge feed synced.
- Transcript unit tests: 5/5. `tests/live_capture_smoke.py`: public-only
  extraction, secret/IPv4 redaction and service material commit.
- A real model turn called `mindie_entry(init)`; the Stop capture was accepted,
  the material was organized, and the DSH model summary completed
  (`summary_attempts={'complete': 1}`, draft entry indexed).
- The plugin booted in a fresh profile; summary endpoint listened and the
  official `remote_dev.mcp.server` child ran.

## Lenovo native Windows

- DSH `0.2.0-rc.2`, Node 24.19.0, Python 3.11.16, SQLite 3.53.1.
- Setup pinned the same components; the real knowledge service started through
  the Windows Task Scheduler host.
- Transcript unit tests and `tests/live_capture_smoke.py` passed: public-only
  extraction, redaction and service material commit.
- A real model turn called `mindie_entry(init)`; Stop capture
  `a4708c895355b7528c55a6b7b945c05e1c3714e1a808ffefcd4169a83171df12`
  was accepted, organized and indexed after the Windows ASCII-output fix
  (`summary_attempts={'complete': 1, 'outcome_unknown': 1}`; the first attempt
  was the encoding bug above, the retry completed).
- The plugin booted in a fresh profile; the summary endpoint listened and the
  official `remote_dev.mcp.server` child ran.
- Windows feed sync remains unavailable because the machine's global git proxy
  points at a different port than the running proxy. Capture, redaction,
  service, plugin activation, MCP and model/summary are independently verified;
  no feed-sync success is claimed.

## Known limits

- The successful contribution was a test capture and its PR (#51) was merged
  into the canonical knowledge repository by automation. No attempt was made to
  remove it from the public feed.
- Windows knowledge feed sync is blocked by that machine's local proxy
  configuration; knowledge retrieval there depends on a successful sync.
- The automatic installer/updater is the DSH plugin manager plus `scripts/setup.py`;
  no silent self-updater is shipped in 0.0.1.

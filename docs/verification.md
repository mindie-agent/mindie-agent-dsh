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
- Diagnostics: a sidecar failure recorded local incident
  `77cecb168d3642c5ba4183cd3679bc6c`; enabling reporting ran
  `mindie_diagnostics.cli reporting ensure` and confirmed a healthy reporting
  worker, then disabled it. A rejected Stop envelope recorded capture incident
  `acc50f110f4245bc8e1c3b164e9709cd`. Fault text is not uploaded unless the
  shared reporting choice is enabled.
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

## Benchmark harness (0.0.2)

`mindie-bench` PR #2 was merged into
`mindie-agent/mindie-bench` and treats DSH as a native L1/L2 G0-G2 harness.
The trial profile is a fresh owned headless profile; the model credential stays
in the benchmark gateway.

- G0 `cpu-rope-rotary-dim-64` L2: `completed_pass`, 11 turns, native
  bash/read/glob tools, real verifier.
- G1: `completed_pass`; remote-dev MCP started and passed preflight; the CPU
  task was solved locally (0 remote calls).
- G2: `completed_pass`; 1 knowledge query, 1 explain and a skill call observed
  with the real knowledge relay and MindIE skill.
- G3 native capture is explicitly unsupported for DSH; the benchmark returns a
  clear error rather than silently degrading.
- Benchmark full suite local: 804 passed, 16 environment-skipped. GitHub CI
  baseline fails on bwrap/runtime-helper sandbox checks; see the PR note.

## Silent auto-update

- The seed `host.mjs` launcher mounted `host3.mjs` as a child Cordis plugin and
  served the summary endpoint.
- A synthetic formal release `v0.0.2` (same code, version-bumped package) was
  staged from a local GitHub-release-shaped API. `SHA256SUMS` was verified, the
  candidate was extracted to `update/generations/v0.0.2/package`, and the
  updater switched `update/current.json`.
- The launcher detected the pointer within one poll, disposed the seed child,
  mounted the generation child, and the summary endpoint token changed while
  the DSH process id stayed the same. The profile dependency remained the
  original package; no DSH restart or bundle replacement occurred.
- Removing the pointer swapped the launcher back to the seed over the same live
  process. `needs_host_reload` stayed false.
- A no-update check against the real GitHub Releases wrote `up-to-date`; the
  updater is started 30 seconds after Host apply, then at the configured
  interval, and defers while an Agent is running.
- 4090 WSL and Lenovo Windows: the stable launcher mounted the seed host, a
  no-update check returned `up-to-date` with a successful feed sync, and a
  synthetic generation pointer swapped the child plugin (summary endpoint token
  changed, same DSH pid). Removing the pointer returned the seed. No profile
  dependency change or DSH restart occurred.
- Offline tests: 24 Python tests and the summary-endpoint Node test pass.

## Known limits

- The successful contribution was a test capture and its PR (#51) was merged
  into the canonical knowledge repository by automation. No attempt was made to
  remove it from the public feed.
- Windows knowledge feed sync is blocked by that machine's local proxy
  configuration; knowledge retrieval there depends on a successful sync.
- Auto-update runs while the DSH Host is alive and checks on startup/interval;
  no OS scheduler updates a stopped DSH installation.
- Skills and the initial bundle patch are not regenerated by a generation
  swap; the launcher/host tools are.
- A profile installed from a pre-launcher release needs one reinstall of the
  0.0.1 artifact before live generation swaps can work.

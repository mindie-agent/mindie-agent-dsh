# Knowledge surface details

The `mindie-agent-host` / knowledge MCP surface exposes:
`mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_distill`,
`knowledge_feedback`, and `knowledge_sync`.

## Entry (`mindie_entry`) & Multi-Domain Route Registration

- `op="init"` binds the current `/mindie-agent` task and returns the saved
  unified contribution state (`sharing`) and any required setup step (`next`).
  Passing `mindie_entry(op="init", domain="<domain>")` explicitly activates a
  registered target `domain` under its own existing `consent` and `project_root`
  scope (returning `domain_activation` without replacing the default session
  binding).
- Registering an additional domain Engine/Feed in an existing adapter config
  uses `python -m mindie_knowledge.loop.domain_routes register --adapter-config <path> --domain <domain> --engine-config <path>`,
  which updates `domain_configs` (preserving `default_domain`) and **never**
  creates or widens `consent`. Ordinary reads do not widen authorization.
- `op="choose"` records the unified contribution switch (`choice="contribute"`
  or `choice="disabled"`) in `mindie-consent.json`.
- `op="status"` and `op="reporting-status"` are read-only checks of knowledge
  and diagnostic reporter worker/queue state.
- `op="deactivate"` revokes **only the current session's** capture lease across
  the default domain and all registered `domain_configs` without altering the
  saved profile consent choice or revoking other sessions. If any registered
  domain fails or times out during revocation, `status="partial"` is returned
  while preserving `changed` and per-domain outcomes (`domains[]`), keeping
  partial failure and already-revoked side effects distinct.

## Query (`knowledge_query`)

- `query` is a concise engineering phrase, not a full prompt.
- `domain` is an optional target domain (selecting the default domain or a
  registered `domain_configs` Engine/Feed such as `"vllm-ascend"` or
  `"operators"`; cross-domain references use existing `source_locator` /
  `evidence_refs` links rather than automatic federated retrieval).
- `limit` is 1–20 (the service default is 5).
- `domain_path` is an optional preferred graded `domain_path` prefix (with
  cross-directory fallback).
- `token_budget` (`1..16000`, default `800`) bounds body excerpts and inherited
  rules in estimated tokens (metadata excluded).
- `include_unknown` (default `false`) includes graded units whose declared
  conditions are `condition_unknown`.
- `conditions`, when present, are observed environment facts evaluated against
  each unit's **exact declared condition keys** (such as `soc`, `cann`,
  `framework`, `workload_scope`, `dtype`, `input_shape`, or
  `workload_case_sha256`; never assume a hardcoded legacy condition-key list)
  with **three-valued `match_status`**:
  - `matched`: all declared premises on the unit (and any included inherited
    parent) are satisfied by the observed facts. An absent requirement (or
    `"any"`) is unconstrained (`matched`), not unknown.
  - `condition_unknown`: either a declared `required` premise on the unit or the
    caller's `observed` fact is `"unknown"`, missing/not supplied, or has an
    incomparable format, and `unknown == unknown` never matches (historical
    `required: "unknown"` is never backfilled by later machine versions); verify
    whichever fact is missing or unknown (e.g., source `repository`/`revision`
    for an `L2` source SOP, or runtime environment for an NPU rule), and
    **never** treat `condition_unknown` as an automatic match.
  - `mismatched`: explicit conflict (e.g., A2 `910B` vs. A3 `910C` dual-Die
    topology); excluded from results.
- `continuation` is an opaque token for legacy citation pages. Pass it without
  `query` to fetch the next related page. A changed corpus rejects a stale
  continuation.
- **Graded vs. Legacy Response Shape**:
  - When a graded corpus is installed (`retrieval: "graded-bm25"`,
    `page: "graded_units"`), `knowledge_query` returns `results[]` where each
    item contains `id` (stable `unit-...` ID),
    `ref = "mindie://<domain>/units/<id>@<revision>"` (positive integer
    semantic revision), `unit_ref = "mindie://<domain>/units/<id>"` (without
    revision), `feedback_ref = ref` (the same revision URI for graded units),
    `revision`, `match_status`
    (`matched` | `mismatched` | `condition_unknown`), `missing_conditions`,
    `tier`, `category`, `evidence_strength`, `domain_path`, `conditions`,
    `provenance`, `excerpt`, `inherited_rules` (each with its own `id` and
    integer `revision`), `inheritance_excluded`, and `route`. A `route` of
    `foundation_baseline` identifies a matched `_common` `L1` foundation unit
    mounted by the observed conditions.
  - When reading legacy task/block entries, results contain grouped matches with
    task/block `ref`, `block_summary`, `conditions`, `navigation`, `entry_id`,
    and optional `related` excerpts. Do not blend legacy block navigation fields
    into the `graded_units` response shape.

## Explain (`knowledge_explain`)

- Pass the actual returned `ref` (`mindie://<domain>/units/<id>@<revision>`,
  a positive integer revision) or `unit_ref` for a graded unit, or a task/block
  `ref` for legacy material—routed by the `mindie://<domain>/...` URI authority;
  **never** pass a bare `id` or rewrite the returned revision URI.
- **Graded unit (`kind: "graded_unit"`)**: returns `ref`, `unit_ref`,
  `feedback_ref` (equal to `ref`), `id`, `revision`, `frontmatter`, `content`,
  and `inherits_from_closure`. It does **not** return `first_block_ref` /
  `previous_block_ref` / `next_block_ref` block navigation. Record `id`
  (`unit_id`) and integer `revision` when citing a unit in `PROGRESS.md` or
  `experiment-ledger.jsonl`.
- **Legacy task or block ref**: a task ref returns the navigation outline and
  `first_block_ref`; a block ref returns that immutable block, its content hash,
  and `previous_block_ref` / `next_block_ref`.
- Reading a reference does not establish applicability; compare its `conditions`
  with observed task facts.

## Layered execution loops (`L1`–`L4`)

Functional `tier` and `evidence_strength` are independent: an `L1` axiom can be
`hardware_spec`, while an `L3` workflow can be `single_observation`. Execute the
layers in order and keep the evidence honest. A verified milestone is
`task_state: "verified_milestone"` (or `"completed"`); `"in_flight"` is a
zero-call no-op for distillation.

### L1 + L2 Foundation Baseline

- A query with observed `conditions` mounts the shared `_common` baseline
  alongside the lexical matches: `L1` hardware/runtime axioms and measurement
  gates (`category: axiom` or `measurement`) plus `L2` bottleneck/triage SOPs
  (`category: sop`).
- Consume `inherited_rules` before the first experiment. If a result carries
  `route: 'foundation_baseline'`, execute that baseline path first and record
  the outcome. Each inherited rule must independently match; an omitted
  `inheritance_excluded` entry is not permission to ignore its premise.
- `L1` constrains the environment and the measurement (supported SOC,
  CANN/framework pairing, profiler/clock discipline, validity gates). `L2`
  provides the bottleneck/triage procedure. `condition_unknown` is not a match:
  verify the missing fact and query again.

### L3 Hermes Mode (Heuristic Convergence & Skill Self-Evolution)

- Run `L3` staged convergence workflows (`category: workflow`) on top of the
  Foundation baseline: decompose the goal into ordered probes, change one
  factor at a time, and record each structured probe (hypothesis, factors,
  observed metric, `run_id`/`job_id`, receipt path, judgment) in
  `experiment-ledger.jsonl`.
- At verified milestones (`task_state="verified_milestone"` with
  `outcome: "verified"` evidence, or `task_state="completed"`), call
  `knowledge_distill(inputs, task_state=..., domain=...)`. Only caller-selected, already-authorized evidence is eligible;
  `reproduced_empirical` requires repeated `verified` evidence and `L4`
  requires a verified `mindie-bench-ablation/1` receipt. Distillation is a
  local C6 admission, never an automatic external publication.
- After actual use, call `knowledge_feedback` with the returned
  `feedback_ref`: for a graded unit it is the single-line
  `mindie://<domain>/units/<id>@<revision>` URI, and the vote binds `unit_id`
  plus integer `revision` to close the P3 reuse flywheel. Feedback is optional.

### L4 RSI Mode (Recursive Self-Improvement De-biased Exploration)

- Escalate from `L3` only after 2 stalled routine attempts (no metric movement,
  repeated null result, or the same bottleneck). Strip the `L3` routine
  workflow's inherited ordering and confirmation bias.
- Keep only `Goal` + `L1` foundation + `L2` bottleneck data + `L4` mutators
  (`category: mutator`) + the `experiment-ledger.jsonl` `conditional_avoid`
  set as negative constraints. A familiar `L3` assumption is not evidence.
- Execute parallel CPU compile (`npu_count=0`) and serial, exclusive NPU timing
  (`devices=[0]`) under a `coordinator` lease. Physical-NPU measurements never
  overlap; verify the environment fingerprint before and after.
- Gate 1–4:
  1. `OracleFreezer` freezes the oracle/reference output and baseline `run_id`
     before any mutation.
  2. `MeasurementGuard` requires exclusive `devices=[0]`, a fixed measurement
     window, a clean environment fingerprint, and a valid measurement;
     `measurement_invalid` is not evidence.
  3. `AblationGate` requires a baseline-bound verified final-combination
     ablation receipt; LOO pruning stays a candidate until
     `pruned_combination_verified` is true.
  4. `Distill/Refute` distills only verified evidence at
     `task_state: "completed"`; append `supported` or `refuted` with
     `conditional_avoid` to `experiment-ledger.jsonl` and update `PROGRESS.md`.
- Refuted mutations are retained as condition-scoped negative knowledge; never
  relabel them as supported or silently replay them.

## Distill (`knowledge_distill`) & Paired-Runtime Boundary

- `knowledge_distill(inputs, task_state, domain=...)` distills caller-selected,
  already-authorized evidence into local C5 graded units for the default or
  explicitly activated target `domain` using that domain's active
  `/mindie-agent` capture lease and configured native organizer (`SummaryLedger`).
- `inputs`: non-empty list of selected evidence items, each requiring `kind`
  (`experiment_ledger` | `run_manifest` | `authorized_transcript` |
  `ecosystem_repo` | `hardware_spec` | `ablation_receipt`), `outcome`
  (`observed` | `verified` | `compile_failed` | `infra_failed` |
  `measurement_invalid` | `in_flight`), `source_locator`, `content`,
  `evidence_refs`, and optional `repetitions` (`>= 1`).
- `task_state`: `"in_flight"` | `"verified_milestone"` | `"completed"`. Use
  `verified_milestone` or `completed`: `observed` as well as
  fault/environment/build evidence (`compile_failed`, `infra_failed`,
  `measurement_invalid`) can be distilled into `single_observation` or
  `hypothesis` (preserving its true `evidence_strength` without elevating
  unverified fault/build evidence to `hardware_spec` or `reproduced_empirical`;
  `hardware_spec` specifically requires a `hardware_spec` source,
  `reproduced_empirical` specifically requires `outcome == "verified"` with
  `repetitions >= 2`, and `L4` specifically requires a verified
  `mindie-bench-ablation/1` receipt). Only `task_state="in_flight"` (or
  all-`in_flight` / empty inputs) is a deterministic zero-call `noop`, and an
  ordinary host `Stop` event is **not** task completion. Local
  receipt/application failures preserve a completed native model result
  (`operation_status="local_receipt_failed"` or `"local_application_failed"`,
  `local_error`) without replaying the paid call.
- **Release Boundary**: `knowledge_distill`, multi-domain routing, and graded
  `knowledge_query` options (`domain_path`, `token_budget`, `include_unknown`)
  depend on the paired `mindie-knowledge 0.13.0` runtime. Graded Feeds use the
  single-line `ID@revision` reference contract on each content repository's
  `main` branch, with independent Feed sync and revision-bound unit feedback
  (`feedback_ref` / `/units/<id>@<revision>`). The selected feed revision and
  installed runtime identity are separate facts; use their own receipts to
  verify them.

## Feedback (`knowledge_feedback`)

- Records an optional `up` or `down` usefulness vote by copying the actual
  returned `feedback_ref` (`mindie://<domain>/units/<id>@<revision>` for a
  graded unit, where `ref` and `feedback_ref` share the same revision URI value,
  and the vote is keyed by `unit_id` plus integer `revision`; or the legacy
  `feedback_ref` for legacy material; never mix legacy field roles and never
  pass a bare `id`) after actual use in the current task, routed by the
  `mindie://<domain>/...` URI authority. Never required at task completion.

## Sync (`knowledge_sync`)

- Model-free and network-bound. It fetches the configured public feed
  (`mindie-agent/knowledge-vllm-ascend`, `main`), validates the complete task
  packages before switching the installed snapshot, and reports one receipt per
  feed.
- Call once only when the corpus is unavailable/stale or the user asks for the
  latest knowledge. Do not poll it.
- A failed sync does not corrupt the previous installed snapshot; results are
  reported honestly as unavailable/stale.

## Service

The tools talk to the local `mindie-knowledge` (`0.13.0`) loopback service. It
is started on demand and can be inspected or stopped with:

```sh
"$MINDIE_DSH_DATA_HOME"/venv/bin/python -m mindie_knowledge.loop.cli status \
  --config <engine-config>
"$MINDIE_DSH_DATA_HOME"/venv/bin/python -m mindie_knowledge.loop.cli stop \
  --config <engine-config>
```

The setup summary prints both absolute paths. The service binds loopback only
and owns its own consumer lock; never start a second copy manually.

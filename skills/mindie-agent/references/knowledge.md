# Knowledge surface details

The `mindie-agent-host` / knowledge MCP surface exposes:
`mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_distill`,
`knowledge_feedback`, and `knowledge_sync`.

## Progressive context and experience flywheel (P0–P3)

| Phase | Adapter surface | Behavior |
| :--- | :--- | :--- |
| P0: unified metering & loading | `knowledge_query`, `knowledge_explain` | Bounded navigation `cards`/`foundation_cards`; batch `refs` closure with shared prerequisites once; whole-response `usage`; exact `known_refs` visibility. |
| P1: experience data | `knowledge_distill`, `knowledge_feedback` | `related_refs` old-knowledge comparison; foreground `proposal` deterministic apply; `discovered`/`loaded`/`applied`/`evaluated` events with idempotent derived counts. |
| P2: contraction & coverage | `knowledge_query`, `knowledge_explain` | Archived/superseded units stay out of ordinary discovery and remain addressable by ref; `include_archived` is an explicit investigation switch. |
| P3: exploration & native context | `knowledge_distill`, `knowledge_feedback` | Goal/evidence axes stay separate; diagnostic value can be reused even when performance does not improve; verified sub-results can return to any tier. |

All new fields are optional. Existing single-`ref` explain, `up`/`down` votes,
and calls that omit these fields keep their previous shape.

## Entry (`mindie_entry`) & Multi-Domain Route Registration

- **Manual `/mindie-agent` Session Gate**: The user must invoke `/mindie-agent` once in the current session before calling `mindie_entry(op="init"|"choose"|"sharing-enable"|"sharing-disable")`, any `knowledge_*` tool (`knowledge_query`, `knowledge_explain`, `knowledge_distill`, `knowledge_feedback`, `knowledge_sync`), or triggering `agent/turn-stopping` background capture. Both the host sidecar (`mindie_sidecar.py`) and the stdio MCP bridge (`knowledge_mcp.py`, which also requires `MINDIE_DSH_SESSION_ID`) enforce `admission.knowledge_lease(session)` (`entry_enabled == 1`).
- `op="init"` reuses the active manual-entry grant (`requireState`, which restores an existing persisted `knowledge_lease` via `resume` if `admission.sqlite3` exists, after `agent/pre-step` activated the session on the `/mindie-agent` gesture), lazily starts the loopback summary server (`ensureSummaryServer`), and returns the saved unified contribution state (`sharing`) and any required setup step (`next`).
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
- `op="status"`, `op="sharing-status"`, and `op="reporting-status"` are factual
  read-only checks (`{kind: "status", activated: Boolean(result.lease?.enabled), ...result}`)
  available in any session without requiring `/mindie-agent`, starting the
  summary server, or emitting onboarding views.
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
- `token_budget` (`1..16000`, default `800`) bounds the graded body/navigation
  estimate; the response also contains a whole-response `usage`
  (`token_budget`, `body_token_estimate`, `estimated_tokens`, `estimator`,
  `has_more`, `excluded_budget`). Treat the estimator as an approximation, not
  measured model usage.
- `include_unknown` (default `false`) includes graded units whose declared
  conditions are `condition_unknown`; these remain `ready_to_apply=false`.
- `include_archived` (default `false`) includes archived or superseded units in
  ordinary discovery. They remain addressable by exact ref without it; use it
  when investigating an old or replaced conclusion, not as a default. The
  excluded count appears under `excluded_conditions.archived_or_superseded`.
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
    `page: "graded_units"`), the response is a discovery envelope:
    `cards[]` is the bounded navigation set, `foundation_cards[]` is the
    deduplicated matched `_common`/foundation premise set, and `results[]`
    retains the compatible detailed rows. Each card contains `id`, `revision`,
    `title`, `tier`, `category`, `evidence_strength`, `status`,
    `superseded_by`, `domain_path`, `match_status`, `ready_to_apply`,
    `missing_conditions`, `inherits_from`, `body_token_estimate`,
    `closure_token_estimate`, and `route`. Prefer the cards for deciding what
    to load; do not expand every `results[]` body.
  - Each `results[]` item contains `id` (stable `unit-...` ID),
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

This is the second step of progressive loading: use the cards from
`knowledge_query`, then load only the selected body closure.

- Pass the actual returned `ref` (`mindie://<domain>/units/<id>@<revision>`,
  a positive integer revision) or `unit_ref` for a graded unit, or a task/block
  `ref` for legacy material—routed by the `mindie://<domain>/...` URI authority;
  **never** pass a bare `id` or rewrite the returned revision URI. A single
  `ref` keeps the historical response contract and top-level compatibility
  fields.
- Pass `refs=[...]` (with optional `conditions`, `known_refs`, and
  `token_budget` 1–16000) for a batch closure. The service computes
  `needed = closure(S) - visible_complete_refs`, emits shared prerequisites once
  in dependency order, and never truncates a complete body to fit the budget.
  `unresolved[]` distinguishes `condition_unknown`, `condition_mismatch`,
  `budget_excluded` (with `required_tokens`), `removed_or_superseded` (with
  `current_revision`/`superseded_by`), and `not_found`. Raise the budget or
  narrow the selected set when a required body is excluded.
- `known_refs` means only exact `id@revision` (or full URI) bodies still fully
  visible in the current context. It suppresses re-sending those bodies. Clear
  it after native compaction: a summary or a leftover ref does not prove the
  body is still visible, and a bare id/old revision never hides the current
  revision.
- `conditions` evaluates applicability for every requested unit and required
  ancestor; a unit with a missing/unknown premise or a mismatch is not
  `ready_to_apply`. Conflicting `mindie://` domain authorities across `refs`
  are rejected, and routing uses `ref` or the first `refs` item.
- **Graded unit (`kind: "graded_unit"`)**: a single read returns `ref`,
  `unit_ref`, `feedback_ref` (equal to `ref`), `id`, `revision`, `frontmatter`,
  `content`, and `inherits_from_closure`. A batch read returns `units[]`,
  `requires[]`, `unresolved[]`, and `usage` metadata. Record `id`
  (`unit_id`) and integer `revision` when citing a unit in `PROGRESS.md` or
  `experiment-ledger.jsonl`.
- **Legacy task or block ref**: a task ref returns the navigation outline and
  `first_block_ref`; a block ref returns that immutable block, its content hash,
  and `previous_block_ref` / `next_block_ref`.
- Reading a reference does not establish applicability; compare its `conditions`
  with observed task facts.

## Diagnosis and exploration

Knowledge tiers describe use, not a mandatory execution order. Apply complete,
condition-matched prerequisites; select L3 branches from observations. Hermes
can revise any tier, and an exploration finding may be L1, L2, L3 or L4.

Read [Diagnostic Reasoning](diagnostic-reasoning.md) when the routine path no
longer explains the evidence, accuracy depends on state/observation, or a
milestone challenges existing knowledge. It covers discriminating experiments,
first divergence, observer controls, sequence reproducers, independent oracles,
and preserving corrections. Keep evidence validity, goal attainment and
reusable information distinct. Repeated no-information attempts prompt review;
new facts are progress even without a performance improvement.

Use `task_state="verified_milestone"` for a finished sub-result in a continuing
task. `in_flight` and all-pending inputs remain no-op; Stop is not completion.
Keep supported/refuted/inconclusive judgments with their scope and source.
Only L4 transformation admission requires the verified final-combination
ablation receipt; diagnostic findings need the evidence appropriate to their claim.

## Distill (`knowledge_distill`) & Paired-Runtime Boundary

- `knowledge_distill(inputs, task_state, domain=..., related_refs=...,
  proposal=...)` distills caller-selected, already-authorized evidence into
  local C5 graded units for the default or explicitly activated target `domain`
  using that domain's active `/mindie-agent` capture lease and configured native
  organizer (`SummaryLedger`).
- `related_refs` is an optional list of existing stable unit IDs or graded refs.
  The organizer receives their compact `related_units` (id, revision, tier,
  category, title, conditions, triggers, body excerpt) as comparison material
  and can propose an incremental revision/merge instead of a duplicate. It is
  lookup-only: existing units are not counted as new experimental evidence.
- `proposal` is an optional `mindie-distill-decision/1` object formed by the
  foreground Agent. It is validated and applied deterministically, returns
  `model_calls=0`, `model_execution="foreground_proposal"`, and never invokes
  the paid organizer. Omit `related_refs`/`proposal` when neither is available.
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
- **Release Boundary**: `knowledge_distill` (`related_refs`/`proposal`), usage
  events, multi-domain routing, and graded `knowledge_query`/`knowledge_explain`
  options (`domain_path`, `token_budget`, `include_unknown`, `include_archived`,
  `refs`, `known_refs`, `conditions`) depend on the paired
  `mindie-knowledge 0.13.0` runtime. Existing single-`ref` explain, `up`/`down`
  votes, and calls that omit the new optional fields remain backward
  compatible. Graded Feeds use the single-line `ID@revision` reference contract
  on each content repository's `main` branch, with independent Feed sync and
  revision-bound unit feedback (`feedback_ref` / `/units/<id>@<revision>`). The
  selected feed revision and installed runtime identity are separate facts; use
  their own receipts to verify them.

## Feedback (`knowledge_feedback`)

- `ref` is the actual returned `feedback_ref`
  (`mindie://<domain>/units/<id>@<revision>` for a graded unit, where `ref` and
  `feedback_ref` share the same revision URI value, and the vote is keyed by
  `unit_id` plus integer `revision`; or the legacy `feedback_ref` for legacy
  material). Never mix legacy field roles and never pass a bare `id`. Routing
  follows the `mindie://<domain>/...` URI authority.
- `rating` (`up`/`down`) is optional. `reason` remains bounded at 1000
  characters.
- One optional four-stage usage event can accompany or replace the vote:
  - `event_type="discovered"` — a query returned the card. Delivery does not
    prove the model read or adopted it.
  - `event_type="loaded"` — the complete body entered visible context. Only the
    host/caller can confirm this; the service does not infer it from delivery.
  - `event_type="applied"` — the decision actually uses the unit; bind
    `run_id` and optional `step_id`.
  - `event_type="evaluated"` — a real result exists; requires a nonempty
    `run_id` and `outcome` `supported`/`refuted`/`inconclusive`. Invalid or
    missing measurements belong to `inconclusive`, not success or refutation.
- Events are idempotent per `(root, unit_id, revision, event_type, run_id)`;
  replayed receipts and repeated polling do not inflate counts. A pure
  `discovered`/`loaded` event needs no rating. Never required at task
  completion.

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

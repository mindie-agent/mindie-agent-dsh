# Remote-dev surface details

`remote-dev` is the published remote development substrate. Its MCP server is
started from the pinned runtime; the tool names are
`mcp__remote-dev__remote_read`, `remote_write`, `remote_edit`,
`remote_multi_edit`, `remote_bash`, `remote_glob`, `remote_grep`, `remote_ls`,
`remote_apply_patch`, `remote_job_status`, `remote_job_tail`,
`remote_job_stop`, `remote_job_stdin`, `remote_artifact_manifest`,
`remote_artifact_pull`, `remote_artifact_push`, `remote_context_snapshot`, and
`remote_probe`.

## Endpoint selection

Every call needs one selector:

- `host` + `port` (plus optional `user`, default `root`), optionally with an
  existing Docker `container` name or ID; or
- `alias` from the user's configured endpoint alias files.

Do not guess. If the user gives a machine but no container, ask only when the
target container is genuinely ambiguous. Reuse the full container ID the tool
returns instead of repeating a name lookup.

## Jobs & Long-Horizon Continuation Protocol

Long commands may yield a job ID. Keep it, poll with `remote_job_status` or
`remote_job_tail`, and stop it with `remote_job_stop`. A job may outlive a lost
MCP connection; inspect its original record before starting another one.

- **Single-Turn `Stop` != Long-Horizon Task Completion**: Ending a single DSH
  turn (`turn-stopping`) only triggers turn-level experience handoff; it never
  implies that an iterative remote NPU benchmark or multi-step optimization task
  has finished.
- **Launch-Time `in_flight_runs` Registration & Recovery-First-Query**:
  `coordinator` assigns `run_id` (`run_id == execution_id`, never replaced) at
  submission (`client.run`). Immediately upon launching a run or job (before
  waiting or ending the turn), atomically update `PROGRESS.md` (`os.replace`) to
  record `{run_id, job_id, slot_id, host_endpoint, manifest_path, started_at}`
  under `in_flight_runs` alongside the task goal, current workspace
  `git_commit` / `git diff`, verified baseline `run_id`, best verified
  candidate `run_id`, key historical `conditional_avoid` refs, and next minimal
  experiment. On resume or after compaction, read `PROGRESS.md`
  `in_flight_runs` first and query the original `run_id` / `job_id`
  (`TaskClient.observe(run_id=...)` / `remote_job_status`) before taking any
  action—**never** resubmit a command while its in-flight `run_id` is unresolved.
- **Single-Writer `experiment-ledger.jsonl` & Sequential Pruning Retain Rule**:
  Append one JSON record per experiment linking `task_id`, `exp_id`, `run_ids`,
  `hypothesis`, `factors`, `factor_roles` (`performance` |
  `correctness_required` | `dependency` | `inseparable_group`), `unit_refs`
  (`unit_id` + integer `revision`), `receipt_refs`, `run_status`,
  `judgment` (`supported`, `refuted`, `inconclusive`), `scope_conditions`, and
  optional `conditional_avoid` / `supersedes_exp_id`. Local receipt/ledger
  errors never overwrite a succeeded remote result. In Contract C4
  (`AblationReceipt`), LOO `delta <= tolerance` only sets
  `disposition: "candidate_for_sequential_pruning"`; consumers **must** treat
  `disposition == "candidate_for_sequential_pruning"` with
  `pruned_combination_verified == false` as **RETAIN**.

## Safety, Physical NPU Timing & Profiler Discipline

- A timeout or lost receipt is `unknown`, not "not executed". Inspect before a
  deliberate repeat.
- Never automatically replay a write, edit, patch, artifact push, or bash
  mutation after an uncertain result.
- **Pre-Compile Parallelism vs. Physical NPU Timing Mutual Exclusion**:
  CPU/cross-compile jobs (`bisheng` / `ascendc` / `triton` compilation) and log
  analysis may run in parallel using per-slot `MINDIE_SLOT_ID` cache paths, but
  any performance benchmark or profiler capture on the same physical NPU cards
  (including dual-Die `Phy-ID` sibling mappings on Ascend A3 910C) must hold an
  exclusive `coordinator` lease and pass pre/post `environment_fingerprint`
  checks.
- **Profiler Explicit Windows (`segment.py`), Scene/Clock Annotations &
  Unverified `msmodeling` Provider**: `python -m mindie_profiler` supports
  **explicit unlabeled time windows** (`--time-window START:END` /
  `--window START:END`), **explicit labeled step windows**
  (`--step-window STEP=START:END` / `--step STEP=START:END` / `--steps-file`),
  and explicit `--full-range` (in `--unit us|ns|ms`). Not every analysis
  requires step labels, and `segment.py` **never** auto-infers real steps from
  kernel order, timestamps, or CSV headers. Scene metadata (`--mode`,
  `--framework`, `--phase`, `--tp`, `--dp`, `--graph-mode`, `--clock-domain`,
  `--input-signature`) uses explicit caller annotations and reports `uncertain`
  otherwise (`scene_detector.py`). Omitting `--clock-domain` on multi-rank DBs
  emits `clock_domain_unknown`, and `compare.py` refuses cross-run timing
  comparison unless `clock_domain` matches. `theoretical_model.py` separates
  `ideal_hardware_lower_bound_ms` (local `roofline-max-v1` single-kernel bound,
  not a whole-network bound), `model_predicted_ms` (only from an injected
  validated external provider with real version, matching protocol, matching
  `input_signature`, and non-empty evidence; otherwise `None` with
  `unmodeled_reason`), and `measured_device_ms`; `probe_msmodeling_adapter`
  reports install state only and never imports/executes `msmodeling`
  (`verified=False`).
- **No `85°C` Throttling Heuristic**: Never infer thermal throttling from a
  static temperature threshold (such as `85°C`, `used_as_throttling_proof=False`);
  if explicit hardware throttle signal bits are unavailable from `npu-smi`,
  report throttling status as explicitly `unknown` / `measurement_limitations`.
- Keep the CPU/source-only checks distinct from actual NPU execution. Record
  the revision, device, command, output, and limit for real runs.
- Do not modify the user's unrelated containers, SSH configuration, or other
  users' live jobs.

# Remote-dev surface details

`remote-dev` is the published remote development substrate, started per session
by `host.mjs` from the pinned runtime with `REMOTE_DEV_HARNESS_PROFILE=dsh`.
The 14 `dsh` tools are:

`remote_read`, `remote_write`, `remote_edit`, `remote_bash`, `remote_glob`,
`remote_grep`, `remote_job_output`, `remote_job_list`, `remote_job_kill`,
`remote_read_image`, `remote_auth`, `remote_probe`, `remote_artifact_pull`,
`remote_artifact_push`.

The companion `mindie-tools` server exposes deterministic component tools,
`mindie_status`, the read-only `mindie_doctor` health check, and the 5
`knowledge_*` tools. Run `mindie_doctor` once when readiness is unclear; report a
failing concrete check once and continue with native tools.

## Endpoint selection & one-step authentication

Every call needs one selector:

- `host` + `port` (plus optional `user`, default `root`), optionally with an
  existing Docker `container` name or ID; or
- `alias` from the user's configured endpoint alias files.

Prefer `remote_auth(action="setup", host="<REAL_IP>", port=22,
user="<REAL_USER>", save_default=True, discover=True)`. `save_default=True`
persists the endpoint for later calls and `discover=True` probes the container.
Terminal mode may use (leading space; `--password` last):

```text
 /abs/path/to/python -m remote_dev auth setup --host <REAL_IP> --port 22 --user <REAL_USER> --save-default --discover --password
```

Do not guess. If the user gives a machine but no container, ask only when the
target container is genuinely ambiguous. Reuse the full container ID the tool
returns instead of repeating a name lookup.

## Runtime privacy tiers

`python scripts/setup.py sharing --status` is a read-only snapshot.
Switch only on the user's explicit choice:

```bash
python scripts/setup.py sharing --enable --project-root <abs>   # contribute
python scripts/setup.py sharing --local-only --project-root <abs>
python scripts/setup.py sharing --disable                       # read-only
```

`contribute` requires a working `gh` only for PR publication; it is optional and
never blocks `local-only`, `read-only`, knowledge retrieval, or `remote-dev`.
`Stop` capture checks the tier and `project_roots` before opening SQLite or
spawning a child.

## Jobs & Long-Horizon Continuation Protocol

Long commands may yield a job ID. Keep it, poll with `remote_job_output` or
`remote_job_list`, and stop it with `remote_job_kill`. A job may outlive a lost
MCP connection; inspect its original record before starting another one.

- **Single-Turn `Stop` != Long-Horizon Task Completion**: Ending a single DSH
  turn (`turn-stopping`) only triggers turn-level experience handoff; it never
  implies that an iterative remote NPU benchmark or multi-step optimization task
  has finished.
- **Launch-Time `in_flight_runs` Tracking & Recovery-First-Query**:
  `coordinator` assigns `run_id` (`run_id == execution_id`, never replaced) at
  submission (`client.run`), and `remote-dev` assigns an authoritative `job_id`.
  Record `{run_id, job_id, slot_id, host_endpoint, manifest_path, started_at}`
  in task-local state before waiting or ending the turn. On resume or after
  compaction, query the original `run_id` / `job_id` first before taking any
  action—**never** resubmit a command while its in-flight `run_id` is unresolved.
- **Sequential Pruning Retain Rule**: Local receipt/ledger errors never overwrite
  a succeeded remote result. Treat a factor whose single-removal `delta <=
  tolerance` as a sequential-pruning candidate only; retain it unless the pruned
  combination has itself been verified.

## Safety, Physical NPU Timing & Profiler Discipline

- A timeout or lost receipt is `unknown`, not "not executed". Inspect before a
  deliberate repeat.
- Never automatically replay a write, edit, patch, artifact push, or bash
  mutation after an uncertain result.
- **Pre-Compile Parallelism vs. Physical NPU Timing Mutual Exclusion**: Only
  confirmed device-free compilation and log analysis may run in parallel using
  per-slot `MINDIE_SLOT_ID` caches. Compiler names do not prove device
  independence; device probing, JIT, and warmup need the existing lease and
  actual allocation. Any benchmark or profiler capture on the same physical NPU
  cards (including dual-Die `Phy-ID` sibling mappings on Ascend A3 910C) must
  hold an exclusive `coordinator` lease or equivalent verified reservation and
  pass pre/post `environment_fingerprint` checks.
- **Profiler Explicit Windows & Scene/Clock Annotations**: `python -m
  mindie_profiler` supports explicit unlabeled windows (`--time-window`),
  explicit labeled step windows (`--step-window` / `--steps-file`), and explicit
  `--full-range`. Scene metadata uses caller annotations and reports `uncertain`
  otherwise. Omitting `--clock-domain` on multi-rank DBs emits
  `clock_domain_unknown`; cross-run timing comparison requires matching clock
  domains. `msmodeling` is reported as install state only and never executed.
- **No `85°C` Throttling Heuristic**: Never infer thermal throttling from a
  static temperature threshold. If explicit hardware throttle signal bits are
  unavailable, report throttling as `unknown`/`measurement_limitations`.
- Keep CPU/source-only checks distinct from live NPU execution, and record the
  revision, device, command, and output for hardware runs.

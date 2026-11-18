---
name: mindie-agent
description: "Ascend NPU, vLLM-Ascend, xLLM, MindIE-SD, AscendC, Triton, and CANN engineering workflow with default-enabled L1-L4 graded knowledge, remote NPU development, profiling, Roofline simulation, CANN fault triage, and cluster lease coordination."
user-invocable: false
---

# MindIE Agent (DeepSeek Harness Runtime Workflow)

## 1. Default-On Tooling & Non-Blocking Task Continuity

- **Ready out of the box**: the `remote-dev` surface (14 `dsh` tools: `remote_read`, `remote_write`, `remote_edit`, `remote_bash`, `remote_glob`, `remote_grep`, `remote_job_output`, `remote_job_list`, `remote_job_kill`, `remote_read_image`, `remote_auth`, `remote_probe`, `remote_artifact_pull`, `remote_artifact_push`) and the `mindie-tools` surface (`mindie_status`, `mindie_doctor`, `diagnostics_status`, `diagnostics_bundle`, `profiler`, `sim`, `coordinator`, `debugger`, `rsi_loop`, `experiment_ledger`, and the 5 `knowledge_*` tools) work in every DSH session and subagent without activation, `request_nonce`, or `context_file`.
- **Non-Blocking Task Continuity Rule**: MindIE tools (`remote-dev`, `coordinator`, `knowledge`, `mindie_doctor`) are accelerators, **never roadblocks**. If any tool fails, report the concrete error once—reconciling any uncertain remote write before repeating it—then continue immediately via native `ssh`/`scp` or task-local scripts. Never stop or ask the user to repair a helper before completing the requested work.
- **One-step NPU connection**: prefer `remote_auth(action="setup", host="<REAL_IP>", port=22, user="<REAL_USER>", save_default=True, discover=True)`; `save_default=True` persists the endpoint and `discover=True` probes the container environment. Reuse the resulting `job_id`/endpoint before resubmitting.

## 2. Code-First Investigation, Knowledge & NPU Discipline

- **Inspect code first**: Read target source files and callers before calling `knowledge_query`; fix and verify directly when the codebase already explains the issue.
- **Two-step `L1–L4` retrieval**:
  1. `knowledge_query`: pass concise technical terms (canonical English APIs/error codes plus bilingual domain keywords), optional `domain`, and observed `conditions` (`token_budget` default `8000`, up to `64000`). Treat `condition_unknown` as unproven and probe missing facts before applying a unit.
  2. `knowledge_explain`: copy exact `mindie://<domain>/units/<id>@<revision>` URIs into `refs=[...]` with observed `conditions`, visible `known_refs=[...]`, and `token_budget` (default `16000`).
- **Privacy tiers**: `contribute` enables sanitized public experience/PR and fault reporting; `local-only` captures locally; `read-only` disables capture and outgoing sends. Switch with `python scripts/setup.py sharing --enable | --local-only | --disable [--project-root <abs>]` and inspect with `--status`. Change tiers only on the user's explicit choice.
- **Feedback, distillation & NPU leases**: Record `knowledge_feedback` on `feedback_ref` when useful, call `knowledge_sync` once only if the corpus is missing/stale or requested, and call `knowledge_distill` at `task_state="verified_milestone"` or `"completed"` (`reproduced_empirical` requires repeated verified runs; `L4` requires a verified leave-one-out ablation receipt). Acquire or verify a `coordinator` lease before running workloads on physical NPUs, inspect existing `execution_id`/`job_id` handles before resubmitting, and pass explicit `--time-window`, `--step-window`, or `--full-range` plus `--clock-domain` to `profiler`.
- **De-biased exploration**: When repeated `L3` repairs stall on a structural bottleneck, query `L4` structural mutators (`mode="explore"`) and optionally seed a clean subagent with `exploration_context.subagent_fact_pack.prompt_brief`.
- **Doctor before blaming the environment**: run the read-only `mindie_doctor` once for runtime/config/permission/remote-reachability facts; report a failing concrete check once and continue with native tools.

## 3. Progressive References (Load Only When Needed)

- [Knowledge](references/knowledge.md) · [Remote-Dev](references/remote-dev.md) · [Diagnostic Reasoning](references/diagnostic-reasoning.md) · [Limitations](references/limitations.md)

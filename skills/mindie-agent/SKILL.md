---
name: mindie-agent
description: "Ascend NPU, vLLM-Ascend, xLLM, MindIE-SD, and AscendC/Triton engineering workflow with default-enabled L1-L4 graded knowledge, remote NPU development, profiling, Roofline simulation, CANN fault triage, and cluster lease coordination."
---

# MindIE Agent (DeepSeek Harness Runtime Workflow)

## 1. Default-Enabled Tooling & Task Continuity

- **Ready out of the box**: `remote-dev` (`mcp__remote-dev__remote_*`), `mindie-tools` (`mindie_status`, `diagnostics_status`, `diagnostics_bundle`, `coordinator`, `debugger`, `profiler`, `sim`), and Host knowledge tools (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`, `mindie_entry`) work by default in every DSH session, subagent, and workspace-free run without session activation or `context_file`.
- **Keep the user's task moving**: Knowledge, diagnostics, and local ledgers are optional helpers. If any helper fails, report that specific error once and continue with deterministic MCP tools or authorized host-native tools; reconcile any `unknown` remote write before repeating it.

## 2. Advisory Knowledge & NPU Execution Discipline

- **Inspect code first**: Read the relevant source files and callers before calling `knowledge_query`; fix and verify directly when the codebase already explains the issue.
- **Two-step `L1–L4` retrieval**:
  1. `knowledge_query`: pass concise bilingual terms (canonical English APIs/error codes alongside domain keywords), optional `domain`, and observed `conditions` (`token_budget` default `8000`, up to `64000`). Treat `condition_unknown` as unproven and probe missing facts before applying a unit.
  2. `knowledge_explain`: copy exact `mindie://<domain>/units/<id>@<revision>` URIs into `refs=[...]` with observed `conditions`, visible `known_refs=[...]`, and `token_budget` (default `16000`).
- **Feedback, distillation & NPU leases**: Record `knowledge_feedback` on `feedback_ref` when useful, run `knowledge_sync` once only if the corpus is missing/stale or requested, and call `knowledge_distill` at `task_state="verified_milestone"` or `"completed"` (`reproduced_empirical` requires repeated verified runs; `L4` requires a verified leave-one-out ablation receipt). Acquire or verify a `coordinator` lease before running workloads on physical NPUs, inspect live `coordinator` (`execution_id`) and `remote-dev` (`job_id`) handles before resubmitting, and pass explicit `--time-window`, `--step-window`, or `--full-range` plus `--clock-domain` to `profiler`.
- **De-biased exploration**: When repeated `L3` repairs stall on a structural bottleneck, query `L4` structural mutators (`mode="explore"`) and optionally delegate a clean subagent seeded with `exploration_context.subagent_fact_pack.prompt_brief`.

## 3. Progressive References (Load Only When Needed)

- Detailed knowledge, remote-dev & diagnostic reasoning references: [Knowledge](references/knowledge.md), [Remote-Dev](references/remote-dev.md), [Diagnostic Reasoning](references/diagnostic-reasoning.md), [Limitations](references/limitations.md)

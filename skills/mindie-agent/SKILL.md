---
name: mindie-agent
description: Manually enable Ascend knowledge, domain workflows, feedback, distillation and background experience capture for the current DeepSeek Harness session. Configured deterministic tools remain available without this entry.
whenToUse: Only when the user explicitly types /mindie-agent for an Ascend NPU, vLLM-Ascend, AscendC, Triton, NPU testing, tuning, or troubleshooting task.
disable-model-invocation: true
---

# MindIE Agent (DeepSeek Harness Unified Entry)

## 1. Core Operating Stance

- **Keep the user's task moving**: Knowledge, diagnostics, and local ledgers are optional helpers. If any helper fails, report that specific error once and continue inside the user's native DSH session with deterministic MCP tools or authorized host-native tools. Reconcile any `unknown` remote write before repeating it.
- **Zero-gate deterministic tools**: `remote-dev` (`mcp__remote-dev__remote_*`) and `mindie-tools` (`mindie_status`, `diagnostics_status`, `diagnostics_bundle`, `coordinator`, `debugger`, `profiler`, `sim`) work in every DSH session, subagent, and workspace-free run without `/mindie-agent`, session hooks, or `context_file`; `coordinator` runs are addressed directly by `execution_id`. Obtain a `coordinator` lease (or verify an existing reservation) before running workloads on physical NPUs, pausing only that device step if ownership cannot be confirmed.
- **Explicit `/mindie-agent` knowledge gate**: Load this skill only when the user explicitly types `/mindie-agent`. Call `mindie_entry(op="init")` once (with optional `domain="<domain>"`) to initialize the current session's lease; new chats, forks, and subagents require their own manual entry.

## 2. Advisory Knowledge & Domain Tooling

Treat knowledge strictly as reference material, never as rigid instructions:

- **Inspect code first**: Read the relevant code and callers before calling `knowledge_query`. If the codebase already explains the bug or requirement, fix and verify it directly.
- **Two-step retrieval (`L1–L4` graded units)**:
  1. `knowledge_query`: pass a concise bilingual query (include canonical English APIs/identifiers alongside Chinese domain terms), optional `domain`, and observed `conditions` (`token_budget` default `8000`, up to `64000`). Units separate `tier` (`L1–L4`) from `evidence_strength`; treat `condition_unknown` as unproven and probe missing facts before applying a unit.
  2. `knowledge_explain`: copy exact `mindie://<domain>/units/<id>@<revision>` URIs into `refs=[...]`, passing observed `conditions`, currently visible `known_refs=[...]`, and `token_budget` (default `16000`).
- **Optional feedback, sync & distillation**: Record `knowledge_feedback` on `feedback_ref` when useful, run `knowledge_sync` once only if the local corpus is missing/stale or requested, and call `knowledge_distill` at `task_state="verified_milestone"` or `"completed"` (`reproduced_empirical` requires repeated verified runs; `L4` requires a verified ablation receipt).
- **Long-horizon runs & de-biased exploration**:
  - `coordinator` (`execution_id`) and `remote-dev` (`job_id`) own authoritative job/lease state—inspect existing handles before resubmitting after an interruption or compaction, and remember a single-turn `Stop` event never marks an ongoing multi-turn task complete. Pass explicit `--time-window`, `--step-window`, or `--full-range` and `--clock-domain` to `mindie_profiler`.
  - For multi-round kernel/serving tuning, track hypotheses and verified receipts cleanly in task-local notes. When repeated `L3` repairs plateau on a structural bottleneck, query `L4` structural mutators (`mode="explore"`) and optionally delegate a clean subagent seeded with `exploration_context.subagent_fact_pack.prompt_brief`, verifying multi-factor changes with leave-one-out ablations on the operator's real interface.

## 3. Progressive References (Load Only When Needed)

- Detailed knowledge, remote-dev & diagnostic reasoning references: [Knowledge](references/knowledge.md), [Remote-Dev](references/remote-dev.md), [Diagnostic Reasoning](references/diagnostic-reasoning.md), [Limitations](references/limitations.md)

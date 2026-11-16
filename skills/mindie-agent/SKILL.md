---
name: mindie-agent
description: Manually enable Ascend knowledge, domain workflows, feedback, distillation and background experience capture for the current DeepSeek Harness session. Configured deterministic tools remain available without this entry.
whenToUse: Only when the user explicitly types /mindie-agent for an Ascend NPU, vLLM-Ascend, AscendC, Triton, NPU testing, tuning, or troubleshooting task.
disable-model-invocation: true
---

# MindIE Agent (DeepSeek Harness Unified Entry)

## 1. Core Operating Stance

- **Keep the user's task moving**: Knowledge, onboarding, diagnostics, and local ledgers are optional helpers. If any helper fails, report that specific error once and continue inside the user's native DSH session with deterministic MCP tools or authorized host-native tools. Reconcile any `unknown` remote write before repeating it.
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
  - For multi-round kernel/serving tuning, you may optionally track hypotheses via `mindie_knowledge.graded.experiment_ledger`. When repeated `L3` repairs plateau on a structural bottleneck, query `L4` structural mutators (or let `assess_stagnation` promote exploration) and optionally delegate a clean subagent seeded with `exploration_context.subagent_fact_pack.prompt_brief`, verifying multi-factor changes with leave-one-out ablations on the operator's real interface.

## 3. Non-Intrusive First-Use Onboarding

- Defer optional onboarding when the user is focused on an active task; reuse saved settings (`contribute` with `choice_revision`, `disabled`, `read-only`, `later`) without prompting again.
- During initial setup or when `mindie_entry(op="init")` / `setup.py sharing --status` reports `needs_choice` / `needs_onboarding_prompt: true`, briefly reference [the user notice](references/user-notice.md) and ask once in the user's language:
  1. **Workspace**: Keep the current directory when already inside a repository checkout, or initialize/select `vllm-ascend-workspace` when starting fresh;
  2. **Community Sharing & Sanitized Fault Reporting**: Enable (`mindie_entry(op="choose", choice="contribute", account="<github-account>")` or `setup.py sharing --enable --project-root "$WORKSPACE_ROOT"`) vs. Keep Local-Only (`choice="disabled"` / `sharing --disable`);
  3. **Optional GitHub Star**: Ask lightly whether to star `mindie-agent/mindie-agent` (only run `gh api --hostname github.com --silent -X PUT /user/starred/mindie-agent/mindie-agent` on an explicit yes).
- Report local consent state, background reporter health (`reporting-status`), and external GitHub Issue/PR creation separately.

## 4. Progressive References (Load Only When Needed)

- GitHub sign-in & enterprise network token login: [GitHub Sign-In](references/github-authentication.md)
- Workspace setup details: [Workspace Setup](references/workspace-setup.md)
- Detailed knowledge, remote-dev & diagnostic reasoning references: [Knowledge](references/knowledge.md), [Remote-Dev](references/remote-dev.md), [Diagnostic Reasoning](references/diagnostic-reasoning.md), [Limitations](references/limitations.md)

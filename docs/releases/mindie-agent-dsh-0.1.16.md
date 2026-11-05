# mindie-agent-dsh 0.1.16

`mindie-agent-dsh 0.1.16` refines `skills/mindie-agent/SKILL.md` so RSI stagnation automatically promotes `requested_mode="reuse" -> effective_mode="explore"` (`auto_switched_from_reuse=True`) after 3 distinct refuted candidate repair hashes (`candidate_sha256`) in `logs/experiment-ledger.jsonl` without manual `mode="explore"` calls, enforces strict workspace boundary discipline during evaluation, and upgrades the pinned `mindie-knowledge` runtime component to `0.13.13` (`da64fe111d878f44b7efec6af6ca46d3f5887be6`).

## What's New (Human-Readable Summary)

- **Automatic `reuse -> explore` Promotion Workflow (`skills/mindie-agent/SKILL.md`)**: Guides the agent to record each distinct candidate repair (`candidate_sha256`) and test outcome in `logs/experiment-ledger.jsonl` and re-query with `mode="reuse"` (`caller_initiated_explore=False`), letting `mindie-knowledge` automatically promote to `effective_mode="explore"` after 3 distinct refuted candidate hashes.
- **Upgraded Runtime & Component Contracts**:
  - Locked runtime (`pyproject.toml`, `runtime-requirements.txt`, `uv.lock`, `harness-compatibility.json`): `mindie-knowledge 0.13.13` (`da64fe111d878f44b7efec6af6ca46d3f5887be6`), `remote-dev 0.11.5` (`131b24533479e9cefc572144adccdcb345f70846`), and `mindie-diagnostics 0.5.6` (`9b331956b00737dd6d8c956da3416316f2de1ddf`).
- **Workspace Boundary & Anti-Oracle-Reverse-Engineering Discipline (`skills/mindie-agent/SKILL.md`)**: Explicitly forbids leaving the current workspace directory or brute-forcing SHA-256 test digests during `L3` candidate evaluation.
- **16-Trial End-to-End RSI A/B Benchmark Verification (`track-RSI-E2E-v4`)**: Verified on DSH (`deepseek-flash max`) across 4 Ascend 950DT (A5) controlled contract tasks: Control `G2` `0/4 (0.0%)` vs Treatment `G4` `4/4 (100.0%)`, with `4/4` automatic `reuse -> explore` promotions (`caller_initiated_explore=False`) and `4/4` `graded-c6` local Git commits (`completed:applied`), independently audited by Codex `gpt-6-astra (max)`.

# mindie-agent-dsh 0.1.24: Strict Runtime `/mindie-agent` Skill vs. Install-Time Onboarding Boundary

[中文](mindie-agent-dsh-0.1.24.md)

- **Preserves Native Manual `/mindie-agent` Trigger (`disable-model-invocation: true`)**: Keeps DeepSeek Harness manual-only `/mindie-agent` session lease mounting (`P2.1`) while deterministic `remote-dev` and `mindie-tools` MCP servers remain zero-gate default-on (`P2.7`).
- **Moves Install & Consent Guides Out of Runtime `skills/`**: Relocates `user-notice.md`, `github-authentication.md`, and `workspace-setup.md` from `skills/mindie-agent/references/` to `docs/install/`, and removes all first-use onboarding, `vllm-ascend-workspace` setup, `gh auth login`, and GitHub Star prompts from `skills/mindie-agent/SKILL.md` and its runtime reference files.

# mindie-agent-dsh 0.1.27: Pre-`0.1.26` Seamless Self-Upgrade Handoff Fix

[中文](mindie-agent-dsh-0.1.27.md)

- **Restore `v0.1.25 -> v0.1.27` native self-upgrade handoff**: `scripts/setup.py` accepts pre-`0.1.26` `updater.py:setup_candidate` flags (`--python`, `--total-timeout`, `--install-timeout`, `--component-manifest`, `--prepare-only`) and bootstraps via the bundled `assets/mindie-harness-seed.pyz` whenever invoked under a pre-`0.1.26` interpreter lacking `mindie_harness.mcp.gateway`.
- **Side-effect-free `--prepare-only` staging and legacy `dsh/config.json` migration**: `--prepare-only` validates the candidate without touching canonical `dsh.json`, while the apply pass preserves `domain`, `root`, `feeds`, and rebound `component_commands` from legacy `dsh/config.json`, writing canonical `dsh.json` / `dsh.engine.json` and updating an existing legacy `dsh/config.json` in lockstep.
- **Automated `n-1 -> n` self-upgrade regression tests**: Adds `tests/test_legacy_self_upgrade.py` verifying the exact two-stage `setup_candidate` CLI flow (`--prepare-only` followed by apply), legacy `dsh/config.json` migration, and `npm pack` tarball file inclusion.

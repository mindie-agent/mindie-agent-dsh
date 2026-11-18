# mindie-agent-dsh 0.1.27：Pre-`0.1.26` 在线平滑自升级交接兼容修复

[English](mindie-agent-dsh-0.1.27.en.md)

- **恢复 `v0.1.25 -> v0.1.27` 在线自升级交接**：`scripts/setup.py` 支持旧版 `updater.py:setup_candidate` 传入的 `--python`、`--total-timeout`、`--install-timeout`、`--component-manifest` 与 `--prepare-only` 参数；在旧代解释器尚未具备 `mindie_harness.mcp.gateway` 时自动通过内置 `assets/mindie-harness-seed.pyz` 预置新版运行时并交接执行。
- **两阶段无污染准备与历史 `dsh/config.json` 配置平滑迁移**：`--prepare-only` 阶段零污染校验候选运行时，切流阶段自动继承历史 `dsh/config.json` 的 `domain`、`root`、`feeds` 与重绑后的 `component_commands`，原子写入规范 `dsh.json` 与 `dsh.engine.json` 并同步已存在的历史 `dsh/config.json`。
- **自动化 `n-1 -> n` 自升级契约回归测试**：新增 `tests/test_legacy_self_upgrade.py`，端到端覆盖 `setup_candidate` 两阶段参数（`--prepare-only` + apply）、历史 `dsh/config.json` 迁移与 `npm pack` 发行归档清单校验。

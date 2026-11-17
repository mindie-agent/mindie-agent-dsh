# mindie-agent-dsh 0.1.23：接入 `mindie-harness` 共享底座、Windows 无窗后台拉起加固与 `0.1.13` 组件基线对齐

[English](mindie-agent-dsh-0.1.23.en.md)

- **接入 `mindie-harness v0.1.0` 共享底座与 Windows 无窗进程防护**：内置确定性冷启动包 `assets/mindie-harness-seed.pyz` + `seed-lock.json`（由 `scripts/mindie_launch.py` 与 `paths.cjs` 统一校验），将跨代租约、受管进程树与 RSI 循环统一委托至 `mindie_harness`，并在 Node/Python 子进程拉起边界严格禁止 Windows 控制台弹窗。
- **历史设计文档归档与 `< 800` 行内聚治理**：将历史阶段性方案归档至 `docs/archive/`，对齐双语 `CONTRIBUTING{.en}.md` 与 `--profile host-plugin` PR 设计原则门禁，全部核心 `.mjs`/`.cjs`/`.py` 模块保持 `< 800` 行。
- **`0.1.13` 全量组件正式版锁定**：在 `pyproject.toml`、`runtime-requirements.txt` 与 `uv.lock` 中同步锁定 `mindie-harness v0.1.0`、`mindie-knowledge v0.13.23`、`remote-dev v0.11.13`、`mindie-diagnostics v0.5.16`、`mindie-debugger v0.1.4`、`mindie-profiler v0.5.16`、`mindie-sim v0.9.18` 与 `mindie-coordinator v0.7.13`。

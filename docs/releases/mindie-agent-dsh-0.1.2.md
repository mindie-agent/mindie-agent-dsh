# mindie-agent-dsh 0.1.2 Release Notes

**中文** | [English](https://github.com/mindie-agent/mindie-agent-dsh/blob/v0.1.2/docs/releases/mindie-agent-dsh-0.1.2.en.md)

`mindie-agent-dsh 0.1.2` 是面向 DeepSeek Harness（DSH）的维护更新版本（配套主产品 `mindie-agent 0.1.1`，保留既有 `0.1.1` 插件标签不变），新增共享运行时组件独立自动更新、`host` 与 `remote-dev` 同代挂载切换，以及原生 `dsh plugin` 安装回读校验与操作系统级文件锁保护。

## 核心更新

- **共享组件独立自动更新与同代子插件热切换**：
  - `cordis.patch.yml` 与 `host.mjs` 将原生 Host 插件（`host3.mjs`）与 `remote-dev` stdio MCP 客户端挂载在同一个 `mindie-agent-generation` 子 Cordis 纤维内，仅在当前无运行中 Agent 任务时随 `current.json` 同步切换至同一 Generation（挂载失败时自动回退至上一可用代数）。
  - 当无新插件 Release 时，更新器直接从各官方组件仓库发现正式 GitHub Release，按不降级原则构建带完整 `uv.lock` 的旁路组件环境并在空闲时切换 `current.json`（保留原生插件包，**无需重新安装 DSH 原生插件定义**）；插件升级时同样继承已安装组件的版本下界。
- **原生插件安装回读校验与文件锁自动释放**：
  - 插件级更新先校验新包的 `peerDependencies["@deepseek-ai/dsh"]` 与当前 `dsh --version` 兼容，再执行真实 `dsh plugin --profile <profile> add link:<package> --ignore-scripts` 并通过 `dsh plugin --profile <profile> list --json` 回读确认；若清理或回读结果不确定，如实记录 `stage="unknown"` 并停止盲目覆盖或回滚。涉及原生插件定义变更时标记 `needs_host_reload=True`，需重载宿主生效。
  - `updater.py` 对 `check.lock` 采用操作系统文件锁，确保更新进程异常退出或被终止后锁自动释放。
- **探索上下文原样透传与会话显式授权边界**：
  - 直接透传 `mindie-knowledge` 探索输出；后台更新绝不触发语料同步或自动授予会话知识权限，知识能力仍仅在会话内手动输入 `/mindie-agent` 后生效。

## 运行时组件与验证范围

- **配套运行时组件**：锁定正式发布的 `mindie-knowledge 0.13.3`（`38a42f6f778cfa555d8918fdcacd12cee4a181f5`）、`remote-dev 0.10.6`（`0463a13fce9618ec9a51904869d61acb0cdb8538`）与 `mindie-diagnostics 0.5.2`（`801a01b2789aed7f58e971a909205a7d40b61077`）；精确提交哈希见 `uv.lock`、`runtime-requirements.txt` 与 `harness-compatibility.json`。
- **验证与验收层级**：
  1. **macOS `arm64` 原生候选实测（DSH `0.2.0-rc.2`）**：默认新会话直接调用 `knowledge_query` 在执行前被拒绝、不初始化知识服务且不重试；显式输入 `/mindie-agent` 激活 `entry_enabled=1`，同会话续跑继承，独立新会话不继承；默认 `remote_job_status` 直接走确定性远端路径（查询不存在的 `job_id` 正常返回调用方未找到错误，不受知识门禁阻塞）。独立组件更新实测对齐 `mindie-knowledge 0.13.3`、`remote-dev 0.10.6` 与 `mindie-diagnostics 0.5.2`（保留原生插件包），更新后新建原生 profile 正常达到 `READY`。
  2. **自动化测试与各 OS CI**：既有更新/安装契约测试、新增原生插件更新测试（`3 passed`）、更新进程强杀锁释放测试以及 `host` + `remote-dev` 同代挂载 Node 单元契约测试（`tests/test_generation_launcher.mjs`，`1 passed`，注：该项为 Node 单元契约测试）全部通过，跨平台契约由各 OS CI 流水线覆盖。
  3. **正式发布后回读待办**：待四端 `0.1.2` 正式 GitHub Release 发布后，执行面向已发布正式包的本地自动更新回读确认。

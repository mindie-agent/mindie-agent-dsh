## 变更摘要 (Summary)

- 

## 宿主插件架构核查清单 (Pre-PR Checklist — `host-plugin`)

> 请逐项核实并勾选 `[x]`；纯文档变更可在对应证据中填写 `not-applicable: docs-only`。

- [ ] **HOST-NATIVE（宿主原生无窗进程与跨平台环境契约）**：确认 Node `spawn` 全部启用 `windowsHide: true` 且 `win32` 后台子进程严禁 `detached: true`（使用 `detached: process.platform !== 'win32'` 与大小写归一 `PATH`），Python 子进程启用 `CREATE_NO_WINDOW` 且无 Windows 裸调 `os.kill(pid, 0)`，核心模块 `< 800` 行并允许合法符号链接（无 `O_NOFOLLOW` 拦截）。
- [ ] **HOST-WIRE（单一语义 `mindie-agent` Skill、两套默认常驻 MCP 与 Receipt Honesty）**：确认收敛为单一静默语义 `mindie-agent` Skill（`user-invocable: false`，无斜杠命令），`host.mjs` 仅挂载 `remote-dev`（14 个 `dsh` 工具）与 `mindie-tools`（确定性 + `mindie_doctor` + 5 个 `knowledge_*`），零 `_meta`/`request_nonce`，父子会话共享 `state/remote` `job_id` 句柄，且主操作成功后本地回执异常保持主结果不丢失。
- [ ] **HOST-ADMISSION（全表面默认常驻与 `Stop` 同意范围前置门禁）**：确认 `remote-dev`、`mindie-tools` 与 `knowledge_*` 全部默认零前置门禁可用；`host3.mjs` 在查找 transcript 或派发子进程前以纯 Node 校验 `mindie-consent.json`（`contribute`/`local-only`）与 `mindie-community.json` 的 `project_roots`，`mindie_launch.py` 再校验运行时 tier 并保留显式 `Admission` 撤销（关闭或越界时零 SQLite 快速跳过）。
- [ ] **HOST-UPGRADE（canonical `dsh.json` / legacy `config.json`、Seed 锁与双代 GC）**：确认 canonical `dsh.json` 优先且旧版 `dsh/config.json` 仍可读，`MINDIE_AGENT_CONFIG`/`MINDIE_DSH_CONFIG` 显式覆盖优先，`assets/mindie-harness-seed.pyz` 与 `seed-lock.json` 校验一致，`maintain_runtime` 执行 Active+Rollback GC。
- **自查结论与证据**：

## 验证结果 (Verification)

- 

## 变更摘要 (Summary)

- 

## 宿主插件架构核查清单 (Pre-PR Checklist — `host-plugin`)

> 请逐项核实并勾选 `[x]`；纯文档变更可在对应证据中填写 `not-applicable: docs-only`。

- [ ] **HOST-NATIVE（宿主原生无窗进程与跨平台环境契约）**：确认 Node `spawn` 全部启用 `windowsHide: true` 且 `win32` 后台子进程严禁 `detached: true`（使用 `detached: process.platform !== 'win32'` 与大小写归一 `PATH`），Python 子进程启用 `CREATE_NO_WINDOW` 且无 Windows 裸调 `os.kill(pid, 0)`，核心模块 `< 800` 行并允许合法符号链接（无 `O_NOFOLLOW` 拦截）。
- [ ] **HOST-WIRE（单一语义 `mindie-agent` Skill、并发常驻 MCP 与 Receipt Honesty）**：确认收敛为单一静默语义 `mindie-agent` Skill（无评测条款外溢、无 `PROGRESS.md` / `mindie-bench-ablation/1` 耦合），`component_mcp.py` 并发常驻不阻塞并行请求，父子会话共享 `state/remote` `job_id` 句柄，且主操作成功后本地回执异常保持主结果不丢失。
- [ ] **HOST-ADMISSION（全表面默认常驻与 `Stop` 同意范围前置门禁）**：确认 `remote-dev`、`mindie-tools` 与 `knowledge_*`（Host 与 stdio MCP）全部默认零前置门禁可用；写操作按需签发默认会话租约（`not_before=0.0`）并尊重显式 `deactivate`，`Stop` 在打开 SQLite 前校验 `mindie-consent.json` 与 `project_roots`（关闭或越界时零 SQLite 开销快速跳过）。
- [ ] **HOST-UPGRADE（`config.json` / `current.json` 平滑升级兼容、Seed 锁与双代 GC）**：确认保持现有 `config.json`、`update/current.json` 与 `mindie-consent.json` 在线升级及回滚兼容，`assets/mindie-harness-seed.pyz` 与 `seed-lock.json` 校验一致，运行时目录遵循双代保护与 `.inuse.lock` 租约回收。
- **自查结论与证据**：

## 验证结果 (Verification)

- 

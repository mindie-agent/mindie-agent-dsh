## 变更摘要 (Summary)

- 

## 宿主插件架构核查清单 (Pre-PR Checklist — `host-plugin`)

> 请逐项核实并勾选 `[x]`；纯文档变更可在对应证据中填写 `not-applicable: docs-only`。

- [ ] **HOST-NATIVE（宿主原生无窗进程与跨平台环境契约）**：确认 Node `spawn` 全部启用 `windowsHide: true` 且 `win32` 后台子进程严禁 `detached: true`（使用 `detached: process.platform !== 'win32'` 与大小写归一 `PATH`），Python 子进程启用 `CREATE_NO_WINDOW` 且无 Windows 裸调 `os.kill(pid, 0)`，核心模块 `< 800` 行并允许合法符号链接（无 `O_NOFOLLOW` 拦截）。
- [ ] **HOST-WIRE（单一 `/mindie-agent` 入口、并发常驻 MCP 与 Receipt Honesty）**：确认无评测条款外溢（无 `PROGRESS.md` / `mindie-bench-ablation/1` 耦合），`component_mcp.py` 并发常驻不阻塞并行请求，父子会话共享 `state/remote` `job_id` 句柄，且主操作成功后本地回执异常保持主结果不丢失。
- [ ] **HOST-ADMISSION（`P2.7` 确定性零门禁与 `P2.1` 知识按需挂载）**：确认 `remote-dev` 与 `mindie-tools` 零前置会话门禁可用；未激活默认会话仅暴露 `mindie_entry`，`knowledge_*` 仅在 `/mindie-agent` 激活会话租约后挂载并在 `deactivate` / `agent/disposed` 时卸载，严格受 `mindie-consent.json` 约束。
- [ ] **HOST-UPGRADE（`config.json` / `current.json` 平滑升级兼容、Seed 锁与双代 GC）**：确认保持现有 `config.json`、`update/current.json` 与 `mindie-consent.json` 在线升级及回滚兼容，`assets/mindie-harness-seed.pyz` 与 `seed-lock.json` 校验一致，运行时目录遵循双代保护与 `.inuse.lock` 租约回收。
- **自查结论与证据**：

## 验证结果 (Verification)

- 

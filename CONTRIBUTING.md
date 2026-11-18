# 贡献指南 (`mindie-agent-dsh`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

感谢参与 `mindie-agent-dsh`（DeepSeek Harness 原生宿主插件）开发。提交变更前请遵循以下工程边界与验证流程。

## 1. 架构与宿主边界

1. **薄宿主层职责**：本仓仅维护 DSH Cordis 插件装载（`host.mjs` / `host3.mjs` / `paths.cjs`）、DSH v4 `zstd` 会话转录提取（`scripts/dsh_transcript.py`）、本地回环摘要路由（`scripts/dsh_organizer.py`）、常驻 Sidecar（`scripts/mindie_sidecar.py`）与 MCP 桥接；进程树监管、跨代锁与 RSI 辅助逻辑统一委托 `mindie_harness`（内置 `assets/mindie-harness-seed.pyz`）。
2. **Windows 零弹窗铁律**：Node 侧所有 `spawn` 必须设置 `windowsHide: true`，后台子进程统一使用 `backgroundSpawnOptions()`（`detached: process.platform !== 'win32'`，严禁在 `win32` 传入 `detached: true`），并通过 `buildChildEnv()` 归一化大小写无关的 `PATH`；Python 侧子进程统一传入 `CREATE_NO_WINDOW`。
3. **准入与状态诚实**：
   - `remote-dev`、`mindie-tools`（`component_mcp.py` 并发常驻）与 `knowledge_*`（Host 原生工具与 `knowledge_mcp.py`）对所有会话与子智能体默认常驻开放，共享 `state/remote` 以支持跨父子会话查询/停止 `job_id`；写操作按需签发默认租约（`not_before=0.0`），`Stop` 采集在打开 SQLite 前校验 `mindie-consent.json` 与 `project_roots`；
   - 严禁在 `except OSError:` 中吞没 `TimeoutError`，并允许合法符号链接配置读取（禁用 `O_NOFOLLOW` 一刀切拦截）。
4. **模块内聚与行尾规范**：所有运行时模块保持 `< 800` 行并在前 30 行声明 `[Role]`、`[Invariants]`、`[Timeouts & Locks]`、`[Side-Effect Policy]`；全仓统一使用 Unix `LF` (`\n`) 行尾。

## 2. 本地隔离测试与门禁验证

在隔离临时目录运行完整 Python 与 Node 测试套件，严禁污染宿主机真实 `$HOME` 或 `~/.dsh`：

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -ra -m "not live_npu"
node --check paths.cjs && node --check host.mjs && node --check host3.mjs
node tests/test_summary_endpoint.mjs
node --test tests/test_manual_entry_host.mjs tests/test_update_host.mjs \
  tests/test_generation_launcher.mjs tests/test_remote_isolation.mjs \
  tests/test_log_rotation.mjs tests/test_summary_host.mjs \
  tests/test_win32_spawn_and_gating.mjs
```

## 3. 提交 PR 前自查清单（`host-plugin`）

请按 [`.github/pull_request_template.md`](.github/pull_request_template.md) 逐项核实并勾选 `HOST-NATIVE`、`HOST-WIRE`、`HOST-ADMISSION`、`HOST-UPGRADE` 四项检查点并填写实证说明（纯文档改动可标注 `not-applicable: <具体原因>`）：

```bash
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr-body.md>
```

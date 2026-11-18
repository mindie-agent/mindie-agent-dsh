# 贡献指南 (`mindie-agent-dsh`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

感谢参与 `mindie-agent-dsh`（DeepSeek Harness 原生宿主插件）开发。提交变更前请遵循以下工程边界与验证流程。

## 1. 架构与宿主边界

1. **薄宿主层职责**：`v0.1.27` 仅维护六个生产文件——Host 边界 `paths.cjs` / `host.mjs` / `host3.mjs`，以及 `scripts/{mindie_launch.py,dsh_transcript.py,setup.py}`；MCP 路由、运行时准备、共享/更新、锁、存储 GC 与宿主适配统一委托 `mindie-harness`（`API_VERSION = 1`，内置 `assets/mindie-harness-seed.pyz`）。六者合计必须 `<= 1,500` 行。
2. **Windows 零弹窗铁律**：Node 侧所有 `spawn` 必须设置 `windowsHide: true`，后台子进程统一使用 `backgroundSpawnOptions()`（`detached: process.platform !== 'win32'`，严禁在 `win32` 传入 `detached: true`），并通过 `buildChildEnv()` 归一化大小写无关的 `PATH`；Python 侧子进程统一传入 `CREATE_NO_WINDOW`。
3. **准入与状态诚实**：
   - 每个会话默认挂载 `remote-dev`（14 个 `dsh` 工具）与 `mindie-tools`（确定性组件 + `mindie_doctor` + 5 个 `knowledge_*`），共享 `<state>/remote` 以支持跨父子会话查询/停止 `job_id`；`Stop` 采集先在纯 Node 中校验 `mindie-consent.json`（`contribute`/`local-only`）与 `mindie-community.json` 的 `project_roots`，再进入 `mindie_launch.py` 的运行时租约检查并保留显式 `Admission` 撤销；
   - 严禁在 `except OSError:` 中吞没 `TimeoutError`，并允许合法符号链接配置读取（禁用 `O_NOFOLLOW` 一刀切拦截）。
4. **模块内聚与行尾规范**：所有运行时模块保持 `< 800` 行并在前 30 行声明 `[Role]`、`[Invariants]`、`[Timeouts & Locks]`、`[Side-Effect Policy]`；全仓统一使用 Unix `LF` (`\n`) 行尾。

## 2. 本地隔离测试与门禁验证

在隔离临时目录运行完整 Python 与 Node 测试套件，严禁污染宿主机真实 `$HOME` 或 `~/.dsh`：

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -ra -m "not live_npu"
node --check paths.cjs && node --check host.mjs && node --check host3.mjs
node --test tests/test_dsh_host.mjs
wc -l paths.cjs host.mjs host3.mjs scripts/*.py
```

## 3. 提交 PR 前自查清单（`host-plugin`）

请按 [`.github/pull_request_template.md`](.github/pull_request_template.md) 逐项核实并勾选 `HOST-NATIVE`、`HOST-WIRE`、`HOST-ADMISSION`、`HOST-UPGRADE` 四项检查点并填写实证说明（纯文档改动可标注 `not-applicable: <具体原因>`）：

```bash
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr-body.md>
```

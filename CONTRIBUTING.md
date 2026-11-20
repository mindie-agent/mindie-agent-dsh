# 贡献指南 (`mindie-agent-dsh`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

感谢参与 `mindie-agent-dsh`（DeepSeek Harness 原生宿主插件，`v0.1.28`）开发。提交变更前请遵循以下工程边界与验证流程。

## 1. 架构与宿主边界

1. **薄宿主层职责**：`v0.1.28` 仅维护六个生产文件——Host 边界 `paths.cjs` / `host.mjs` / `host3.mjs`，以及 `scripts/{mindie_launch.py,dsh_transcript.py,setup.py}`（当前合计 `1,466` 行，严守 `<= 1,500` 行上限，零 `_knowledge_fallback` 影子桩）；全部 9 个运行时包（含 `npu-top 0.3.5` 与 `mindie-harness >= 0.1.5`）在 `pyproject.toml`、`runtime-requirements.txt`、`uv.lock` 与 `product-contract.json` 中统一锁定。MCP 路由、9 领域内容寻址绑定、共享/更新、锁与存储 GC 委托 `mindie-harness`（`API_VERSION = 1`）；冷启动种子引导（`assets/mindie-harness-seed.pyz`）隔离 `stdin=DEVNULL` 与 `stdout=DEVNULL`，确保 MCP JSON-RPC 首字节纯净。
2. **Windows 零弹窗与解释器解析铁律**：Node 侧所有 `spawn` 必须设置 `windowsHide: true`，后台子进程统一使用 `backgroundSpawnOptions()`（`detached: process.platform !== 'win32'`，严禁在 `win32` 传入 `detached: true`），通过 `buildChildEnv()` 归一化大小写无关的 `PATH`，并通过 `resolvePythonCommand` 拒绝 `WindowsApps` 空字节别名（`win32` 下绝不返回裸 `'python'`）；JSON 解析前剥离 UTF-8 BOM；Python 侧子进程统一传入 `CREATE_NO_WINDOW`。
3. **准入、转录 Spool 与状态诚实**：
   - `host.mjs` 为每个会话单次解析 `sessionId`（缺省时缓存 `dsh-conn-<uuid>` 连接身份）并通过 `closed` 挂载记录安全回收中途销毁/同 ID 替换的迟到 Fiber，默认挂载 `remote-dev`（14 个 `dsh` 工具）与 `mindie-tools`（11 个组件/状态工具 + 5 个 `knowledge_*`），共享 `<state>/remote` 以支持跨父子会话查询/停止 `job_id`；
   - `host3.mjs` 要求非空 `turn` 并以纯 Node 校验 `mindie-consent.json`（`contribute`/`local-only`）与 `mindie-community.json` 的 `project_roots`；`dsh_transcript.py` 将 `session.v4.jsonl.zstd` 投影为统一字节坐标的 UTF-8 Spool（`identify(native_zstd) == identify(spool_path)`，校验 `cwd` 包含关系，非阻塞加锁冷淘汰 `<= 64`）；`setup.py init` 跨 `admission_path` 迁移并保留 `inactive` 撤权会话；
   - 严禁在 `except OSError:` 中吞没 `TimeoutError`，并允许合法符号链接配置读取（禁用 `O_NOFOLLOW` 一刀切拦截）。
4. **模块内聚与行尾规范**：所有运行时模块保持 `< 800` 行并在前 30 行声明 `[Role]`、`[Invariants]`、`[Timeouts & Locks]`、`[Side-Effect Policy]`；全仓统一使用 Unix `LF` (`\n`) 行尾，CI 通过 `verify-host-plugin.yml`（`host: dsh`）执行宿主验收。

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

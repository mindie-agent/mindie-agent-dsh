# mindie-agent-dsh 0.1.22：存储生命周期治理与四代存量布局静默回收

[English](mindie-agent-dsh-0.1.22.en.md)

- **存储生命周期治理**：在更新检查 Pre-Build 与应用切换成功后，静默回收四代历史 DSH 布局——旧架构 `dsh/venv`、超量 `dsh/runtimes/<key>` 及其孤儿 `config/dsh/engines/<key>.json`、超量 `dsh/update/generations/v*`（含历史遗留 `asset.tgz`）、超量 `update/component-runtimes/*`，以及历史遗留的孤立 `~/.cache/mindie-agent-dsh/uv-cache`（Windows `%LOCALAPPDATA%\mindie-agent-dsh\Cache\uv-cache` 等旧专用缓存）。
- **回滚与活进程刚性保护**：`config.json` 当前绑定、`update/current.json` 的 `generation` 与 `previous_generation`、DSH profile `node_modules/@mindie-agent/dsh` 软链目标、活租约/活进程以及跨插件借用根始终保留；`updater.recover()` 的唯一回滚目标不会被回收。
- **消除双倍 `.venv` 与冗余 tarball**：组件合并运行时 `effective["python"]` 直接作为候选 setup 的解释器，`runtimes/<key>` 不再额外执行第二次 `uv sync`；解压并验证 `package/package.json` 后立即安全删除 `asset.tgz`。冷启动构建在默认 uv 缓存跨卷时使用同卷 `<data_home>/.uv-cache`，不再创建孤立缓存。
- **有界日志与共享运行租约**：`update/launcher.log`、`update/host.log` 达到 2 MiB 时原子轮转为 `.1`，最多保留当前文件与 `.1`；Windows 重命名冲突容错忽略。知识 MCP、组件 MCP 与常驻 sidecar 在运行期间对 `owning_dir(sys.executable)` 持有共享 `acquire_runtime_lease` 租约。
- **组件版本锁定**：同步锁定 `mindie-knowledge 0.13.22`、`mindie-coordinator 0.7.11`、`mindie-debugger 0.1.3`、`mindie-diagnostics 0.5.14`、`mindie-profiler 0.5.14`、`mindie-sim 0.9.15`、`remote-dev 0.11.12` 的正式提交，并更新 `uv.lock` 与 `runtime-requirements.txt`。
- **测试隔离与门禁**：新增跨平台存储生命周期与 Node 日志轮转回归测试；pytest 额外隔离 `TMPDIR`/`TEMP`/`TMP` 与 `UV_CACHE_DIR`；确认全部核心 `.py`/`.mjs` 模块（尤其 `host3.mjs`）保持 `< 800` 行。

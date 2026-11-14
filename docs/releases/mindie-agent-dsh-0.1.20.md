# mindie-agent-dsh 0.1.20

[English](mindie-agent-dsh-0.1.20.en.md)

- **Windows 11（`CP936` / UTF-8 BOM / Smart App Control / `\\?\` 扩展路径）与 WSL2 全链路加固**：
  - Node.js Host（`paths.cjs`、`host.mjs`、`host3.mjs`）统一补齐 `windowsHide: true`、Windows `-X utf8` 解释器启动参数、进程级 `taskkill /T /F` 超时清理与 WSL2 `/mnt/<drive>` Windows `.exe`/shim 过滤；
  - Python 运行时与工具桥（`mindie_paths.py`、`mindie_sidecar.py`、`sidecar_github.py`、`setup_process.py`、`component_tools.py`、`dsh_organizer.py`、`updater.py`、`native_update.py`、`dsh_diagnostics.py`、`dsh_transcript.py`、`knowledge_mcp.py`、`setup.py`）支持 Windows Smart App Control 下 `.exe` 入口包装器转 `python -I -X utf8 -m <module>` 调用、Win32 Job Object（`KILL_ON_JOB_CLOSE`）孙进程超时回收、`dsh_organizer` 请求发送前后传输状态诚实区分（发送前 `model_calls=0` 配置错误 vs 发送后 `UncertainError`）、UNC `file://` 与 `\\?\` 路径身份归一，以及 npm `%dp0%`/`%~dp0` `.cmd` 包装器直接解包；
  - 移除测试套件在 Windows 上的全部 `os.name == "nt"` 跳过项与 `symlink` 依赖，由 `ubuntu-latest`、`macos-latest` 与 `windows-latest` 全量执行契约与蒸馏测试门禁。

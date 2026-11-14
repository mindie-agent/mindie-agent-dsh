# mindie-agent-dsh 0.1.20

[中文](mindie-agent-dsh-0.1.20.md)

- **End-to-End Windows 11 (`CP936` / UTF-8 BOM / Smart App Control / `\\?\` Extended Paths) & WSL2 Hardening**:
  - Node.js Host modules (`paths.cjs`, `host.mjs`, `host3.mjs`) enforce `windowsHide: true`, `-X utf8` Python child invocation on Windows, process-tree `taskkill /T /F` timeout cleanup, and WSL2 `/mnt/<drive>` Windows `.exe`/shim filtering;
  - Python runtime and tool bridges (`mindie_paths.py`, `mindie_sidecar.py`, `sidecar_github.py`, `setup_process.py`, `component_tools.py`, `dsh_organizer.py`, `updater.py`, `native_update.py`, `dsh_diagnostics.py`, `dsh_transcript.py`, `knowledge_mcp.py`, `setup.py`) add Smart App Control-safe `python -I -X utf8 -m <module>` component dispatch, Win32 Job Object (`KILL_ON_JOB_CLOSE`) grandchild timeout reaping, pre-send vs post-send `dsh_organizer` transport honesty (`model_calls=0` `ConfigurationError` vs post-send `UncertainError`), UNC `file://` and `\\?\` transcript identity canonicalization, and direct npm `%dp0%`/`%~dp0` `.cmd` shim unwrapping;
  - Removed all `os.name == "nt"` test skips and `symlink` privileges across the test suite so `ubuntu-latest`, `macos-latest`, and `windows-latest` execute the full contract and distillation gates without skips.

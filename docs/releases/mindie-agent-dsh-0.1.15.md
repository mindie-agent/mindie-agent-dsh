# mindie-agent-dsh 0.1.15

`mindie-agent-dsh 0.1.15` fixes the Windows periodic console popup flash during 5-minute background auto-update checks (`windowsHide: true` in `host3.mjs`, `subprocess.CREATE_NO_WINDOW` in `scripts/updater.py` and `scripts/native_update.py`, and `pythonw.exe` background daemon launch in `scripts/setup.py`) and upgrades shared runtime components to `mindie-knowledge 0.13.12`, `mindie-diagnostics 0.5.6`, `remote-dev 0.11.5`, and `mindie-coordinator 0.7.4`.

## What's New (Human-Readable Summary)

- **Windows Zero-Console-Popup Subprocess Guardrails**: Enforces `windowsHide: true` in `host3.mjs` `spawn()` calls, `subprocess.CREATE_NO_WINDOW` across `scripts/updater.py` and `scripts/native_update.py`, and `pythonw.exe` preference in `scripts/setup.py` `background_interpreter` so the 5-minute auto-update check and background workers never flash a Windows console window.
- **Upgraded Runtime & Component Contracts**:
  - Locked runtime (`pyproject.toml`, `runtime-requirements.txt`, `uv.lock`, `harness-compatibility.json`): `mindie-knowledge 0.13.12` (`1840bd4e238e718676768489058917818d18cd3c`), `remote-dev 0.11.5` (`131b24533479e9cefc572144adccdcb345f70846`), and `mindie-diagnostics 0.5.6` (`9b331956b00737dd6d8c956da3416316f2de1ddf`).
  - Component contracts (`component-contracts` group): `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.3` (`dbadff04b60937a0722b0a227cdad01beed083cd`), and `mindie-coordinator 0.7.4` (`14d7cec7247bdcbea9b9878db4770e3a05a6ea41`).
- **Unified `setup.py sharing` & Step 5 Onboarding Popup**: Adds `setup.py sharing --status|--enable|--disable` with fast stdlib status inspection, incremental `GIT_CONFIG_COUNT` enterprise network compatibility, and native/structured interactive onboarding.

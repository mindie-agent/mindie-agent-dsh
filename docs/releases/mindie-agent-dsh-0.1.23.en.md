# mindie-agent-dsh 0.1.23: `mindie-harness` Shared Substrate, Windows Windowless Spawn Hardening, and `0.1.13` Component Baseline

[中文](mindie-agent-dsh-0.1.23.md)

- **Shared `mindie-harness v0.1.0` Substrate & Windowless Spawn Safety**: Bundles `assets/mindie-harness-seed.pyz` + `seed-lock.json` verified across `scripts/mindie_launch.py` and `paths.cjs`, delegates generation leases, process supervision, and RSI helpers to `mindie_harness`, and enforces windowless child-process spawning on Windows.
- **Historical Docs Archive & Sub-800-Line Module Hygiene**: Archives superseded design notes under `docs/archive/`, aligns bilingual `CONTRIBUTING{.en}.md` and `--profile host-plugin` PR governance, and keeps all core `.mjs`/`.cjs`/`.py` modules `< 800` lines.
- **`0.1.13` Formal Component Baseline**: Pins `mindie-harness v0.1.0`, `mindie-knowledge v0.13.23`, `remote-dev v0.11.13`, `mindie-diagnostics v0.5.16`, `mindie-debugger v0.1.4`, `mindie-profiler v0.5.16`, `mindie-sim v0.9.18`, and `mindie-coordinator v0.7.13` across `pyproject.toml`, `runtime-requirements.txt`, and `uv.lock`.

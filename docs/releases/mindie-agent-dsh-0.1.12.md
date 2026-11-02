# mindie-agent-dsh 0.1.12

`mindie-agent-dsh 0.1.12` widens DeepSeek Harness (`@deepseek-ai/dsh`) version compatibility to the full `>=0.2.0-rc.2 <1.0.0` `0.x` range (with `0.2.1-rc.5` interface verification), adds `mindie-debugger` to the `mindie-tools` MCP server, and upgrades the locked runtime and component contracts to the latest Ascend AI Infra releases (`knowledge 0.13.10`, `remote-dev 0.11.4`, `diagnostics 0.5.5`, `debugger 0.1.0`, `profiler 0.5.6`, `sim 0.9.2`, `coordinator 0.7.3`, and `npu-top 0.3.1`).

## What's New (Human-Readable Summary)

- **Forward-Compatible DSH `0.x` Range & `0.2.1-rc.5` Verification**: Widens `package.json` `peerDependencies["@deepseek-ai/dsh"]` and `harness-compatibility.json` from a single-version point lock (`0.2.0-rc.2`) to `>=0.2.0-rc.2 <1.0.0` (`0.2.0-rc.2..0.999.999`, `include_prerelease: true`), updates `scripts/native_update.py` (`scripts/semver_range.py`) to evaluate SemVer comparator ranges during native bundle updates, and records the `0.2.1-rc.5` host interface audit.
- **Upgraded Runtime & Component Contracts**:
  - Locked runtime (`pyproject.toml`, `runtime-requirements.txt`, `uv.lock`, `harness-compatibility.json`): `mindie-knowledge 0.13.10` (`7936a4fda67f6808d8fa73824363a4d7345766e5`), `remote-dev 0.11.4` (`7c67876d716feb3949e4dbf33eb5e06980ff0d36`), and `mindie-diagnostics 0.5.5` (`f2f2f946c172afc99342a84c0c1ed244f6d44e01`).
  - Component contracts (`component-contracts` group): `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.2` (`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`), and `mindie-coordinator 0.7.3` (`79a8b28eef72d57f965a01c6660849f024fbb1c0`).
- **Clearer Remote CANN Log Triage**: Exposes and refines the `debugger` MCP tool in `scripts/component_mcp.py` for remote container `plog` and ACLGraph fault analysis.

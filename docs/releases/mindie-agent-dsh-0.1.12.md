# mindie-agent-dsh 0.1.12

This release widens the `@deepseek-ai/dsh` optional peer dependency and
`harness-compatibility.json` declaration from a single-version point lock
(`0.2.0-rc.2`) to the forward-compatible `0.x` SemVer range
`>=0.2.0-rc.2 <1.0.0` (including prerelease versions such as `0.2.1-rc.2`),
updates `scripts/native_update.py` to evaluate SemVer comparator ranges during
native bundle updates, and clarifies the remote container CANN `plog` triage
workflow in the `debugger` MCP tool description.

## Runtime and verification

The locked runtime selects `mindie-knowledge 0.13.8`
(`9a4325858e6fadb10715275826a7795843cbbdf4`), `remote-dev 0.11.2`
(`85be999811771e03ecc7d78e5e560fac1636e7ef`), and `mindie-diagnostics 0.5.4`
(`5ae643e19223f2181ecdbaa3edaf464137c4805e`), with contract pins at
`mindie-profiler 0.5.5` (`7cdd56b6cc9e0e7f603368393497487dd2dbfe39`),
`mindie-sim 0.9.0` (`f5608b4943f515c401d2f9b12fffb579a1e2571b`), and
`mindie-coordinator 0.7.1` (`d146c55c414bfb92136cf5fb7fa11ae14c673ef0`).

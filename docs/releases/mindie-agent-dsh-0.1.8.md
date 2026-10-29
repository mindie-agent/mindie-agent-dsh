# mindie-agent-dsh 0.1.8

Paired with MindIE Agent `0.1.4`, this release makes knowledge loading depend on
the task's missing information and carries standard external component commands
into managed runtime updates.
It retains the explicit `/mindie-agent` session gate and the existing native
release updater.

## Behavior

- A self-contained bug with a file, function and local invariant can proceed
  directly to inspection and verification. The Skill permits zero knowledge
  calls; it introduces no classification model or compulsory query.
- Knowledge-dependent work uses a focused query, selects the relevant references
  and loads their prerequisite closure within the requested budget. The paired
  knowledge runtime applies the same budget to single-reference and batch
  explanations.
- Standard `python -m` component bindings participate in release selection and
  version floors. Successful idle activation rebinds them to the selected
  interpreter; custom wrappers remain unchanged.
- A candidate can complete an older updater's core-only preparation before
  activation, so an already configured optional component does not strand the
  first upgrade. The existing runtime binding comparison still rejects a
  concurrent installation change.
- Updates run in the installed DSH profile. The launcher switches child
  generations only when no agent is running. Changes to the launcher or native
  profile definitions require a new host process, reported by
  `needs_host_reload`; updating a package alone is not evidence that a running
  host loaded it.

## Runtime and host compatibility

The exact source pins are recorded in `uv.lock`, `runtime-requirements.txt` and
the current `runtime_components` entry of `harness-compatibility.json`.
Historical `verified` entries retain their original versions and evidence.

| Component | Version / Tag | Role | Source commit |
| --- | --- | --- | --- |
| `mindie-knowledge` | `0.13.6` / `v0.13.6` | Runtime dependency | `9af5a82ad557e2120277693854a0571613c8a115` |
| `remote-dev` | `0.11.1` / `v0.11.1` | Runtime dependency | `2a750ec8c74d7f053410d48dd820aeb542e7d3b1` |
| `mindie-diagnostics` | `0.5.4` / `v0.5.4` | Runtime dependency | `5ae643e19223f2181ecdbaa3edaf464137c4805e` |
| `mindie-profiler` | `0.5.1` / `v0.5.1` | Optional component contract | `25c789cd42389c893234c5db20eca67e63b8878a` |
| `mindie-sim` | `0.7.1` / `v0.7.1` | Optional component contract | `15f93f3f0247b2653d0e9441eae56959e4d16d4e` |
| `mindie-coordinator` | `0.7.0` / `v0.7.0` | Optional component contract | `e4a86c972a99c442c78ad7f7ba627af7da5ef7f2` |

The declared native host remains DeepSeek Harness `0.2.0-rc.2`, with Node.js
`>=22` and Python `>=3.11`.

## Verification scope

- The release workflow installs the exact lock, validates formal first-party
  dependencies, runs Python and Node contracts, and builds the npm archive.
  CI checks the adapter on Ubuntu, macOS and Windows.
- The tag workflow attaches `benchmark-results.json`, `benchmark-results.md`
  and checksums from the tagged source. The required public-transcript CPU
  microbenchmark is a functional and local latency check, not an agent
  productivity claim.
- The earlier paired DSH/Codex pilot found lower cost on its self-contained
  single-file task, while complex-task overall non-regression did not pass.
  Its frozen artifacts preceded the final source integrity fixes; it does not
  establish a general cost guarantee for this release.
- Installed-profile auto-update and native runtime readback are separate
  post-publication acceptance steps. No model request is needed to trigger the
  existing idle update timer or inspect the resulting binding.

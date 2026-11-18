# Contribution, privacy, and fault-reporting notice

Bootstrap with
`uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host dsh`;
first-run setup then asks the three questions (workspace, privacy tier, SSH NPU)
documented in [workspace setup](workspace-setup.md). `gh` is optional and never
blocks `local-only`/`read-only`.

`mindie-consent.json` controls one of three runtime privacy tiers:

| Tier | Command | Behavior |
| --- | --- | --- |
| `contribute` | `python scripts/setup.py sharing --enable --project-root <abs>` | Captures sanitized public transcripts for community knowledge PRs and sends sanitized fault reports to `mindie-agent/mindie-agent`. `gh` is recommended but optional. |
| `local-only` | `python scripts/setup.py sharing --local-only --project-root <abs>` | Captures and indexes locally only; no outgoing PR or fault report. `gh` is not required. |
| `read-only` / `disabled` | `python scripts/setup.py sharing --disable` | Disables capture, outgoing sharing, and fault reporting. Knowledge retrieval and `remote-dev` remain available. |

Inspect the current state without side effects:

```bash
python scripts/setup.py sharing --status
```

Knowledge contributions retain visible user and assistant messages after local
redaction. Hidden reasoning, system/developer instructions, and raw tool
calls/results are excluded. Redaction cannot identify every business secret;
exclude confidential projects from contribution.

Fault reports contain allowlisted product/code metadata, incident identifiers,
and sanitized error categories. They exclude prompts, transcripts, command
strings, environment values, credentials, and personal filesystem paths.
Recording a local incident does not mean a GitHub issue was published: inspect
the reporting worker and queue status. A failed worker startup remains degraded
and must not be presented as ready.

Existing `disabled`, `later`, and read-only choices stay unchanged. A legacy
choice without a unified consent revision requires an explicit contribution
choice before fault uploads begin; ordinary startup does not grant consent.
Turning contribution off withdraws pending reports, and turning it on again does
not upload incidents from the previous authorization or disabled interval.
Already published GitHub material remains public.

After updating an installed DSH bundle, restart the entire DSH process so every
MCP child and Stop worker reads the same profile policy. The policy is
`<config-home>/diagnostics.json`; the contribution authority is the sibling
`mindie-consent.json`. Run the read-only `mindie_doctor` tool after the restart
and report its concrete result once; it never blocks the requested task.

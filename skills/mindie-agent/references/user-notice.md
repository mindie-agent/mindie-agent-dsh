# Contribution and fault-reporting notice

The explicit `contribute` choice enables both public knowledge sharing for the selected project scope and automatic sanitized product-fault reports to `mindie-agent/mindie-agent`. Use `disabled` to turn both off. Read-only knowledge and remote tools remain available.

Knowledge contributions retain visible user and assistant messages after local redaction. Hidden reasoning, system/developer instructions, and raw tool calls/results are excluded. Redaction cannot identify every business secret; exclude confidential projects from contribution.

Fault reports contain allowlisted product/code metadata, incident identifiers, and sanitized error categories. They exclude prompts, transcripts, command strings, environment values, credentials, and personal filesystem paths. Recording a local incident does not mean a GitHub issue was published: inspect the reporting worker and queue status. A failed worker startup remains degraded and must not be presented as ready.

Existing `disabled`, `later`, and read-only choices stay unchanged. A legacy choice without a unified consent revision requires an explicit contribution choice before fault uploads begin; ordinary startup does not grant consent. Turning contribution off withdraws pending reports, and turning it on again does not upload incidents from the previous authorization or disabled interval. Already published GitHub material remains public.

After updating an installed DSH bundle, restart the entire DSH process so every MCP child, sidecar, and Stop worker reads the same profile policy. The policy is `<config-home>/diagnostics.json`; the contribution authority is its sibling `mindie-consent.json`.

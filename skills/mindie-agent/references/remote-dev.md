# Remote-dev surface details

`remote-dev` is the published remote development substrate. Its MCP server is
started from the pinned runtime; the tool names are
`mcp__remote-dev__remote_read`, `remote_write`, `remote_edit`,
`remote_multi_edit`, `remote_bash`, `remote_glob`, `remote_grep`, `remote_ls`,
`remote_apply_patch`, `remote_job_status`, `remote_job_tail`,
`remote_job_stop`, `remote_job_stdin`, `remote_artifact_manifest`,
`remote_artifact_pull`, `remote_artifact_push`, `remote_context_snapshot`, and
`remote_probe`.

## Endpoint selection

Every call needs one selector:

- `host` + `port` (plus optional `user`, default `root`), optionally with an
  existing Docker `container` name or ID; or
- `alias` from the user's configured endpoint alias files.

Do not guess. If the user gives a machine but no container, ask only when the
target container is genuinely ambiguous. Reuse the full container ID the tool
returns instead of repeating a name lookup.

## Jobs

Long commands may yield a job ID. Keep it, poll with `remote_job_status` or
`remote_job_tail`, and stop it with `remote_job_stop`. A job may outlive a lost
MCP connection; inspect its original record before starting another one.

## Safety

- A timeout or lost receipt is `unknown`, not "not executed". Inspect before a
  deliberate repeat.
- Never automatically replay a write, edit, patch, artifact push, or bash
  mutation after an uncertain result.
- Keep the CPU/source-only checks distinct from actual NPU execution. Record
  the revision, device, command, output, and limit for real runs.
- Do not modify the user's unrelated containers, SSH configuration, or other
  users' live jobs.

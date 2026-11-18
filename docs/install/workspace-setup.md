# Development workspace setup

Run this flow during the first DSH session after the deterministic bootstrap:

```bash
uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host dsh
```

The installer warms the locked runtime and writes `dsh.json` / `dsh.engine.json`.
Reuse the user's creation, initialization and registration decisions; ordinary
tasks and updates do not repeat onboarding.

1. Check the current tier with
   `python scripts/setup.py sharing --status` (read-only). When
   `"needs_onboarding_prompt": true` (or no workspace choice exists), invoke the
   native question tool in the current DSH session — or present a structured
   numbered `[1]/[2]` card when no question tool is available — and ask all three
   questions once:
   1. **Q1 Local workspace**: create/initialize `vllm-ascend-workspace`
      (recommended), reuse the current directory, or use an existing absolute
      path. Resolve `WORKSPACE_ROOT` first.
   2. **Q2 Experience and privacy tier** (`gh`-aware): when
      `gh api user --jq .login` succeeds, recommend `contribute`; otherwise
      recommend `local-only`; `read-only` is always available.
   3. **Q3 One-step SSH NPU connection** (optional now, repeatable later).
2. Before downloads or fork setup, follow [GitHub Sign-In](github-authentication.md).
   Reuse authentication already verified during installation. A GitHub username
   alone does not establish authentication; `gh` is never required for
   `local-only` or `read-only`.
3. For a new directory, run
   `git clone https://github.com/mindie-agent/vllm-ascend-workspace.git "<destination>"`.
   For reuse, inspect the selected checkout first and preserve existing files.
   Report clone failures with the actual path and result; inspect an interrupted
   clone before retrying.
4. Record the tier with exactly one of:
   ```bash
   python scripts/setup.py sharing --enable --project-root "$WORKSPACE_ROOT"
   python scripts/setup.py sharing --local-only --project-root "$WORKSPACE_ROOT"
   python scripts/setup.py sharing --disable
   ```
5. Register/open the absolute workspace root through DSH's native workspace
   control (below). Reuse an existing record for the same path; a child-shell
   `cd` is not project registration.
6. If initialization was approved, read the checkout's `AGENTS.md`, `README.md`,
   and `.agents/scripts/README.md`, then use the checked-out initializer via
   `uv` (avoids the Windows Store `python3` alias):
   `uv run --no-project --python 3.11 python .agents/scripts/repo_init.py --no-input`.
   For first setup with the verified account's existing forks, pass
   `--github-user <account>`. The initializer configures forks; it does not
   create them.
7. Finish with the read-only `mindie_doctor` readiness summary and report the
   path, native registration/open status, initialization status, tier, and NPU
   connection separately. Resume a failed initialization in the same workspace;
   workspace setup never widens the approved contribution scope.

## Native entry

DSH: use the native workspace picker or the active authenticated client
operation `workspace/create`. In DSH `0.2.0-rc.2`,
`ctx.remote.workspace.create({path: "<absolute-path>"})` registers an existing
directory and reuses the same path. Check the successful native result and the
workspace list. The path must exist on the running DSH host in its current
profile.

Use available native tools or UI automation. If the agent cannot operate the
required folder picker, give the selected path and that one remaining action and
report registration as pending. Never edit private harness project databases or
create a second client profile to simulate registration.

## One-step NPU connection

Prefer the tool call
`remote_auth(action="setup", host="<REAL_IP>", port=22, user="<REAL_USER>", save_default=True, discover=True)`.
Terminal mode may use (leading space and `--password` last):

```text
 /abs/path/to/python -m remote_dev auth setup --host <REAL_IP> --port 22 --user <REAL_USER> --save-default --discover --password
```

After the three questions, run `mindie_doctor`. If `gh` is logged in and the user
agreed, append one polite Star line:

```sh
gh api --hostname github.com --silent -X PUT /user/starred/mindie-agent/mindie-agent
```

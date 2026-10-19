# Explicit Refresh of an Existing `0.0.2` Installation

This `0.0.2` release is an authorized one-time replacement of the earlier `0.0.2` build (`828c375d1f546ed653250526465b8075c1691390`). Because the legacy updater compares version strings using strict greater-than (`>`), it will not automatically select a new commit that carries the same `0.0.2` version string. In addition, an active DSH process keeps the previously loaded Node Host plugin in memory until the process exits.

If you already installed the earlier `0.0.2` build, refresh both the Python runtime (`scripts/setup.py`) and the DSH plugin bundle using the exact release source SHA, then restart the entire DSH process.

These steps preserve your existing data and configuration directories, unified contribution choice (`mindie-consent.json`), community settings, and local knowledge materials. Keep any existing `MINDIE_DSH_DATA_HOME`, `MINDIE_DSH_CONFIG_HOME`, `DSH_HOME`, or other path environment variables set in your shell; do not delete those directories or create a new profile.

## Step-by-Step Refresh

1. **Exit the running DSH process** for the target profile so the updated Node Host plugin modules reload cleanly after replacement.
2. **Set `MINDIE_RELEASE_COMMIT`** to the full commit SHA of the current `0.0.2` GitHub Release and **`MINDIE_PROFILE`** to your active DSH profile (e.g., `web`).
3. **Refresh the Python runtime and replace the plugin bundle** from that exact commit:

POSIX shell:

```sh
set -e
MINDIE_RELEASE_COMMIT="<full commit from the 0.0.2 release>"
MINDIE_PROFILE="web"
MINDIE_STAGE="$(mktemp -d)"
git clone https://github.com/mindie-agent/mindie-agent-dsh.git "$MINDIE_STAGE/source"
git -C "$MINDIE_STAGE/source" checkout --detach "$MINDIE_RELEASE_COMMIT"
uv run --quiet --no-project --python 3.13 \
  "$MINDIE_STAGE/source/scripts/setup.py" --python 3.13 --no-sync
dsh plugin --profile "$MINDIE_PROFILE" add \
  "git+https://github.com/mindie-agent/mindie-agent-dsh.git#$MINDIE_RELEASE_COMMIT" \
  --ignore-scripts
```

Windows PowerShell:

```powershell
$MINDIE_RELEASE_COMMIT = "<full commit from the 0.0.2 release>"
$MINDIE_PROFILE = "web"
$MINDIE_STAGE = Join-Path ([IO.Path]::GetTempPath()) ("mindie-dsh-" + [guid]::NewGuid().ToString("N"))
git clone https://github.com/mindie-agent/mindie-agent-dsh.git $MINDIE_STAGE
if ($LASTEXITCODE -ne 0) { throw "clone failed" }
git -C $MINDIE_STAGE checkout --detach $MINDIE_RELEASE_COMMIT
if ($LASTEXITCODE -ne 0) { throw "checkout failed" }
uv run --quiet --no-project --python 3.13 "$MINDIE_STAGE/scripts/setup.py" --python 3.13 --no-sync
if ($LASTEXITCODE -ne 0) { throw "runtime setup failed" }
dsh.cmd plugin --profile $MINDIE_PROFILE add "git+https://github.com/mindie-agent/mindie-agent-dsh.git#$MINDIE_RELEASE_COMMIT" --ignore-scripts
if ($LASTEXITCODE -ne 0) { throw "bundle refresh failed" }
```

- Run the commands in order and stop on any error. `--no-sync` retains the existing local knowledge snapshot during the runtime refresh (omit `--no-sync` if you also want to sync the remote knowledge feed immediately).
- Verify that `setup.py` outputs `status: "ready"` with `mindie-knowledge 0.12.2`, `remote-dev 0.10.1`, and `mindie-diagnostics 0.4.3`. `setup.py` also copies the transcript adapter, summary worker, and path helper into `state_dir/bin/` so the staging checkout (`$MINDIE_STAGE`) can be safely removed afterwards.
- For a DSH Desktop profile (where the CLI rejects `--profile desktop`) or scripted GitHub bundle installers (`install-from-github.mjs`), install the same `git+https://github.com/mindie-agent/mindie-agent-dsh.git#<SHA>` spec via the Desktop **Plugins** page or `plugin_manager(action="install_bundle", target="git+https://github.com/mindie-agent/mindie-agent-dsh.git#<SHA>")`, then quit and relaunch the DSH application.

4. **Restart the DSH process**, verify that `mindie-agent-dsh` is enabled on the **Plugins** page, open a new session, and run `/mindie-agent`.

To verify Host row composition from the CLI without invoking a model:

```sh
dsh --profile "$MINDIE_PROFILE" --dump-config
```

Confirm that the output lists `mindie-agent-host`, `mindie-agent-skill`, and `mindie-agent-remote-mcp`.

## Verified Refresh Behavior & Platform Notes

- **State & Consent Preservation**: Refreshing from `828c375d1f546ed653250526465b8075c1691390` to the updated `0.0.2` commit preserves stored knowledge materials, `mindie-consent.json` and community configuration files, existing `disabled` / `later` / `read-only` choices, and active session leases without re-prompting.
- **Self-Contained Persistent Workers**: Because `setup.py` installs worker scripts under `state_dir/bin/`, removing the temporary source clone after setup leaves `--identity` checks and loopback summary worker requests working normally.
- **Windows User-Level Execution**: Run DSH and `setup.py` under your normal non-elevated Windows user account so background scheduled tasks and `pythonw.exe` share standard user directory permissions.

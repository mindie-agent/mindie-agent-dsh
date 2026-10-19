# Explicit refresh of an existing 0.0.2 installation

The earlier `0.0.2` installer/updater compares version numbers. It does not
discover another commit carrying the same version. Refresh both the Python
runtime and the DSH bundle explicitly, using the full commit recorded in the
new GitHub Release.

These steps retain the existing data/config directories, consent choices,
community settings and knowledge materials. Keep any existing
`MINDIE_DSH_DATA_HOME`, `MINDIE_DSH_CONFIG_HOME`, `DSH_HOME` and other path
overrides in the shell. Do not delete those directories or create a new profile
to perform an upgrade.

1. Exit the DSH process using the affected profile. Package replacement needs
   a process restart to load the new JavaScript modules.
2. Set `MINDIE_RELEASE_COMMIT` to the release's full commit and `MINDIE_PROFILE`
   to the profile already used by DSH; `web` below is an example.
3. Provision from that commit, then replace the bundle with the same commit.

POSIX shell:

```sh
set -e
MINDIE_RELEASE_COMMIT="<full commit from the release>"
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
$MINDIE_RELEASE_COMMIT = "<full commit from the release>"
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

Run the commands in order and stop on any failure. `--no-sync` retains the
existing feed while separating package refresh from a network feed sync; omit
it when a feed refresh is also wanted. Setup must report `status: "ready"`.
The new setup copies the transcript adapter, summary worker and its path module
under the persistent data directory. The staging clone can then be removed.

For the Desktop-managed profile, use its Plugins page to install the same exact
Git spec after provisioning; the CLI deliberately rejects `--profile desktop`.
The `plugin_manager` tool's `install_bundle` action accepts that same spec.
Close and reopen Desktop after replacing its package.

4. Restart the original DSH profile/process. Check that `mindie-agent-dsh` is
   enabled on the Plugins page, open a new session and use `/mindie-agent`.
   The native DSH `0.2.0-rc.2` CLI used in the Windows acceptance selected the
   bundle during `add`; checking the saved selection also covers installations
   with different package-manager behavior.

The CLI-only composition check does not call a model:

```sh
dsh --profile "$MINDIE_PROFILE" --dump-config
```

It should include `mindie-agent-host`, `mindie-agent-skill` and
`mindie-agent-remote-mcp`. It checks composition, not a model turn or successful
Stop-event capture.

## Acceptance and Windows boundaries

Native Windows acceptance refreshed old commit `828c375d1f546ed653250526465b8075c1691390`
to the new `0.0.2` runtime: a real stored material retained its content and
revision, consent/community file hashes stayed unchanged, the disabled sharing
choice stayed disabled and an existing session lease stayed active. Exact-SHA
DSH package replacement and native configuration composition also passed.
The persistent-worker regression removes the setup checkout, then exercises
`--identity` and one authenticated loopback summary response using the installed
worker. It uses a synthetic response and makes no paid model call.

Run normal Windows desktop sessions without elevation. An elevated SSH/admin
process can create owner-only directories owned by Administrators; a
LeastPrivilege background task cannot read those directories. Application
Control can also block the selected `pythonw.exe`. Acceptance on a separate
Windows host confirmed normal-user Task Scheduler startup, Stop-envelope
capture, secret/IP redaction and material creation. This does not certify a
machine whose local policy blocks the executable or a model-generated host
turn. Do not change application-control policy or elevate the service to make
an acceptance check pass.

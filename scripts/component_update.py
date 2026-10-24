"""[Role] Prepare component-only generations for DSH's existing installer.
[Invariants] The active native package is retained with an independently locked
runtime. New plugin releases inherit all installed component version floors.
[Timeouts & Locks] Shared builder has 420s; setup/probe/handoff uses 180s plus
10s outer cleanup. Existing installation lock serializes binding publication.
[Side-Effect Policy] Builds software and invokes setup's idle switch only;
no knowledge session, feed sync or model work is performed.
"""


def prepare(source, python, *, force=False, active=False):
    import mindie_paths as mp
    from mindie_knowledge.component_runtime import source_baseline, discover, build
    baseline = source_baseline(source)
    selected = discover(python, baseline["runtime"], force=force)
    if active and not selected["changed"]:
        return None
    if {name: row["revision"] for name, row in selected["runtime"].items()} == baseline["runtime"]:
        return None
    return build(source, mp.data_home() / "update/component-runtimes", baseline,
                 selected["runtime"], python=python)

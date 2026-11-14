"""[Role] Adapt installed deterministic component CLIs to DSH MCP calls.
[Invariants] Discovery is static; execution uses argv without a shell or knowledge activation.
[Timeouts & Locks] One owned child with a caller deadline, five-second cleanup and <=1 MiB output.
[Side-Effect Policy] Only explicit calls run components; local diagnostics never uploads a bundle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time

import mindie_paths as paths


CAPTURE_LIMIT_BYTES = 1024 * 1024

MODULE_MAP = {
    "profiler": "mindie_profiler",
    "sim": "mindie_sim",
    "coordinator": "mindie_coordinator.cli",
    "debugger": "mindie_debugger.cli",
}


DESCRIPTIONS = {
    "profiler": (
        "Inspect and analyze Ascend NPU profiling captures using mindie-profiler. "
        "Pass CLI arguments as an argv array, e.g. ['inventory', '--root', '/capture'] "
        "or ['analyze', '--root', '/capture', '--full-range']. Commands: inventory, analyze, "
        "correlate, compare, sql, camodel, and kernels. Use ['--help'] to inspect options "
        "and --output for large reports."
        " Offline analysis of existing captures requires no device lease or managed task binding."
    ),
    "sim": (
        "Compute Ascend hardware bounds (A2/A3/A5), operator/graph/serving latency, HBM capacity "
        "(including async_scheduling delayed release), Roofline efficiency, SLA concurrency "
        "frontiers, and P/D disaggregation using mindie-sim. Pass CLI arguments as an argv "
        "array: synthesize a case from model/hardware IDs or HF config (e.g. ['compose', "
        "'--model', 'deepseek-v2-lite', '--hardware', 'Ascend910B2C', '--tp', '8', "
        "'--async-scheduling', '--output', '/case.json']) and evaluate it (e.g. ['predict', "
        "'/case.json', '--scope', 'body', '--phase', 'decode', '--sequences', '32'], "
        "['efficiency', '/case.json'], ['sla', '/case.json', '--sla-ms', '50'], "
        "['sweep', '/case.json', '--mode', 'pd'], ['skew', '/case.json'], or "
        "['graph', '/case.json', '--phase', 'prefill', '--tokens', '32']). "
        "Use ['--help'] to inspect options and --output for large reports."
        " Simulation calculations require no device allocation or managed task binding."
    ),
    "coordinator": (
        "Manage Ascend host inventory, probe CPU/RAM/NPU capacity, bind containers, "
        "and acquire/inspect/release NPU and service-port leases through "
        "mindie-coordinator. Pass CLI argv, e.g. ['hosts', '--action', 'list'] "
        "or ['probe', '--json', '{\"action\":\"hosts\"}']. Use ['--help'] to inspect options."
        " Host inventory, probes, containers and bare-metal leases need no managed "
        "task binding or knowledge activation. Omit context_file for these independent "
        "operations; inspect resources before acquiring a lease."
    ),
    "debugger": (
        "Ascend NPU fault triage (`triage`, `decode-code`), ACLGraph/NPUGraph capture/replay & buffer-address "
        "debugger (`aclgraph`: `guard`, `inspect`), and operator precision/memory-stomp toolkit (`precision`: "
        "`check-call`, `replay-call`, `inspect-capsule`, `compare-traces`, `watch`) via mindie-debugger. "
        "Error-code decoding (`['decode-code', '507015']`) or inline stderr triage (`['triage', '--text', '<err>']`) "
        "runs locally. Since full CANN `plog` directories (`$ASCEND_PROCESS_LOG_PATH` or `~/ascend/log/debug/plog`) "
        "and dynamic ACLGraph/precision runtime hooks reside inside the remote NPU container, call `['remote-guide']` "
        "(or `['triage']` / `['aclgraph', ...]` / `['precision', ...]`) to stage the pure-Python `mindie_debugger` "
        "bundle locally and get the exact `remote-dev` (`remote_artifact_push` + `remote_bash`) commands to triage "
        "CANN `plog`s in-place (`python3 -m mindie_debugger.cli triage`) or run `ACLGraphCaptureGuard`, "
        "`ACLGraphAddressTracker`, `ACLGraphDumper`, `ACLGraphVllmInspector`, and `precision` inside the NPU container."
    ),
}


def catalog():
    limits = {
        "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 600000, "default": 120000,
                       "description": "Execution deadline; allow inner budgets plus cleanup."},
        "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": 262144,
                              "default": 16384,
                              "description": "Combined output budget in four-byte units; use output files for larger reports."},
    }
    tools = [
        {"name": name, "description": description + " No knowledge activation is required.",
         "inputSchema": {"type": "object", "additionalProperties": False,
                         "required": ["argv"], "properties": {
                             "argv": {"type": "array", "items": {"type": "string"},
                                      "description": "CLI arguments without shell expansion. Use absolute paths or set cwd."},
                             "cwd": {"type": "string", "description": "Absolute working directory for relative input/output paths."},
                             **limits}},
         "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}}
        for name, description in DESCRIPTIONS.items()
    ]
    for name, description, properties, required in (
        ("diagnostics_status", "Read local diagnostic/reporting status without starting a worker or contacting GitHub.",
         {"config": {"type": "string", "description": "Optional existing diagnostics policy path."}}, []),
        ("diagnostics_bundle", "Export a bounded sanitized local support bundle; does not upload it.",
         {"root": {"type": "string", "description": "Optional local diagnostics log root; defaults to the active MindIE diagnostics root."},
          "operation_id": {"type": "string"}, "incident_id": {"type": "string", "description": "Incident identifier alias for operation_id."},
          "output": {"type": "string"}}, []),
    ):
        tools.append({
            "name": name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": {**properties, **limits}, "required": required},
            "annotations": {"readOnlyHint": name == "diagnostics_status",
                            "destructiveHint": False, "openWorldHint": False},
        })
    tools.append({
        "name": "mindie_status", "description": "Read installed MindIE runtime paths and version; no service or activation.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    })
    return tools


def adapter_config():
    value = json.loads(paths.config_file().read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("python"), str):
        raise ValueError("MindIE runtime configuration requires its Python interpreter")
    return value


def command(adapter, component):
    configured = adapter.get("component_commands", {}).get(component)
    if configured is not None:
        if (not isinstance(configured, list) or not configured
                or not all(isinstance(value, str) and value for value in configured)):
            raise ValueError("component_commands entries must be nonempty executable argv arrays")
        return configured
    if component == "diagnostics":
        return [adapter["python"], "-I", "-X", "utf8", "-m", "mindie_diagnostics.cli"]
    executable_name = "mindie-" + component
    executable = shutil.which(executable_name, path=str(Path(adapter["python"]).parent))
    if executable is None and Path(adapter["python"]).absolute() == Path(sys.executable).absolute():
        executable = shutil.which(executable_name, path=sysconfig.get_path("scripts"))
    executable = executable or paths.which_native(executable_name)
    if executable is None:
        raise FileNotFoundError(
            executable_name + " is not installed; install it in the runtime/on PATH "
            "or configure component_commands." + component)
    if os.name == "nt" and component in MODULE_MAP and executable.lower().endswith(".exe"):
        # Windows 11 Smart App Control blocks unsigned pip/uv console-script `.exe`
        # trampolines (`WinError 4551`); dispatch through the signed Python binary.
        return [adapter["python"], "-I", "-X", "utf8", "-m", MODULE_MAP[component]]
    return [executable]


def run(command_argv, *, timeout, max_output, cancel, cwd=None, extra_env=None):
    """Use the runtime's owned process tree; temporary output keeps memory bounded."""
    from remote_dev.core.local_process import OwnedProcess

    deadline = time.monotonic() + timeout
    environment = {"MINDIE_DIAGNOSTICS_CONFIG": str(paths.diagnostics_config()), "PYTHONPATH": ""}
    if os.environ.get("MINDIE_DIAGNOSTICS_ROOT"):
        environment["MINDIE_DIAGNOSTICS_ROOT"] = os.environ["MINDIE_DIAGNOSTICS_ROOT"]
    if isinstance(extra_env, dict):
        environment.update(extra_env)
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        status = "completed"
        cleanup_error = None
        owner = OwnedProcess(command_argv, stdin=subprocess.DEVNULL,
                             stdout=stdout, stderr=stderr, env=environment, cwd=cwd)
        process = owner.process
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    status = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                # A presentation budget truncates the reply, not the CLI's business outcome.
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > CAPTURE_LIMIT_BYTES:
                    status = "output_limit"
                    break
                try:
                    process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                owner.stop(timeout=5.0)
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
        sizes = [os.fstat(stream.fileno()).st_size for stream in (stdout, stderr)]
        truncated = sum(sizes) > max_output
        # Reserve space for stderr so a large report cannot hide a CLI error.
        err_budget = min(sizes[1], max_output // 2)
        out_budget = min(sizes[0], max_output - err_budget)
        err_budget = min(sizes[1], max_output - out_budget)
        stdout.seek(0)
        stderr.seek(0)
        result = {
            "status": status, "exit_code": process.returncode,
            "stdout": stdout.read(out_budget).decode("utf-8", errors="replace"),
            "stderr": stderr.read(err_budget).decode("utf-8", errors="replace"),
            "output_truncated": truncated,
        }
        if cleanup_error:
            result["cleanup_error"] = cleanup_error
        if status != "completed":
            result["automatic_retry"] = False
        return result


def response(value, *, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            "structuredContent": value, "isError": is_error}


def execute(name, arguments, *, cancel=None):
    definitions = {tool["name"]: tool for tool in catalog()}
    if name not in definitions:
        raise ValueError("unknown deterministic component tool: " + str(name))
    schema = definitions[name]["inputSchema"]
    if unexpected := set(arguments) - schema["properties"].keys():
        raise ValueError("unexpected tool arguments: " + ", ".join(sorted(unexpected)))
    if missing := set(schema.get("required", [])) - arguments.keys():
        raise ValueError("missing tool arguments: " + ", ".join(sorted(missing)))
    if cancel is not None and cancel.is_set():
        return response({"status": "cancelled", "execution": "not_started"}, is_error=True)
    if name == "mindie_status" and not paths.config_file().is_file():
        return response({"status": "not_configured"})
    adapter = adapter_config()
    if name == "mindie_status":
        return response({"status": "configured", **{
            key: adapter[key] for key in ("version", "python", "venv_dir", "state_dir")
            if key in adapter}})
    if name in DESCRIPTIONS:
        component, argv = name, arguments["argv"]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError("argv must be an array of CLI arguments")
    elif name == "diagnostics_status":
        component, argv = "diagnostics", ["reporting", "status"]
        if arguments.get("config") is not None:
            argv += ["--config", arguments["config"]]
    else:
        root_val = arguments.get("root")
        if root_val is None:
            explicit_root = os.environ.get("MINDIE_DIAGNOSTICS_ROOT")
            if explicit_root:
                root_val = str(Path(explicit_root).expanduser())
            elif os.name == "nt" and not os.environ.get("XDG_STATE_HOME") and os.environ.get("LOCALAPPDATA"):
                root_val = str(Path(os.environ["LOCALAPPDATA"]) / "mindie" / "diagnostics")
            elif os.environ.get("XDG_STATE_HOME"):
                root_val = str(Path(os.environ["XDG_STATE_HOME"]) / "mindie" / "diagnostics")
            else:
                root_val = str(Path.home() / ".local" / "state" / "mindie" / "diagnostics")
        component, argv = "diagnostics", ["bundle", "--root", root_val]
        op_id = arguments.get("operation_id") if arguments.get("operation_id") is not None else arguments.get("incident_id")
        if op_id is not None:
            argv += ["--operation-id", op_id]
        if arguments.get("output") is not None:
            argv += ["--output", arguments["output"]]
    budget, milliseconds = arguments.get("max_output_tokens", 16384), arguments.get("timeout_ms", 120000)
    if type(budget) is not int or not 1 <= budget <= 262144:
        raise ValueError("max_output_tokens must be between 1 and 262144")
    if type(milliseconds) is not int or not 1 <= milliseconds <= 600000:
        raise ValueError("timeout_ms must be between 1 and 600000")
    cwd = arguments.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute()):
        raise ValueError("cwd must be an absolute path")
    cmd_prefix = command(adapter, component)
    invocation_token = secrets.token_hex(16) if name in DESCRIPTIONS else None
    extra_env = {"MINDIE_DIAGNOSTICS_INVOCATION_TOKEN": invocation_token} if invocation_token is not None else None
    result = run(cmd_prefix + argv, timeout=milliseconds / 1000,
                 max_output=budget * 4, cancel=cancel, cwd=cwd, extra_env=extra_env)
    result["component"] = component
    if not result["output_truncated"]:
        try:
            result["result"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    if name in DESCRIPTIONS and result["status"] == "completed" and result["exit_code"] not in (0, 2, None):
        import dsh_diagnostics
        diag = dsh_diagnostics.record_component_crash(
            name, argv, result["exit_code"], result["stderr"],
            parsed_result=result.get("result"), executable=cmd_prefix[0],
            invocation_token=invocation_token,
        )
        if diag is not None:
            result["diagnostic"] = diag
    return response(result, is_error=result["status"] != "completed" or result["exit_code"] != 0)

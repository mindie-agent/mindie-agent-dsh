"""[Role] Adapt installed deterministic component CLIs to DSH MCP calls.
[Invariants] Discovery is static; execution uses argv without a shell or knowledge activation.
[Timeouts & Locks] One owned child with a caller deadline, five-second cleanup and <=1 MiB output.
[Side-Effect Policy] Only explicit calls run components; local diagnostics never uploads a bundle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time

import mindie_paths as paths


CAPTURE_LIMIT_BYTES = 1024 * 1024


DESCRIPTIONS = {
    "profiler": (
        "Inspect and analyze Ascend profiling captures with mindie-profiler. "
        "Pass CLI argv, e.g. ['inventory', '--root', '/capture'] or "
        "['analyze', '--root', '/capture', '--full-range']. Supports correlate, "
        "compare and sql; use ['--help'] for the installed parser. "
        "Use --output for large reports. Device capture requires an appropriate NPU lease."
    ),
    "sim": (
        "Calculate operators, graphs/models and serving constraints from explicit "
        "input files with mindie-sim. Pass CLI argv; use ['--help'] and the "
        "installed subcommand's --help for its parser. Partial studies "
        "keep their report and nonzero exit. Predictions are not measured accuracy. "
        "Check CPU/RAM needs and use remote-dev when local resources are insufficient."
    ),
    "coordinator": (
        "Manage host inventory, probe CPU/RAM/NPU capacity and acquire, inspect or "
        "release device/port leases with mindie-coordinator. Pass CLI argv, e.g. "
        "['hosts', '--action', 'list']; use ['--help'] for the installed parser. "
        "Preserve lease IDs and never resubmit an uncertain external mutation."
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
         {"root": {"type": "string"}, "operation_id": {"type": "string"},
          "output": {"type": "string"}}, ["root"]),
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
        return [adapter["python"], "-I", "-m", "mindie_diagnostics.cli"]
    executable_name = "mindie-" + component
    executable = shutil.which(executable_name, path=str(Path(adapter["python"]).parent))
    if executable is None and Path(adapter["python"]).absolute() == Path(sys.executable).absolute():
        executable = shutil.which(executable_name, path=sysconfig.get_path("scripts"))
    executable = executable or shutil.which(executable_name)
    if executable is None:
        raise FileNotFoundError(
            executable_name + " is not installed; install it in the runtime/on PATH "
            "or configure component_commands." + component)
    return [executable]


def run(command_argv, *, timeout, max_output, cancel, cwd=None):
    """Use the runtime's owned process tree; temporary output keeps memory bounded."""
    from remote_dev.core.local_process import OwnedProcess

    deadline = time.monotonic() + timeout
    environment = {"MINDIE_DIAGNOSTICS_CONFIG": str(paths.diagnostics_config()), "PYTHONPATH": ""}
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
        component, argv = "diagnostics", ["bundle", "--root", arguments["root"]]
        for key in ("operation_id", "output"):
            if arguments.get(key) is not None:
                argv += ["--" + key.replace("_", "-"), arguments[key]]
    budget, milliseconds = arguments.get("max_output_tokens", 16384), arguments.get("timeout_ms", 120000)
    if type(budget) is not int or not 1 <= budget <= 262144:
        raise ValueError("max_output_tokens must be between 1 and 262144")
    if type(milliseconds) is not int or not 1 <= milliseconds <= 600000:
        raise ValueError("timeout_ms must be between 1 and 600000")
    cwd = arguments.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute()):
        raise ValueError("cwd must be an absolute path")
    result = run(command(adapter, component) + argv, timeout=milliseconds / 1000,
                 max_output=budget * 4, cancel=cancel, cwd=cwd)
    result["component"] = component
    if not result["output_truncated"]:
        try:
            result["result"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return response(result, is_error=result["status"] != "completed" or result["exit_code"] != 0)

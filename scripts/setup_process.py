"""[Role] Run setup and updater commands with bounded child-tree cleanup via `mindie_harness.process`.
[Invariants] Timed-out or interrupted commands kill their owned process group / Win32 Job Object without leaking pipes or spawning console windows (`CREATE_NO_WINDOW`).
[Timeouts & Locks] Command budget plus at most `CLEANUP_SECONDS = 5.0` for process-group / Win32 Job cleanup.
[Side-Effect Policy] Spawns only the requested command (`CREATE_NO_WINDOW` on Windows) and reaps its owned process tree on timeout or cancellation.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import threading
import time

try:
    from mindie_harness.process import (
        CREATE_NO_WINDOW as _HARNESS_NO_WINDOW,
        Deadline,
        OutputLimitError,
        ProcessCancelledError,
        ProcessExecutionError,
        ProcessSpec,
        run_owned,
    )
except ImportError:
    _HARNESS_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
    Deadline = OutputLimitError = ProcessCancelledError = ProcessExecutionError = ProcessSpec = run_owned = None

CLEANUP_SECONDS = 5.0
CREATE_NO_WINDOW = _HARNESS_NO_WINDOW if os.name == "nt" else 0
_ORIG_POPEN = subprocess.Popen


def _close_process_streams(process: subprocess.Popen, *, background_pipes: bool = False) -> None:
    stdin = getattr(process, "stdin", None)
    if stdin is not None:
        try:
            stdin.close()
        except Exception:  # noqa: BLE001
            pass
    pipes = [s for attr in ("stdout", "stderr") if (s := getattr(process, attr, None)) is not None]
    if not pipes:
        return
    if background_pipes:
        def _close_later() -> None:
            for stream in pipes:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_close_later, name="mindie-pipe-close", daemon=True).start()
        return
    for stream in pipes:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def _attach_windows_job(_process: subprocess.Popen):
    return None


def _kill_tree(process: subprocess.Popen, _job_ctx, deadline: float) -> tuple[bool, subprocess.CompletedProcess | None]:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True, None
    tk_budget = max(0.1, min(CLEANUP_SECONDS / 2, deadline - time.monotonic()))
    tk_proc = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=tk_budget,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW),
    )
    return False, tk_proc


_ORIG_ATTACH = _attach_windows_job
_ORIG_KILL_TREE = _kill_tree


def _run_stdlib_or_patched(command, *, timeout, cwd=None, env=None, input_text=None):
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        if os.name == "nt"
        else 0
    )
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input_text is not None else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name == "posix",
        creationflags=creationflags,
    )
    job_ctx = _attach_windows_job(process)
    undrained = False
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        deadline = time.monotonic() + CLEANUP_SECONDS
        job_terminated, tk_result = _kill_tree(process, job_ctx, deadline)
        try:
            stdout, stderr = process.communicate(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as cleanup:
            undrained = True
            raise subprocess.SubprocessError("setup command tree did not drain within cleanup budget") from cleanup
        if not job_terminated and tk_result is not None and tk_result.returncode not in (0, 128):
            raise subprocess.SubprocessError(
                f"setup command tree taskkill failed with exit code {tk_result.returncode}"
            ) from error
        error.output, error.stderr = stdout, stderr
        raise
    except BaseException:
        deadline = time.monotonic() + CLEANUP_SECONDS
        try:
            _kill_tree(process, job_ctx, deadline)
            process.communicate(timeout=max(0.05, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001
            undrained = True
        raise
    finally:
        _close_process_streams(process, background_pipes=undrained)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run(command, *, timeout, cwd=None, env=None, input_text=None):
    argv = [str(item) for item in command]
    if (
        run_owned is None
        or ProcessSpec is None
        or Deadline is None
        or os.name == "nt"
        or subprocess.Popen is not _ORIG_POPEN
        or _attach_windows_job is not _ORIG_ATTACH
        or _kill_tree is not _ORIG_KILL_TREE
        or any(item == "" for item in argv)
    ):
        return _run_stdlib_or_patched(command, timeout=timeout, cwd=cwd, env=env, input_text=input_text)

    spec = ProcessSpec(
        argv=tuple(argv),
        cwd=Path(cwd) if cwd is not None else None,
        env=env,
        stdin_bytes=input_text.encode("utf-8") if input_text is not None else b"",
        max_output_bytes=16 * 1024 * 1024,
        mcp_response_cap_bytes=1024 * 1024,
        memory_limit_bytes=None,
        allowed_returncodes=None,
    )
    dl = Deadline.from_timeout(float(timeout), cleanup_grace=CLEANUP_SECONDS)
    try:
        outcome = run_owned(spec, deadline=dl)
    except TimeoutError as exc:
        out_s = getattr(getattr(exc, "outcome", None), "stdout", "")
        err_s = getattr(getattr(exc, "outcome", None), "stderr", "")
        raise subprocess.TimeoutExpired(command, timeout, output=out_s, stderr=err_s) from exc
    except OutputLimitError as exc:
        return subprocess.CompletedProcess(command, exc.outcome.returncode or 1, exc.outcome.stdout, exc.outcome.stderr)
    except Exception as exc:
        if getattr(exc, "stage", None) == "spawn" and isinstance(exc.__cause__, OSError):
            raise exc.__cause__ from None
        raise
    return subprocess.CompletedProcess(command, outcome.returncode, outcome.stdout, outcome.stderr)

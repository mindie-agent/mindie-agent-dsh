"""[Role] Run setup and updater commands with bounded child-tree cleanup.
[Invariants] A timed-out or interrupted installation cannot keep its owned build processes running or leak OS pipe/Job handles; output is decoded strictly as UTF-8 (`errors="replace"`).
[Timeouts & Locks] Command budget plus at most five seconds (`CLEANUP_SECONDS`) for Win32 Job Object / `taskkill` or POSIX process-group cleanup.
[Side-Effect Policy] Spawns only the requested command (`CREATE_NO_WINDOW` on Windows); kills its owned group/tree on timeout or cancellation.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

CLEANUP_SECONDS = 5.0


def _attach_windows_job(process: subprocess.Popen):
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        proc_handle = int(getattr(process, "_handle", 0) or 0)
        if not proc_handle or not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(proc_handle)):
            kernel32.CloseHandle(job)
            return None
        return (kernel32, job)
    except Exception:  # noqa: BLE001
        return None


def _terminate_windows_job(job_ctx, *, terminate: bool) -> None:
    if not job_ctx:
        return
    kernel32, job = job_ctx
    try:
        if terminate:
            kernel32.TerminateJobObject(job, 1)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            kernel32.CloseHandle(job)
        except Exception:  # noqa: BLE001
            pass


def _close_process_streams(process: subprocess.Popen) -> None:
    for attr in ("stdin", "stdout", "stderr"):
        stream = getattr(process, attr, None)
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass


def _kill_tree(process: subprocess.Popen, job_ctx, deadline: float) -> tuple[bool, subprocess.CompletedProcess | None]:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True, None
    job_terminated = False
    if job_ctx:
        kernel32, job = job_ctx
        try:
            job_terminated = bool(kernel32.TerminateJobObject(job, 1))
        except Exception:  # noqa: BLE001
            job_terminated = False
        finally:
            try:
                kernel32.CloseHandle(job)
            except Exception:  # noqa: BLE001
                pass
    tk_budget = max(0.1, min(CLEANUP_SECONDS / 2, deadline - time.monotonic()))
    tk_proc = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=tk_budget,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return job_terminated, tk_proc


def run(command, *, timeout, cwd=None, env=None, input_text=None):
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        deadline = time.monotonic() + CLEANUP_SECONDS
        job_terminated, tk_result = _kill_tree(process, job_ctx, deadline)
        job_ctx = None
        try:
            stdout, stderr = process.communicate(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as cleanup:
            _close_process_streams(process)
            raise subprocess.SubprocessError(
                "setup command tree did not drain within cleanup budget"
            ) from cleanup
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
            job_ctx = None
            process.communicate(timeout=max(0.05, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001
            pass
        finally:
            _close_process_streams(process)
        raise
    finally:
        if job_ctx is not None:
            _terminate_windows_job(job_ctx, terminate=False)
        _close_process_streams(process)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

"""[Role] Run setup and updater commands with bounded child-tree cleanup.
[Invariants] A timed-out installation cannot keep its owned build processes running.
[Timeouts & Locks] Command budget plus at most five seconds for tree cleanup.
[Side-Effect Policy] Spawns only the requested command; kills its owned group/tree on timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

CLEANUP_SECONDS = 5.0


def run(command, *, timeout, cwd=None, env=None, input_text=None):
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, stdin=subprocess.PIPE if input_text is not None else None,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        deadline = time.monotonic() + CLEANUP_SECONDS
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            # taskkill is available before the locked Python runtime exists.
            # Failure is visible; it must not be reported as completed cleanup.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=CLEANUP_SECONDS / 2, check=True,
            )
        try:
            stdout, stderr = process.communicate(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as cleanup:
            raise subprocess.SubprocessError(
                "setup command tree did not drain within cleanup budget"
            ) from cleanup
        error.output, error.stderr = stdout, stderr
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

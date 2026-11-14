"""Windows 11 (CP936 / UTF-8 BOM / Smart App Control) and WSL2 contract tests for DSH.

[Role]
Verifies Windows/WSL2 path canonicalization, WSL2 `/mnt/<drive>` PE/CRLF/cmd-shim
filtering, npm `%dp0%`/`%~dp0` `.cmd` unwrapping, Smart App Control `.exe` -> `python -m`
dispatch, pre-send vs post-send `dsh_organizer` transport honesty, Win32 archive
traversal rejection, and `<800`-line / top-30-line 4-section module header budgets.

[Invariants]
- Every runtime module stays `< 800` lines with `[Role]`, `[Invariants]`,
  `[Timeouts & Locks]`, and `[Side-Effect Policy]` in its top 30 lines.
- All temporary patches use `pytest.MonkeyPatch.context()` so autouse isolation
  stays intact through fixture teardown.

[Timeouts & Locks]
- Subprocess and loopback HTTP socket tests bound execution within `15s`.

[Side-Effect Policy]
- Writes only inside `tmp_path`; closes loopback sockets and child processes cleanly.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import component_tools  # noqa: E402
import dsh_diagnostics  # noqa: E402
import dsh_organizer  # noqa: E402
import dsh_transcript  # noqa: E402
import mindie_paths  # noqa: E402
import updater  # noqa: E402

REQUIRED_SECTIONS = (
    "[Role]",
    "[Invariants]",
    "[Timeouts & Locks]",
    "[Side-Effect Policy]",
)


def _nt_os():
    return SimpleNamespace(
        name="nt",
        environ=os.environ,
        pathsep=";",
        sep="\\",
        altsep="/",
        fspath=os.fspath,
        path=os.path,
    )


def test_all_runtime_modules_obey_line_cap_and_top_30_header() -> None:
    targets = sorted(SCRIPTS.glob("*.py")) + [
        ROOT / "paths.cjs",
        ROOT / "host.mjs",
        ROOT / "host3.mjs",
        SCRIPTS / "knowledge_tools.mjs",
    ]
    assert len(targets) >= 20
    for module_path in targets:
        lines = module_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) < 800, f"{module_path.name} exceeds 800 lines ({len(lines)})"
        head = "\n".join(lines[:30])
        for section in REQUIRED_SECTIONS:
            assert section in head, f"{module_path.name} missing {section} in top 30 lines"


def test_wsl_which_native_rejects_pe_crlf_and_npm_cmd_shims(tmp_path: Path) -> None:
    win_bin = tmp_path / "win_npm"
    linux_bin = tmp_path / "linux_bin"
    win_bin.mkdir()
    linux_bin.mkdir()

    # Case 1: extensionless LF npm cmd-shim invoking node.exe under /mnt/c/...
    npm_shim = win_bin / "dsh"
    npm_shim.write_bytes(
        b'#!/bin/sh\nbasedir=$(dirname "$(echo "$0" | sed -e \'s,\\\\,/,g\')")\n'
        b'exec "$basedir/node.exe" "$basedir/node_modules/@deepseek-ai/dsh/bin/dsh.mjs" "$@"\n'
    )
    npm_shim.chmod(0o755)
    # Case 2: PE binary without .exe suffix
    pe_shim = win_bin / "gh"
    pe_shim.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    pe_shim.chmod(0o755)
    # Case 3: CRLF script
    crlf_shim = win_bin / "uv"
    crlf_shim.write_bytes(b"#!/bin/sh\r\necho bad\r\n")
    crlf_shim.chmod(0o755)

    for tool in ("dsh", "gh", "uv"):
        real_tool = linux_bin / tool
        real_tool.write_bytes(b"#!/bin/sh\necho linux\n")
        real_tool.chmod(0o755)

    assert mindie_paths._is_wsl_windows_shim(f"/mnt/c/Users/u/AppData/Roaming/npm/dsh.cmd")
    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(mindie_paths, "_is_wsl", lambda: True)
        mp_ctx.setattr(mindie_paths, "_WSL_MOUNT_RE", mindie_paths.re.compile(r"^" + mindie_paths.re.escape(str(win_bin))))
        if os.name == "nt":
            mp_ctx.setattr(
                mindie_paths.shutil,
                "which",
                lambda cmd, path=None: next(
                    (str(Path(d) / cmd) for d in (path or "").split(os.pathsep) if d and (Path(d) / cmd).is_file()),
                    None,
                ),
            )
        search_path = f"{win_bin}{os.pathsep}{linux_bin}"
        for tool in ("dsh", "gh", "uv"):
            resolved = mindie_paths.which_native(tool, path=search_path)
            assert resolved == str(linux_bin / tool)


def test_unwrap_cli_argv_handles_npm_dp0_and_tilde_dp0_wrappers(tmp_path: Path) -> None:
    npm_dir = tmp_path / "npm"
    mod_dir = npm_dir / "node_modules" / "@deepseek-ai" / "dsh" / "bin"
    mod_dir.mkdir(parents=True)
    entry_mjs = mod_dir / "dsh.mjs"
    entry_mjs.write_text("// entry\n", encoding="utf-8")
    fake_node = npm_dir / "node.exe"
    fake_node.write_bytes(b"MZ\x90\x00")

    dp0_cmd = npm_dir / "dsh.cmd"
    dp0_cmd.write_text(
        '@ECHO off\r\nSET dp0=%~dp0\r\n"%_prog%"  "%dp0%\\node_modules\\@deepseek-ai\\dsh\\bin\\dsh.mjs" %*\r\n',
        encoding="utf-8",
    )
    tilde_cmd = npm_dir / "dsh2.cmd"
    tilde_cmd.write_text(
        '@ECHO off\r\n"%~dp0\\node.exe" "%~dp0\\node_modules\\@deepseek-ai\\dsh\\bin\\dsh.mjs" %*\r\n',
        encoding="utf-8",
    )

    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(mindie_paths, "os", _nt_os())
        mp_ctx.setattr(mindie_paths, "which_native", lambda name, path=None: str(fake_node) if name == "node" else None)
        argv1 = mindie_paths.unwrap_cli_argv(str(dp0_cmd))
        argv2 = mindie_paths.unwrap_cli_argv(str(tilde_cmd))
    assert argv1 == [str(fake_node), str(entry_mjs.resolve())]
    assert argv2 == [str(fake_node), str(entry_mjs.resolve())]


def test_component_tools_dispatches_windows_exe_via_python_module(tmp_path: Path) -> None:
    fake_py = tmp_path / "Scripts" / "python.exe"
    fake_py.parent.mkdir(parents=True)
    fake_py.write_bytes(b"MZ")
    adapter = {"python": str(fake_py)}

    expected_modules = {
        "profiler": "mindie_profiler",
        "sim": "mindie_sim",
        "coordinator": "mindie_coordinator.cli",
        "debugger": "mindie_debugger.cli",
    }
    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(component_tools, "os", _nt_os())
        for comp, mod_name in expected_modules.items():
            exe = fake_py.parent / f"mindie-{comp}.exe"
            exe.write_bytes(b"MZ")
            mp_ctx.setattr(component_tools.shutil, "which", lambda _name, path=None, _exe=str(exe): _exe)
            cmd = component_tools.command(adapter, comp)
            assert cmd == [str(fake_py), "-I", "-X", "utf8", "-m", mod_name]
            # Verify every mapped entrypoint has a runnable __main__ or main()
            proc = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-m", mod_name, "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            assert proc.returncode == 0, f"{mod_name} --help failed: {proc.stderr}"
            assert proc.stdout.strip(), f"{mod_name} --help produced empty stdout"


def test_organizer_distinguishes_presend_refusal_from_postsend_drop_and_bom(tmp_path: Path) -> None:
    from mindie_knowledge.graded import distill

    # Prepare a valid C6 request using a cross-platform no-op scanner
    if os.name == "nt":
        py_scan = tmp_path / "scan.py"
        py_scan.write_text("import sys; sys.stdin.buffer.read(); sys.stdout.write('[]')\n", encoding="utf-8")
        scanner = tmp_path / "scan.cmd"
        scanner.write_text(f'@"{sys.executable}" -I -X utf8 "{py_scan}" %*\n', encoding="utf-8")
    else:
        scanner = tmp_path / "scan"
        scanner.write_text("#!/bin/sh\ncat >/dev/null\nprintf '[]'\n", encoding="utf-8")
        scanner.chmod(0o755)

    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)

    # 1. Pre-send connection refusal on an unbound closed port -> failed / configuration / model_calls == 0
    sock_closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_closed.bind(("127.0.0.1", 0))
    closed_port = sock_closed.getsockname()[1]
    sock_closed.close()

    (state_dir / "summary-endpoint.json").write_text(
        json.dumps({
            "url": f"http://127.0.0.1:{closed_port}/summary",
            "token": "tok",
            "provider": "deepseek-official",
            "model": "deepseek-flash",
        }),
        encoding="utf-8",
    )
    env = {**os.environ, "MINDIE_DSH_STATE_DIR": str(state_dir)}
    ident = json.loads(
        subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / "dsh_organizer.py"), "--identity"],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        ).stdout
    )
    req = distill.with_worker_identity(
        distill.prepare_request(
            [{
                "kind": "experiment_ledger",
                "outcome": "verified",
                "source_locator": "experiment-ledger.jsonl",
                "content": "中文路径与昇腾算子观测验证",
                "evidence_refs": ["run-1", "run-2"],
                "repetitions": 2,
            }],
            session_digest="a" * 64,
            task_state="verified_milestone",
            redactor_executable=str(scanner),
            redaction_key=b"k" * 32,
        ),
        ident,
    )
    # Pipe UTF-8 BOM + Chinese JSON via raw bytes!
    bom_payload = b"\xef\xbb\xbf" + json.dumps(req, ensure_ascii=False).encode("utf-8")
    presend_proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "dsh_organizer.py")],
        input=bom_payload,
        env=env,
        capture_output=True,
        timeout=15,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    presend = json.loads(presend_proc.stdout.decode("utf-8"))
    assert presend["status"] == "failed"
    assert presend["error"] == "configuration"
    assert presend["model_calls"] == 0
    assert presend["billing_status"] == "not_called"

    # 2. Post-send socket drop after reading HTTP request -> outcome_unknown / model_calls == 1
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    drop_port = srv.getsockname()[1]

    def _drop_after_recv():
        try:
            conn, _ = srv.accept()
            conn.settimeout(5.0)
            conn.recv(4096)
            conn.close()
        finally:
            srv.close()

    dropper = threading.Thread(target=_drop_after_recv, daemon=True)
    dropper.start()
    (state_dir / "summary-endpoint.json").write_text(
        json.dumps({
            "url": f"http://127.0.0.1:{drop_port}/summary",
            "token": "tok",
            "provider": "deepseek-official",
            "model": "deepseek-flash",
        }),
        encoding="utf-8",
    )
    ident2 = json.loads(
        subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / "dsh_organizer.py"), "--identity"],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        ).stdout
    )
    req2 = distill.with_worker_identity(dict(req), ident2)
    postsend_proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "dsh_organizer.py")],
        input=json.dumps(req2, ensure_ascii=False).encode("utf-8"),
        env=env,
        capture_output=True,
        timeout=15,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    dropper.join(timeout=5.0)
    postsend = json.loads(postsend_proc.stdout.decode("utf-8"))
    assert postsend["status"] == "outcome_unknown"
    assert postsend["model_calls"] == 1
    assert postsend["billing_status"] == "unknown"


def test_unc_file_url_and_extended_length_transcript_identity(tmp_path: Path) -> None:
    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(dsh_diagnostics, "os", _nt_os())
        unc = dsh_diagnostics._file_url_to_path("file://wsl.localhost/Ubuntu/home/alice/repo")
        assert unc is not None
        assert str(unc) == r"\\wsl.localhost\Ubuntu\home\alice\repo"

    sample = tmp_path / "中文会话.jsonl"
    data = b'{"type":"session"}\n'
    sample.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    raw_ident = json.dumps({"dev": 1, "ino": 2, "anchor_len": len(data), "anchor_digest": digest})

    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(mindie_paths, "os", _nt_os())
        a = dsh_transcript.FileIdentity.unserialize(raw_ident, r"\\?\C:\Users\alice\session.jsonl")
        b = dsh_transcript.FileIdentity.unserialize(raw_ident, r"C:\Users\alice\session.jsonl")
    assert a is not None and b is not None
    assert a.path == b.path == r"C:\Users\alice\session.jsonl"


def test_updater_extract_rejects_windows_drive_and_backslash_traversal(tmp_path: Path) -> None:
    for bad_member in (r"package\..\..\evil.py", r"C:\Windows\evil.py", r"\abs\evil.py"):
        archive = tmp_path / f"bad-{abs(hash(bad_member))}.tgz"
        with tarfile.open(archive, "w:gz") as stream:
            payload = b"{}"
            info = tarfile.TarInfo(bad_member)
            info.size = len(payload)
            stream.addfile(info, io.BytesIO(payload))
        with pytest.raises(updater.UpdateError):
            updater.extract(archive, tmp_path / "out")


def test_setup_process_undrained_pipe_returns_within_cleanup_budget() -> None:
    import setup_process

    # Spawn a grandchild that inherits stdout and outlives a failed tree kill.
    cmd = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.8)'], "
            "close_fds=False); "
            "time.sleep(0.8)"
        ),
    ]
    with pytest.MonkeyPatch.context() as mp_ctx:
        mp_ctx.setattr(setup_process, "CLEANUP_SECONDS", 0.08)
        mp_ctx.setattr(setup_process, "_attach_windows_job", lambda _p: None)
        mp_ctx.setattr(setup_process, "_kill_tree", lambda _p, _j, _d: (False, None))
        started = time.monotonic()
        with pytest.raises(subprocess.SubprocessError, match="did not drain within cleanup budget"):
            setup_process.run(cmd, timeout=0.05)
        elapsed = time.monotonic() - started
    assert elapsed < 0.45, f"undrained pipe cleanup blocked for {elapsed:.3f}s"
    time.sleep(0.85)

"""[Role] GitHub CLI (`gh`) account and fork verification helper for DSH contribution setup.
[Invariants] Validates `owner/repo` and GitHub account syntax before invoking `gh`; uses `which_native("gh")` to avoid WSL2 `/mnt/<drive>` Windows shims and `WindowsApps` stubs, and runs with explicit UTF-8 output decoding and `CREATE_NO_WINDOW` on Windows.
[Timeouts & Locks] Each `gh` CLI invocation is bounded at `90.0s`; no background retries or persistent locks.
[Side-Effect Policy] Read-only GitHub API/CLI queries except `gh repo fork <repository> --clone=false` when no matching fork exists under the target account.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable

import mindie_paths as mp

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_STDLIB_WHICH = shutil.which


def run_gh(
    *arguments: str,
    error_cls: type[RuntimeError] = RuntimeError,
    os_mod: Any = os,
    shutil_mod: Any = shutil,
    subprocess_mod: Any = subprocess,
) -> str:
    executable = (
        shutil_mod.which("gh")
        if shutil_mod.which is not _STDLIB_WHICH
        else mp.which_native("gh")
    )
    if executable is None:
        raise error_cls("GitHub CLI (gh) is required to fork and publish community contributions")
    try:
        completed = subprocess_mod.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            creationflags=getattr(subprocess_mod, "CREATE_NO_WINDOW", 0) if os_mod.name == "nt" else 0,
        )
    except TimeoutError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise error_cls(f"gh could not run: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise error_cls("gh failed: " + (detail[-1][:300] if detail else "unknown error"))
    return completed.stdout.strip()


def github_account(*, gh_fn: Callable[..., str], error_cls: type[RuntimeError] = RuntimeError) -> str:
    value = gh_fn("api", "user", "--jq", ".login").strip()
    if not value or not ACCOUNT_RE.fullmatch(value):
        raise error_cls("GitHub CLI did not return a usable account; pass account explicitly")
    return value


def existing_fork(
    repository: str,
    account: str,
    fork: str | None = None,
    *,
    gh_fn: Callable[..., str],
    error_cls: type[RuntimeError] = RuntimeError,
) -> str | None:
    if not ACCOUNT_RE.fullmatch(account):
        raise error_cls("account must be a public GitHub account name")
    repo_name = repository.split("/", 1)[1] if "/" in repository else repository
    candidates = [f"{account}/{repo_name}"]
    if fork and fork not in candidates:
        if not REPO_RE.fullmatch(fork):
            raise error_cls("fork must be owner/repo")
        candidates.insert(0, fork)
    for candidate in candidates:
        try:
            direct = json.loads(gh_fn("api", f"repos/{candidate}"))
        except (error_cls, ValueError):
            direct = None
        if isinstance(direct, dict):
            parent = direct.get("parent") if isinstance(direct.get("parent"), dict) else {}
            parent_name = parent.get("full_name") or parent.get("nameWithOwner")
            full_name = direct.get("full_name") or direct.get("nameWithOwner") or candidate
            if direct.get("fork") is True and parent_name == repository and isinstance(full_name, str) and full_name.count("/") == 1:
                return full_name
            if fork and candidate == fork and (direct.get("fork") is False or (parent_name and parent_name != repository)):
                raise error_cls("fork does not match parent repository")
    try:
        values = json.loads(gh_fn("repo", "list", account, "--fork", "--limit", "200", "--json", "nameWithOwner,parent"))
    except (error_cls, ValueError):
        return None
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict) or not isinstance(parent := item.get("parent"), dict):
            continue
        owner = parent.get("owner") if isinstance(parent.get("owner"), dict) else {}
        parent_name = parent.get("nameWithOwner") or (
            f"{owner['login']}/{parent['name']}" if isinstance(owner.get("login"), str) and isinstance(parent.get("name"), str) else None
        )
        name = item.get("nameWithOwner")
        if parent_name == repository and isinstance(name, str) and name.count("/") == 1:
            return name
    return None


def ensure_fork(
    repository: str,
    fork: str | None,
    account: str,
    *,
    gh_fn: Callable[..., str],
    existing_fork_fn: Callable[[str, str, str | None], str | None] | None = None,
    error_cls: type[RuntimeError] = RuntimeError,
) -> str:
    if not ACCOUNT_RE.fullmatch(account):
        raise error_cls("account must be a public GitHub account name")
    if fork is not None:
        if not isinstance(fork, str) or not REPO_RE.fullmatch(fork):
            raise error_cls("fork must be owner/repo")
        if fork.split("/", 1)[0] != account:
            raise error_cls("fork must belong to the public GitHub account")
    check_existing = existing_fork_fn or (
        lambda repo, acct, fk: existing_fork(repo, acct, fk, gh_fn=gh_fn, error_cls=error_cls)
    )
    if existing := check_existing(repository, account, fork):
        return existing
    if fork:
        try:
            gh_fn("repo", "view", fork, "--json", "nameWithOwner", "--jq", ".nameWithOwner")
            return fork
        except error_cls:
            if mp.which_native("gh") is None:
                return fork
    gh_fn("repo", "fork", repository, "--clone=false")
    if existing := check_existing(repository, account, fork):
        return existing
    if fork:
        try:
            gh_fn("repo", "view", fork, "--json", "nameWithOwner", "--jq", ".nameWithOwner")
            return fork
        except error_cls:
            pass
    raise error_cls("GitHub did not expose the fork after the fork request")

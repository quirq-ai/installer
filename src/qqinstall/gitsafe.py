"""Every git call qqinstall makes goes through `run`, so they all get the same hardening.

Replace refs are ignored, so a commit is always its real tree; fsmonitor and hooks are off. git
still trusts a checkout's own .git (its filters, excludes and core.fileMode can run code or hide
changes, grafts and a commit-graph can rewrite history), so questions about history (`on_branch`)
are asked of a scratch repo fetched fresh from the remote, never of the checkout. Inherited GIT_*
variables are dropped so git looks where it is told, not wherever GIT_DIR points, except the CA
settings some networks need. A transfer slower than 1 KB/s for a minute is abandoned, and no one
command may run longer than TIMEOUT.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile

from qqinstall.errors import InstallerError

SAFE = ["--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "advice.detachedHead=false", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=60"]
ENV_KEPT = {"GIT_SSL_CAINFO", "GIT_SSL_CAPATH"}
TIMEOUT = 900  # seconds; a clone of xo-space takes well under a minute
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def run(checkout: str | None, *argv: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in ENV_KEPT}
    env["GIT_TERMINAL_PROMPT"] = "0"
    where = ["-C", checkout] if checkout else []
    try:
        return subprocess.run(["git", *SAFE, *where, *argv], capture_output=True, text=True, env=env,
                              timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise InstallerError(f"git {argv[0]} took longer than {TIMEOUT}s") from None
    except OSError as e:
        raise InstallerError(f"could not run git: {e}") from None


def check_branch_name(branch: str) -> str:
    if not BRANCH.fullmatch(branch) or ".." in branch or branch.endswith((".", "/", ".lock")):
        raise InstallerError(f"{branch!r} is not a branch name")
    return branch


def on_branch(commit: str, remote: str, branch: str) -> bool:
    """Is `commit` the tip of `remote`'s `branch`, or an ancestor of it? Asked of a scratch repo
    fetched fresh (blobs left out), so nothing in a local .git can change the answer."""
    check_branch_name(branch)
    with tempfile.TemporaryDirectory() as tmp:
        if run(tmp, "init", "-q").returncode:
            raise InstallerError("could not create a scratch git repository")
        if run(tmp, "fetch", "--quiet", "--no-tags", "--filter=blob:none", remote,
               f"+refs/heads/{branch}:refs/qq/tip").returncode:
            raise InstallerError(f"could not read the {branch} branch of {remote}")
        # 0 = an ancestor; 1 = not; anything else (128: the commit is not in the branch's history at
        # all, so the fetch never brought it) also means it is not on the branch.
        return run(tmp, "merge-base", "--is-ancestor", commit, "refs/qq/tip").returncode == 0

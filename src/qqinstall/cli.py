"""qqinstall: resolve a channel, put a checkout on it, or check that a checkout is it.

    qqinstall resolve  --repo NAME --channel canary [--source URL|PATH | --at SHA] [--format text|json|env]
    qqinstall checkout --repo NAME --channel canary --remote URL --dest DIR [--branch main] [--source ... | --at ...]
    qqinstall verify   --repo NAME --channel canary --checkout DIR [--remote URL] [--source ... | --at ...]
    qqinstall show     [--source ... | --at ...]

`checkout` clones (or updates) DIR, detaches it at exactly the commit the manifest names (which must
be on the remote's --branch), and then verifies it, so nothing runs before the check: run DIR's own
install only after it exits 0. It never asks the remote what a tag or channel name means.

Exit codes (decide on these, never on the output text). Treat anything but 0 as "not verified":
Python itself exits 1 if it cannot start (a broken install), which reads like a mismatch.
    0  resolved / the checkout is exactly the channel's commit
    1  checkout, verify: the checkout is not the channel's commit, or has local changes
    2  error: bad arguments, an unreadable or invalid manifest, a broken checkout or remote
    3  not published yet: no manifest, or it does not name that repo and channel
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from qqinstall import manifest
from qqinstall.manifest import Channel, InstallerError, NotPublished

OK, MISMATCH, ERROR, NOT_PUBLISHED = 0, 1, 2, 3


def _source(args) -> str:
    if args.at and args.source:
        raise InstallerError("pass --source or --at, not both")
    if args.at:
        manifest.check_on_release_state(args.at)
        return manifest.source_at(args.at)
    return args.source or manifest.DEFAULT_SOURCE


# Replace refs are ignored, so a commit is always its real tree; fsmonitor and hooks are off. git
# still trusts the checkout's own .git (its filters, excludes and core.fileMode can run code or hide
# changes), so this checks an install its operator controls, not a hostile tree. Inherited GIT_*
# variables are dropped so git looks at the checkout, not wherever GIT_DIR points, except the CA
# settings some networks need. A transfer slower than 1 KB/s for a minute is abandoned.
GIT_SAFE = ["--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "advice.detachedHead=false", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=60"]
GIT_ENV_KEPT = {"GIT_SSL_CAINFO", "GIT_SSL_CAPATH"}
GIT_TIMEOUT = 900  # seconds for any one git command; a clone of xo-space takes well under a minute
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def _git(checkout: str | None, *argv: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in GIT_ENV_KEPT}
    env["GIT_TERMINAL_PROMPT"] = "0"
    where = ["-C", checkout] if checkout else []
    try:
        return subprocess.run(["git", *GIT_SAFE, *where, *argv], capture_output=True, text=True, env=env,
                              timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise InstallerError(f"git {argv[0]} took longer than {GIT_TIMEOUT}s") from None
    except OSError as e:
        raise InstallerError(f"could not run git: {e}") from None


def _top_level(checkout: str) -> None:
    top = _git(checkout, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise InstallerError(f"{checkout} is not a git checkout")
    if Path(top.stdout.strip()).resolve() != Path(checkout).resolve():
        raise InstallerError(f"{checkout} is inside a checkout, not the top of one ({top.stdout.strip()})")


def _origin_is(checkout: str, remote: str) -> None:
    # The URL as stored, before the user's url.insteadOf rewrites (which `remote get-url` applies).
    url = _git(checkout, "config", "--local", "--get", "remote.origin.url")
    if url.returncode != 0 or url.stdout.strip() != remote:
        raise InstallerError(f"{checkout}'s origin is {url.stdout.strip() or '(none)'}, not {remote}")


def _local_changes(checkout: str) -> bool:
    # Untracked files count (an install is the tree, not just the tracked files); entries marked
    # assume-unchanged or skip-worktree (`ls-files -v` lowercase or S) could hide edits, so they count too.
    status = _git(checkout, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
    flags = _git(checkout, "ls-files", "-v")
    if status.returncode != 0 or flags.returncode != 0:
        raise InstallerError(f"could not read the status of {checkout}")
    hidden = [ln for ln in flags.stdout.splitlines() if ln[:1].islower() or ln[:1] == "S"]
    return bool(status.stdout.strip() or hidden)


def _escaping_links(checkout: str) -> list[str]:
    """Tracked symlinks that resolve outside the checkout (install.sh could be one)."""
    out = _git(checkout, "ls-files", "-s", "-z")
    if out.returncode != 0:
        raise InstallerError(f"could not list the files of {checkout}")
    root = Path(checkout).resolve()
    bad = []
    for rec in out.stdout.split("\0"):
        if rec.startswith("120000 "):
            path = rec.split("\t", 1)[1]
            target = Path(os.path.realpath(root / path))
            if target != root and root not in target.parents:
                bad.append(path)
    return bad


def check(checkout: str, ch: Channel, remote: str | None = None) -> int:
    """OK if `checkout` is exactly `ch`'s commit with no local changes (and origin is `remote`)."""
    _top_level(checkout)
    if remote is not None:
        _origin_is(checkout, remote)
    head = _git(checkout, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if head.returncode != 0:
        raise InstallerError(f"{checkout} is a git checkout with no commit")
    got = head.stdout.strip()
    if got != ch.commit:
        print(f"{checkout} is at {got[:12]}, but {ch.repo} {ch.channel} names {ch.commit[:12]} "
              f"(generation {ch.generation})", file=sys.stderr)
        return MISMATCH
    if _local_changes(checkout):
        print(f"{checkout} is at {ch.repo} {ch.channel}'s commit {got[:12]} but has local changes "
              "(or files marked to hide them)", file=sys.stderr)
        return MISMATCH
    escaping = _escaping_links(checkout)
    if escaping:
        raise InstallerError(f"{checkout} has symlinks that point outside it: {', '.join(escaping[:5])}")
    print(f"ok: {checkout} is {ch.repo} {ch.channel} at {got[:12]} (generation {ch.generation})")
    return OK


def cmd_resolve(args) -> int:
    ch = manifest.resolve(_source(args), args.repo, args.channel)
    if args.format == "json":
        print(json.dumps(asdict(ch), sort_keys=True))
    elif args.format == "env":
        print(f"QQ_CHANNEL_COMMIT={ch.commit}\nQQ_CHANNEL_DIGEST={ch.digest}\nQQ_CHANNEL_GENERATION={ch.generation}")
    else:
        print(f"{ch.repo} {ch.channel}: commit {ch.commit} digest {ch.digest} (generation {ch.generation})")
    return OK


def _generation_key(ch: Channel) -> str:
    # Names never contain "/", so the subsection is unambiguous; git splits the key at the last dot.
    return f"qqinstall.{ch.repo}/{ch.channel}.generation"


def _never_backwards(checkout: str, ch: Channel) -> None:
    """Refuse a manifest older than the last one this checkout was put on (a replayed channels.json)."""
    seen = _git(checkout, "config", "--local", "--get", _generation_key(ch))
    if seen.returncode == 1:
        return  # never recorded: a checkout made before this check, or by hand
    try:
        last = int(seen.stdout.strip()) if seen.returncode == 0 else None
    except ValueError:
        last = None
    if last is None:
        raise InstallerError(f"could not read {_generation_key(ch)} in {checkout}")
    if ch.generation < last:
        raise InstallerError(f"{ch.repo} {ch.channel} manifest is generation {ch.generation}, older than "
                             f"generation {last} already installed in {checkout}; refusing to go back")


def _clear(dest: Path, keep_dir: bool) -> None:
    """Undo a first checkout that failed, so the next run starts fresh instead of finding a half-made clone."""
    if not dest.is_dir() or dest.is_symlink():
        return
    if not keep_dir:
        shutil.rmtree(dest, ignore_errors=True)
        return
    for child in dest.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def cmd_checkout(args) -> int:
    ch = manifest.resolve(_source(args), args.repo, args.channel)
    if not BRANCH.fullmatch(args.branch) or ".." in args.branch:
        raise InstallerError(f"--branch {args.branch!r} is not a branch name")
    dest = Path(args.dest)
    missing = not os.path.lexists(dest)
    empty = not missing and dest.is_dir() and not dest.is_symlink() and not any(dest.iterdir())
    if not (missing or empty):
        _top_level(str(dest))
        _origin_is(str(dest), args.remote)
        _never_backwards(str(dest), ch)
        if _local_changes(str(dest)):
            print(f"{dest} has local changes; leaving it as it is", file=sys.stderr)
            return MISMATCH
        return _put_on(dest, ch, args)
    try:
        rc = _put_on(dest, ch, args, clone=True)
    except BaseException:
        _clear(dest, keep_dir=empty)
        raise
    if rc != OK:
        _clear(dest, keep_dir=empty)
    return rc


def _put_on(dest: Path, ch: Channel, args, clone: bool = False) -> int:
    d = str(dest)
    if clone:
        if _git(None, "clone", "--quiet", "--no-checkout", "--no-tags", "--", args.remote, d).returncode:
            raise InstallerError(f"could not clone {args.remote} into {dest}")
    elif _git(d, "fetch", "--quiet", "--no-tags", "origin").returncode:
        raise InstallerError(f"could not fetch from {args.remote}")
    # The commit by its id, never a branch or tag name the remote could point elsewhere.
    want = f"{ch.commit}^{{commit}}"
    if _git(d, "cat-file", "-e", want).returncode:
        _git(d, "fetch", "--quiet", "--no-tags", "origin", ch.commit)
        if _git(d, "cat-file", "-e", want).returncode:
            raise InstallerError(f"{args.remote} does not have {ch.repo} {ch.channel}'s commit {ch.commit[:12]}")
    # ...and only a commit on the reviewed branch, so a forged manifest cannot name any object the
    # remote serves by id (an unmerged branch, a fork's commit).
    tip = "refs/qqinstall/branch"
    if _git(d, "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/{args.branch}:{tip}").returncode:
        raise InstallerError(f"could not fetch {args.branch} from {args.remote}")
    on = _git(d, "merge-base", "--is-ancestor", ch.commit, tip).returncode
    if on == 1:
        raise InstallerError(f"{ch.repo} {ch.channel}'s commit {ch.commit[:12]} is not on {args.remote} "
                             f"{args.branch}; refusing it")
    if on != 0:
        raise InstallerError(f"could not check that {ch.commit[:12]} is on {args.branch}")
    if _git(d, "checkout", "--quiet", "--detach", ch.commit).returncode:
        raise InstallerError(f"could not check out {ch.commit[:12]} in {dest}")
    rc = check(d, ch, args.remote)
    if rc == OK and _git(d, "config", "--local", _generation_key(ch), str(ch.generation)).returncode:
        raise InstallerError(f"could not record generation {ch.generation} in {dest}")
    return rc


def cmd_verify(args) -> int:
    ch = manifest.resolve(_source(args), args.repo, args.channel)
    # TODO(expert): resolve/verify/show keep no state, so they cannot tell a replayed (older)
    # channels.json from the current one; only `checkout` refuses to go back. Decide whether
    # verify should also read the checkout's recorded generation.
    return check(args.checkout, ch, args.remote)


def cmd_show(args) -> int:
    chans = manifest.load(_source(args))
    print(json.dumps({f"{r} {c}": asdict(ch) for (r, c), ch in sorted(chans.items())}, indent=2, sort_keys=True))
    return OK


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="qqinstall", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def src(p):
        p.add_argument("--source", help=f"manifest URL (https) or path; default {manifest.DEFAULT_SOURCE}")
        p.add_argument("--at", metavar="SHA", help="read the manifest at this commit of release's release-state "
                                                   "branch (checked to be on it)")

    def channel(p):
        p.add_argument("--repo", required=True)
        p.add_argument("--channel", required=True)

    r = sub.add_parser("resolve", help="print the commit and digest a channel names")
    channel(r)
    r.add_argument("--format", choices=["text", "json", "env"], default="text")
    src(r)
    r.set_defaults(fn=cmd_resolve)

    c = sub.add_parser("checkout", help="clone or update DIR to exactly the channel's commit, then verify it")
    channel(c)
    c.add_argument("--remote", required=True, help="the repo's git URL")
    c.add_argument("--dest", required=True, help="the checkout directory (created if missing or empty)")
    c.add_argument("--branch", default="main", help="the commit must be on this branch of --remote (default main)")
    src(c)
    c.set_defaults(fn=cmd_checkout)

    v = sub.add_parser("verify", help="check that a git checkout is exactly the channel's commit")
    channel(v)
    v.add_argument("--checkout", required=True)
    v.add_argument("--remote", help="also require the checkout's origin to be this URL")
    src(v)
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("show", help="print every channel the manifest names")
    src(s)
    s.set_defaults(fn=cmd_show)
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except NotPublished as e:
        print(f"not published yet: {e}", file=sys.stderr)
        return NOT_PUBLISHED
    except InstallerError as e:
        print(f"error: {e}", file=sys.stderr)
        return ERROR
    except Exception as e:   # never let an unexpected failure exit 1, which means "mismatch"
        print(f"error: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return ERROR

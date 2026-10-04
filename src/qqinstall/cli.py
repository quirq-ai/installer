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
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from qqinstall import gitsafe, manifest
from qqinstall.manifest import Channel, InstallerError, NotPublished

OK, MISMATCH, ERROR, NOT_PUBLISHED = 0, 1, 2, 3


def _source(args) -> str:
    if args.at and args.source:
        raise InstallerError("pass --source or --at, not both")
    if args.at:
        manifest.check_on_release_state(args.at)
        return manifest.source_at(args.at)
    return args.source or manifest.DEFAULT_SOURCE


_git = gitsafe.run


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


def _bad_links(checkout: str, commit: str) -> list[str]:
    """Symlinks in `commit`'s tree that do not resolve to a path the tree itself has: anything
    outside the checkout, in .git, or in an untracked or ignored path (a venv, the server's state)
    is not what was verified. Read from the commit's objects, so it can run before the checkout
    moves; links are resolved by recreating the tree's directories, files (empty) and links in a
    scratch directory, so chains of links resolve exactly as they would in the checkout."""
    out = _git(checkout, "ls-tree", "-r", "-t", "-z", "--full-tree", commit)
    if out.returncode != 0:
        raise InstallerError(f"could not list the tree of {commit[:12]}")
    files, dirs, links = set(), set(), {}
    for rec in filter(None, out.stdout.split("\0")):
        meta, path = rec.split("\t", 1)
        mode, kind, oid = meta.split()
        if kind == "tree":
            dirs.add(path)
        elif mode == "120000":
            links[path] = oid
        elif kind == "blob":
            files.add(path)
        # a submodule (commit) is left out: its path is an empty directory in this checkout
    if not links:
        return []
    for path in [*dirs, *files, *links]:
        parts = path.split("/")
        if path.startswith("/") or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts):
            raise InstallerError(f"{commit[:12]} has a tree entry no checkout could hold: {path!r}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()

        def inside(p: Path) -> Path:
            # Every parent is a directory this function made, never a link, so nothing lands outside.
            if Path(os.path.realpath(p.parent)) != root and root not in Path(os.path.realpath(p.parent)).parents:
                raise InstallerError(f"{commit[:12]}: {p.relative_to(root)} would land outside the scratch tree")
            return p

        try:
            for d in sorted(dirs):
                inside(root / d).mkdir(exist_ok=True)
            for f in files:
                inside(root / f).touch(exist_ok=False)
            for path, oid in sorted(links.items()):
                target = _git(checkout, "cat-file", "blob", oid)
                if target.returncode != 0:
                    raise InstallerError(f"could not read the symlink {path} in {commit[:12]}")
                os.symlink(target.stdout, inside(root / path))
        except (OSError, ValueError) as e:
            raise InstallerError(f"could not recreate the tree of {commit[:12]}: {e}") from None
        bad = []
        for path in sorted(links):
            try:   # strict: every step must exist, as it would have to in the checkout
                real = Path(os.path.realpath(root / path, strict=True))
            except (OSError, RuntimeError):
                bad.append(path)
                continue
            rel = real.relative_to(root).as_posix() if real == root or root in real.parents else None
            if rel is None or (rel != "." and rel not in files and rel not in dirs) or rel in links:
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
    bad = _bad_links(checkout, got)
    if bad:
        raise InstallerError(f"{checkout} has symlinks to paths outside its tree: {', '.join(bad[:5])}")
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
    gitsafe.check_branch_name(args.branch)
    dest = Path(args.dest)
    missing = not os.path.lexists(dest)
    empty = not missing and dest.is_dir() and not dest.is_symlink() and not any(dest.iterdir())
    if not (missing or empty):
        _top_level(str(dest))
        _origin_is(str(dest), args.remote)
        if _git(str(dest), "rev-parse", "--is-shallow-repository").stdout.strip() != "false":
            raise InstallerError(f"{dest} is a shallow clone; qqinstall needs a full one: remove it and run again")
        _never_backwards(str(dest), ch)
        if _local_changes(str(dest)):
            print(f"{dest} has local changes; leaving it as it is", file=sys.stderr)
            return MISMATCH
        if _git(str(dest), "fetch", "--quiet", "--no-tags", "origin").returncode:
            raise InstallerError(f"could not fetch from {args.remote}")
        return _put_on(dest, ch, args)
    if _git(None, "clone", "--quiet", "--no-checkout", "--no-tags", "--", args.remote, str(dest)).returncode:
        raise InstallerError(f"could not clone {args.remote} into {dest}")
    # From here dest is this run's clone; if the rest fails, undo it so the next run starts fresh.
    try:
        rc = _put_on(dest, ch, args)
    except BaseException:
        _clear(dest, keep_dir=empty)
        raise
    if rc != OK:
        _clear(dest, keep_dir=empty)
    return rc


def _put_on(dest: Path, ch: Channel, args) -> int:
    d = str(dest)
    # The commit by its id, never a branch or tag name the remote could point elsewhere.
    want = f"{ch.commit}^{{commit}}"
    if _git(d, "cat-file", "-e", want).returncode:
        _git(d, "fetch", "--quiet", "--no-tags", "origin", ch.commit)
        if _git(d, "cat-file", "-e", want).returncode:
            raise InstallerError(f"{args.remote} does not have {ch.repo} {ch.channel}'s commit {ch.commit[:12]}")
    # ...and only a commit on the reviewed branch, so a forged manifest cannot name any object the
    # remote serves by id (an unmerged branch, a fork's commit). Asked of the remote, not of dest.
    if not gitsafe.on_branch(ch.commit, args.remote, args.branch):
        raise InstallerError(f"{ch.repo} {ch.channel}'s commit {ch.commit[:12]} is not on {args.remote} "
                             f"{args.branch}; refusing it")
    bad = _bad_links(d, ch.commit)
    if bad:
        raise InstallerError(f"{ch.repo} {ch.channel}'s commit {ch.commit[:12]} has symlinks to paths "
                             f"outside its tree: {', '.join(bad[:5])}; refusing it")
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

"""qqinstall: resolve a channel, or check that a checkout follows it.

    qqinstall resolve --repo NAME --channel canary [--source URL|PATH | --at SHA] [--format text|json|env]
    qqinstall verify  --repo NAME --channel canary --checkout DIR [--source ... | --at ...]
    qqinstall show    [--source ... | --at ...]

Exit codes (decide on these, never on the output text):
    0  resolved / the checkout is exactly the channel's commit
    1  verify: the checkout is not the channel's commit, or has local changes
    2  error: bad arguments, an unreadable or invalid manifest, a broken checkout
    3  not published yet: no manifest, or it does not name that repo and channel
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from qqinstall import manifest
from qqinstall.manifest import InstallerError, NotPublished

OK, MISMATCH, ERROR, NOT_PUBLISHED = 0, 1, 2, 3


def _source(args) -> str:
    if args.at and args.source:
        raise InstallerError("pass --source or --at, not both")
    return manifest.source_at(args.at) if args.at else (args.source or manifest.DEFAULT_SOURCE)


# Defence in depth only: fsmonitor and hooks off. verify still trusts the checkout's own .git
# (its filters, excludes and core.fileMode can run code or hide changes), so it checks an install
# its operator controls, not a hostile tree. Inherited GIT_* variables are dropped so git looks at
# `checkout`, not wherever GIT_DIR points.
GIT_SAFE = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


def _git(checkout: str, *argv: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run(["git", *GIT_SAFE, "-C", checkout, *argv], capture_output=True, text=True, env=env)
    except OSError as e:
        raise InstallerError(f"could not run git: {e}") from None


def cmd_resolve(args) -> int:
    ch = manifest.resolve(_source(args), args.repo, args.channel)
    if args.format == "json":
        print(json.dumps(asdict(ch), sort_keys=True))
    elif args.format == "env":
        print(f"QQ_CHANNEL_COMMIT={ch.commit}\nQQ_CHANNEL_DIGEST={ch.digest}\nQQ_CHANNEL_GENERATION={ch.generation}")
    else:
        print(f"{ch.repo} {ch.channel}: commit {ch.commit} digest {ch.digest} (generation {ch.generation})")
    return OK


def cmd_verify(args) -> int:
    ch = manifest.resolve(_source(args), args.repo, args.channel)
    top = _git(args.checkout, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise InstallerError(f"{args.checkout} is not a git checkout")
    if Path(top.stdout.strip()).resolve() != Path(args.checkout).resolve():
        raise InstallerError(f"{args.checkout} is inside a checkout, not the top of one ({top.stdout.strip()})")
    head = _git(args.checkout, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if head.returncode != 0:
        raise InstallerError(f"{args.checkout} is a git checkout with no commit")
    # Untracked files count (an install is the tree, not just the tracked files); entries marked
    # assume-unchanged or skip-worktree (`ls-files -v` lowercase or S) could hide edits, so they count too.
    status = _git(args.checkout, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
    flags = _git(args.checkout, "ls-files", "-v")
    if status.returncode != 0 or flags.returncode != 0:
        raise InstallerError(f"could not read the status of {args.checkout}")
    hidden = [ln for ln in flags.stdout.splitlines() if ln[:1].islower() or ln[:1] == "S"]
    got = head.stdout.strip()
    if got != ch.commit:
        print(f"{args.checkout} is at {got[:12]}, but {ch.repo} {ch.channel} names {ch.commit[:12]} "
              f"(generation {ch.generation})", file=sys.stderr)
        return MISMATCH
    if status.stdout.strip() or hidden:
        print(f"{args.checkout} is at {ch.repo} {ch.channel}'s commit {got[:12]} but has local changes "
              "(or files marked to hide them)",
              file=sys.stderr)
        return MISMATCH
    print(f"ok: {args.checkout} is {ch.repo} {ch.channel} at {got[:12]} (generation {ch.generation})")
    return OK


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
        p.add_argument("--at", metavar="SHA", help="read the manifest at this release-state commit")

    r = sub.add_parser("resolve", help="print the commit and digest a channel names")
    r.add_argument("--repo", required=True)
    r.add_argument("--channel", required=True)
    r.add_argument("--format", choices=["text", "json", "env"], default="text")
    src(r)
    r.set_defaults(fn=cmd_resolve)

    v = sub.add_parser("verify", help="check that a git checkout is exactly the channel's commit")
    v.add_argument("--repo", required=True)
    v.add_argument("--channel", required=True)
    v.add_argument("--checkout", required=True)
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


if __name__ == "__main__":
    sys.exit(main())

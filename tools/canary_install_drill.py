"""V0-INS-02 done-when, offline half: a test install follows canary, checked before anything runs.

The documented flow (docs/xo-space-canary.md) is: `qqinstall checkout` clones or updates the
install, detaches it at exactly the commit channels.json names and verifies it; only then does the
operator run that checkout's own `./install.sh`, which runs a checkout in place and never fetches.

This drill runs that flow against a local stand-in for xo-space: a repo holding xo-space's real
`install.sh` (fetched at the commit pinned in pins.toml and checked against its sha256) beside a
`server.py` and `requirements.txt`. release's own executor (the pinned `contract` extra, `local`
backend) moves canary and writes channels.json. It checks:

1. a fresh `qqinstall checkout` lands on canary's commit 1 (exit 0), and the checkout's own
   install.sh would run it in place (`resolve_repo_dir` gives MANAGED_CHECKOUT=0, so no git runs);
2. canary is promoted to commit 2: the install is stale (`verify` exits 1) until `checkout` runs
   again, then it is commit 2;
3. a tag and a `refs/channels/canary` ref planted at an unreviewed commit change nothing: checkout
   still lands on the manifest's commit. For contrast it shows the earlier
   `QUIRQ_SOURCE_REF=channels/canary` flow (install.sh's own `fetch_repo`) does install the planted
   commit, which is why that flow is no longer documented;
4. canary is rolled back: `checkout` takes the install back to commit 1;
5. a checkout with local changes is left alone (exit 1).

What it does not cover: the rest of install.sh (uv, venv, the server), the bootstrap behind the
short URL, and GitHub itself (release's github backend, raw.githubusercontent.com).

    python tools/canary_install_drill.py --config .qq/infra-config [--install-sh PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from qqrelease import backends, channels, cli, config, executor
from qqrelease.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermetic import isolate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = "xo-space"     # the consumer under test; its name is also install.sh's checkout directory
CHANNEL = "canary"
REF = f"channels/{CHANNEL}"
GIT_ID = ["-c", "user.name=drill", "-c", "user.email=drill@example.invalid"]


def git(*args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def install_sh(path: str | None) -> bytes:
    pin = tomllib.loads((ROOT / "pins.toml").read_text())[REPO]
    if path:
        data = Path(path).read_bytes()
    else:
        url = f"https://raw.githubusercontent.com/quirq-ai/{REPO}/{pin['commit']}/install.sh"
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read(4 << 20)
    got = hashlib.sha256(data).hexdigest()
    if got != pin["install_sh_sha256"]:
        raise SystemExit(f"install.sh sha256 {got} is not the pinned {pin['install_sh_sha256']}")
    return data


def functions_only(script: bytes) -> str:
    """install.sh without its final `main "$@"`, as xo-space's own tests/install_sh_harness.sh does."""
    lines = script.decode().replace("\r", "").rstrip("\n").split("\n")
    if lines[-1].rstrip() != 'main "$@"':
        raise SystemExit(f"install.sh no longer ends in `main \"$@\"`; update the drill: {lines[-1]!r}")
    return "\n".join(lines[:-1]) + "\n"


def qqinstall(*argv) -> int:
    return subprocess.run([sys.executable, "-m", "qqinstall", *argv], capture_output=True, text=True).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--install-sh", help="a local copy of the pinned install.sh (default: fetch it)")
    args = ap.parse_args(argv)
    cfg = config.load(Path(args.config))
    lkgr = config.lkgr_ref(cfg)
    script = install_sh(args.install_sh)
    failures = []

    def check(what: str, ok: bool):
        print(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            failures.append(what)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        isolate(tmp)
        state = tmp / "state"
        state.mkdir()
        git("init", "-q", "-b", "release-state", cwd=state)
        store = Store(state)
        targets = tmp / "targets"
        upstream = targets / REPO
        upstream.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=upstream)
        (upstream / "install.sh").write_bytes(script)
        shas = []
        for i in range(2):
            (upstream / "server.py").write_text(f"# version {i + 1}\n")
            (upstream / "requirements.txt").write_text("")
            git("add", ".", cwd=upstream)
            git(*GIT_ID, "commit", "-q", "-m", f"commit {i + 1}", cwd=upstream)
            shas.append(git("rev-parse", "HEAD", cwd=upstream))
        git("checkout", "-q", "-b", "unreviewed", cwd=upstream)
        (upstream / "server.py").write_text("# unreviewed\n")
        git(*GIT_ID, "commit", "-q", "-am", "unreviewed", cwd=upstream)
        evil = git("rev-parse", "HEAD", cwd=upstream)
        git("checkout", "-q", "main", cwd=upstream)

        mirror = backends.load("local", target_root=targets)
        manifest = state / channels.MANIFEST
        remote = f"file://{upstream}"
        app = tmp / "test-env" / REPO
        app.parent.mkdir()
        sel = ["--repo", REPO, "--channel", CHANNEL, "--source", str(manifest)]

        def promote(sha: str):
            executor.move(store, mirror, executor.plan(store, "advance", REPO, lkgr, sha))
            channels.apply(store, mirror, channels.plan_promote(cfg, store, REPO, CHANNEL, sha,
                                                                "sha256:" + hashlib.sha256(sha.encode()).hexdigest()))

        def checkout() -> int:
            return qqinstall("checkout", *sel, "--remote", remote, "--dest", str(app))

        def verify() -> int:
            return qqinstall("verify", *sel, "--remote", remote, "--checkout", str(app))

        def head() -> str:
            return git("rev-parse", "HEAD", cwd=app)

        promote(shas[0])
        rc = checkout()
        check(f"1. fresh qqinstall checkout (exit {rc}) is canary's commit 1", rc == 0 and head() == shas[0])
        lib = app / ".qq-drill-install-lib.sh"   # beside server.py, so install.sh sees itself in a checkout
        lib.write_text(functions_only((app / "install.sh").read_bytes()))
        mode = subprocess.run(["bash", "-c", f'source "{lib}"; resolve_repo_dir; printf "%s|%s" "$MANAGED_CHECKOUT" "$REPO_DIR"'],
                              cwd=app, capture_output=True, text=True,
                              env={k: v for k, v in os.environ.items() if not k.startswith("QUIRQ_")})
        lib.unlink()
        check(f"   the checkout's own install.sh runs it in place, no git ({mode.stdout!r})",
              mode.returncode == 0 and mode.stdout == f"0|{app}")

        promote(shas[1])
        check("2. canary promoted to commit 2: the install is stale (verify exit 1)", verify() == 1)
        rc = checkout()
        check(f"   checkout again (exit {rc}) -> commit 2", rc == 0 and head() == shas[1] and verify() == 0)

        git("tag", REF, evil, cwd=upstream)
        git("update-ref", f"refs/{REF}", evil, cwd=upstream)
        rc = checkout()
        check(f"3. a tag and refs/{REF} planted at an unreviewed commit: checkout (exit {rc}) stays on commit 2",
              rc == 0 and head() == shas[1])
        old = tmp / "old-flow" / REPO
        old.parent.mkdir()
        (tmp / "lib.sh").write_text(functions_only(script))
        env = {k: v for k, v in os.environ.items() if not k.startswith("QUIRQ_")}
        env.update(QUIRQ_SOURCE_REPO=remote, QUIRQ_SOURCE_REF=REF)
        for _ in range(2):   # first run clones the branch, the second updates by name
            subprocess.run(["bash", "-c", f'source "{tmp / "lib.sh"}"; REPO_DIR="{old}"; MANAGED_CHECKOUT=1; fetch_repo'],
                           cwd=tmp, env=env, capture_output=True, text=True)
        check("   for contrast, the QUIRQ_SOURCE_REF flow's update installs the planted commit",
              git("rev-parse", "HEAD", cwd=old) == evil)

        rc = cli.main(["channel", "rollback", "--config", args.config, "--state", str(state), "--backend", "local",
                       "--target-root", str(targets), "--repo", REPO, "--channel", CHANNEL, "--from", shas[1],
                       "--reason", "installer canary drill"])
        check(f"4. release rolled canary back (exit {rc})", rc == 0)
        rc = checkout()
        check(f"   checkout again (exit {rc}) -> back to commit 1", rc == 0 and head() == shas[0] and verify() == 0)

        (app / "server.py").write_text("# edited on the test machine\n")
        rc = checkout()
        check(f"5. a checkout with local changes is left alone (exit {rc})", rc == 1 and head() == shas[0])
    if failures:
        return 1
    print("ok: a test install followed canary through two promotions and a rollback, each checked before "
          "it could run, and planted refs changed nothing; no xo-space code change")
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=None))

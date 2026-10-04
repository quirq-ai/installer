"""V0-INS-02 done-when, offline half: a test install follows `channels/canary`.

Runs xo-space's real `install.sh` (fetched at the commit pinned in pins.toml and checked against
its sha256) with `QUIRQ_SOURCE_REF=channels/canary`, against a local stand-in for the repo whose
`channels/canary` branch is moved by release's own executor (the `contract` extra, pinned). Only
install.sh's clone/update step runs (`fetch_repo`, the same functions-only sourcing xo-space's own
tests/install_sh_harness.sh uses): no uv, venv or server.

1. canary is promoted to commit 1; a fresh test install clones it.
2. canary is promoted to commit 2; the install is stale (`qqinstall verify` exits 1) until
   install.sh runs again, then it is commit 2.
3. canary is rolled back; running install.sh again takes the install back to commit 1.
4. an existing install on `main` is left alone: a test install must be its own checkout.

After every step `qqinstall verify` (exit codes only) compares the checkout with what the
published channels.json names.

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

ROOT = Path(__file__).resolve().parent.parent
REPO = "xo-space"     # the consumer under test; its name is also install.sh's checkout directory
CHANNEL = "canary"
REF = f"channels/{CHANNEL}"
GIT_ID = ["-c", "user.name=drill", "-c", "user.email=drill@example.invalid"]


def git(*args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def install_sh(path: str | None) -> str:
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
    text = data.decode().replace("\r", "")
    lines = text.rstrip("\n").split("\n")
    if lines[-1].rstrip() != 'main "$@"':
        raise SystemExit(f"install.sh no longer ends in `main \"$@\"`; update the drill: {lines[-1]!r}")
    return "\n".join(lines[:-1]) + "\n"     # functions only, as xo-space's own harness does


def run_install(lib: Path, app_dir: Path, upstream: Path, ref: str | None) -> int:
    """install.sh's clone-or-update step, as a managed install with QUIRQ_SOURCE_REF set."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("QUIRQ_")}
    env["QUIRQ_SOURCE_REPO"] = f"file://{upstream}"
    if ref is not None:
        env["QUIRQ_SOURCE_REF"] = ref
    script = f'source "{lib}"; REPO_DIR="{app_dir}"; MANAGED_CHECKOUT=1; fetch_repo'
    p = subprocess.run(["bash", "-c", script], cwd=lib.parent, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout + p.stderr, file=sys.stderr)
    return p.returncode


def verify(manifest: Path, checkout: Path) -> int:
    return subprocess.run([sys.executable, "-m", "qqinstall", "verify", "--repo", REPO, "--channel", CHANNEL,
                           "--source", str(manifest), "--checkout", str(checkout)],
                          capture_output=True, text=True).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--install-sh", help="a local copy of the pinned install.sh (default: fetch it)")
    args = ap.parse_args(argv)
    cfg = config.load(Path(args.config))
    lkgr = config.lkgr_ref(cfg)
    failures = []

    def check(what: str, ok: bool):
        print(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            failures.append(what)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib = tmp / "lib.sh"
        lib.write_text(install_sh(args.install_sh))
        state = tmp / "state"
        state.mkdir()
        git("init", "-q", "-b", "release-state", cwd=state)
        store = Store(state)
        targets = tmp / "targets"
        upstream = targets / REPO
        upstream.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=upstream)
        shas = []
        for i in range(2):
            (upstream / "server.py").write_text(f"# version {i + 1}\n")
            (upstream / "requirements.txt").write_text("")
            git("add", ".", cwd=upstream)
            git(*GIT_ID, "commit", "-q", "-m", f"commit {i + 1}", cwd=upstream)
            shas.append(git("rev-parse", "HEAD", cwd=upstream))
        mirror = backends.load("local", target_root=targets)
        manifest = state / channels.MANIFEST

        def promote(sha: str):
            executor.move(store, mirror, executor.plan(store, "advance", REPO, lkgr, sha))
            channels.apply(store, mirror, channels.plan_promote(cfg, store, REPO, CHANNEL, sha,
                                                                "sha256:" + hashlib.sha256(sha.encode()).hexdigest()))

        app = tmp / "test-env" / REPO
        app.parent.mkdir()

        promote(shas[0])
        rc = run_install(lib, app, upstream, REF)
        check(f"fresh test install with QUIRQ_SOURCE_REF={REF} (exit {rc})", rc == 0)
        check("…is canary's commit 1 (qqinstall verify exit 0)", verify(manifest, app) == 0)
        check("…on the channel branch", git("rev-parse", "--abbrev-ref", "HEAD", cwd=app) == REF)

        promote(shas[1])
        check("canary promoted to commit 2: the install is stale (verify exit 1)", verify(manifest, app) == 1)
        rc = run_install(lib, app, upstream, REF)
        check(f"install.sh run again (exit {rc}) -> commit 2 (verify exit 0)", rc == 0 and verify(manifest, app) == 0)

        rc = cli.main(["channel", "rollback", "--config", args.config, "--state", str(state), "--backend", "local",
                       "--target-root", str(targets), "--repo", REPO, "--channel", CHANNEL, "--from", shas[1],
                       "--reason", "installer canary drill"])
        check(f"release rolled canary back (exit {rc})", rc == 0)
        rc = run_install(lib, app, upstream, REF)
        check(f"install.sh run again (exit {rc}) -> back to commit 1 (verify exit 0)",
              rc == 0 and verify(manifest, app) == 0 and git("rev-parse", "HEAD", cwd=app) == shas[0])

        main_install = tmp / "user-env" / REPO
        main_install.parent.mkdir()
        rc = run_install(lib, main_install, upstream, None)
        before = git("rev-parse", "HEAD", cwd=main_install)
        rc2 = run_install(lib, main_install, upstream, REF)
        check("an install on main is left alone when QUIRQ_SOURCE_REF names the channel",
              rc == 0 and rc2 == 0 and git("rev-parse", "--abbrev-ref", "HEAD", cwd=main_install) == "main"
              and git("rev-parse", "HEAD", cwd=main_install) == before)
    if failures:
        return 1
    print(f"ok: a test install with QUIRQ_SOURCE_REF={REF} followed the channel through two promotions "
          "and a rollback, with no xo-space code change")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""V0-INS-01 done-when: a script resolves `canary` to a commit and digest.

The manifest is written by release's own code at the commit pinned in pins.toml (the `contract`
extra installs it), so this checks the real contract, not a copy of it. For every repo that
infra-config ships on canary, it:

1. resolves before any move: `qqinstall resolve` exits 3 (not published yet);
2. ships two canaries through release's executor (lkgr advances, canary is promoted with a digest)
   and resolves canary to the second commit and its digest;
3. rolls canary back and resolves it to the first commit and digest again.

Every check is on `qqinstall`'s exit code and its `--format json` output.

    python tools/contract_check.py --config .qq/infra-config
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from qqrelease import backends, channels, cli, config, executor
from qqrelease.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermetic import isolate  # noqa: E402

CHANNEL = "canary"


def git(*args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def resolve(source: Path, repo: str) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, "-m", "qqinstall", "resolve", "--repo", repo, "--channel", CHANNEL,
                        "--source", str(source), "--format", "json"], capture_output=True, text=True)
    return p.returncode, (json.loads(p.stdout) if p.returncode == 0 else {})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    cfg = config.load(Path(args.config))
    ref = config.lkgr_ref(cfg)
    names = [r["name"] for r in cfg["repos"]["repo"] if CHANNEL in r.get("channels", [])]
    if not names:
        print(f"no onboarded repo ships on {CHANNEL}", file=sys.stderr)
        return 1
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
        mirror = backends.load("local", target_root=tmp / "targets")
        manifest = state / channels.MANIFEST
        for name in names:
            rc, _ = resolve(manifest, name)
            check(f"{name}: before any channel move, resolve says not published (exit {rc})", rc == 3)
            d = tmp / "targets" / name
            d.mkdir(parents=True)
            git("init", "-q", "-b", "main", cwd=d)
            shas = []
            for i in range(2):
                git("-c", "user.name=contract", "-c", "user.email=contract@example.invalid", "commit", "-q",
                    "--allow-empty", "-m", f"commit {i + 1}", cwd=d)
                shas.append(git("rev-parse", "HEAD", cwd=d))
            for sha in shas:
                executor.move(store, mirror, executor.plan(store, "advance", name, ref, sha))
                channels.apply(store, mirror, channels.plan_promote(cfg, store, name, CHANNEL, sha,
                                                                    digest(f"{name}@{sha}")))
            rc, got = resolve(manifest, name)
            check(f"{name}: canary resolves to the promoted commit and digest (exit {rc})",
                  rc == 0 and (got.get("commit"), got.get("digest")) == (shas[1], digest(f"{name}@{shas[1]}")))
            rc = cli.main(["channel", "rollback", "--config", args.config, "--state", str(state),
                           "--backend", "local", "--target-root", str(tmp / "targets"),
                           "--repo", name, "--channel", CHANNEL, "--reason", "installer contract check"])
            check(f"{name}: release rolled canary back (exit {rc})", rc == 0)
            rc, got = resolve(manifest, name)
            check(f"{name}: after the rollback canary resolves to the previous commit and digest (exit {rc})",
                  rc == 0 and (got.get("commit"), got.get("digest")) == (shas[0], digest(f"{name}@{shas[0]}")))
    if failures:
        return 1
    print(f"ok: qqinstall resolved {CHANNEL} to a commit and digest for {len(names)} repos, from the "
          "channels.json release's own code writes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

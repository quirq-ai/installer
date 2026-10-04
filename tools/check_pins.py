"""Fail unless every GitHub Action this repo uses is pinned by a full commit SHA.

    python tools/check_pins.py [ROOT]

A tag or branch (`@v4`, `@main`) can be moved by whoever controls the action's repo, so it is
refused; local actions (`./...`) and `docker://` images pinned by `@sha256:` digest are allowed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^'\"\s#]+)")
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
DOCKER = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")


def problems(root: Path) -> list[str]:
    out = []
    gh = root / ".github"
    files = sorted(p for p in gh.rglob("*") if p.suffix in (".yml", ".yaml")) if gh.is_dir() else []
    for f in files:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            m = USES.match(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./") or SHA.match(ref) or DOCKER.match(ref):
                continue
            out.append(f"{f.relative_to(root)}:{n}: {ref} is not pinned by a full commit SHA")
    return out


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else ".").resolve()
    bad = problems(root)
    for b in bad:
        print(b, file=sys.stderr)
    if bad:
        return 1
    print("ok: every action is pinned by a full commit SHA")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fail unless every GitHub Action this repo uses is pinned by a full commit SHA.

    python tools/check_pins.py [ROOT]

A tag or branch (`@v4`, `@main`) can be moved by whoever controls the action's repo, so it is
refused; local actions (`./...`) and `docker://` images pinned by `@sha256:` digest are allowed.

Workflows (`.github/workflows/`) and action metadata (`action.yml` anywhere in the repo) are read
line by line. To stay strict without a YAML parser, any line that mentions `uses` in a form this
check cannot read (a flow mapping, a quoted key, a value on the next line) fails too.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(r"""^\s*(?:-\s+)?uses:[ \t]+(?P<q>['"]?)(?P<ref>[^'"\s]+)(?P=q)(?:[ \t]+#.*)?\s*$""")
MENTION = re.compile(r"""(^|[\s{,"'-])uses['"]?\s*:""")
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
DOCKER = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
SKIP = {".git", ".venv", "node_modules", ".qq"}


def files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if any(part in SKIP for part in p.relative_to(root).parts) or not p.is_file():
            continue
        ext = p.suffix.lower()
        in_workflows = p.parent == root / ".github" / "workflows"
        if (in_workflows and ext in (".yml", ".yaml")) or p.name.lower() in ("action.yml", "action.yaml"):
            out.append(p)
    return sorted(out)


def problems(root: Path) -> list[str]:
    out = []
    for f in files(root):
        block_indent = None   # inside a `run: |` style block scalar, nothing is a key
        for n, line in enumerate(f.read_text().splitlines(), 1):
            indent = len(line) - len(line.lstrip())
            if block_indent is not None:
                if not line.strip() or indent > block_indent:
                    continue
                block_indent = None
            if re.search(r":\s*[|>][-+0-9]*\s*(#.*)?$", line):
                block_indent = indent
            m = USES.match(line)
            if m:
                ref = m.group("ref")
                if ref.startswith("./") or SHA.match(ref) or DOCKER.match(ref):
                    continue
                out.append(f"{f.relative_to(root)}:{n}: {ref} is not pinned by a full commit SHA")
            elif MENTION.search(line.split(" #", 1)[0]):
                out.append(f"{f.relative_to(root)}:{n}: cannot read this `uses` line; write it as `uses: owner/repo@<sha>`")
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

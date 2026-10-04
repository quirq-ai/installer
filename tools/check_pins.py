"""Fail unless every GitHub Action this repo uses is pinned by a full commit SHA.

    python tools/check_pins.py [ROOT]

A tag or branch (`@v4`, `@main`) can be moved by whoever controls the action's repo, so it is
refused; local actions (`./...`) and `docker://` images pinned by `@sha256:` digest are allowed.

Workflows (`.github/workflows/`) and action metadata (`action.yml` anywhere in the repo) are read
line by line. To stay strict without a YAML parser, any line that mentions `uses` in a form this
check cannot read (a flow mapping, a quoted key, a value on the next line) fails too.
"""
from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

USES = re.compile(r"""^\s*(?:-\s+)?uses:[ \t]+(?P<q>['"]?)(?P<ref>[^'"\s]+)(?P=q)(?:[ \t]+#.*)?\s*$""")
MENTION = re.compile(r"""(^|[\s{,"'-])uses['"]?\s*:""")
SHA = re.compile(r"[^@\s]+@[0-9a-f]{40}")
DOCKER = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
# Root-relative paths never scanned: git's own data and checkouts CI makes of other repos. A local
# action (`uses: ./...`) pointing into one of them is refused instead, so nothing hides there.
SKIP = (".git", ".venv", ".qq/infra-config")
COMPLEX_KEY = re.compile(r"^\s*(?:-\s+)?\?(\s|$)")             # `? uses` then `: value`
ESCAPED_KEY = re.compile(r"""^\s*(?:-\s+)?(["'])[^"']*\\""")  # "u\x73es": ...


def skipped(rel: str) -> bool:
    rel = posixpath.normpath(rel)
    return any(rel == s or rel.startswith(s + "/") for s in SKIP)


def strip_comment(line: str) -> str:
    """The line without a trailing ` # comment` (a `#` inside quotes is kept)."""
    q = None
    for i, c in enumerate(line):
        if q:
            q = None if c == q else q
        elif c in "'\"":
            q = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line


def files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if skipped(p.relative_to(root).as_posix()) or not p.is_file():
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
            code = strip_comment(line)
            if re.search(r":\s*[|>][-+0-9]*\s*$", code):
                block_indent = indent
            m = USES.match(line)
            if m:
                ref = m.group("ref")
                if ref.startswith("./") and skipped(ref):
                    out.append(f"{f.relative_to(root)}:{n}: {ref} points into a path this check does not scan")
                elif ref.startswith("./") or SHA.fullmatch(ref) or DOCKER.fullmatch(ref):
                    continue
                else:
                    out.append(f"{f.relative_to(root)}:{n}: {ref} is not pinned by a full commit SHA")
            elif MENTION.search(code) or COMPLEX_KEY.match(code) or ESCAPED_KEY.match(code):
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

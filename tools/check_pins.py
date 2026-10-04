"""Fail unless every GitHub Action this repo uses is pinned by a full commit SHA.

    python tools/check_pins.py [ROOT]

A tag or branch (`@v4`, `@main`) can be moved by whoever controls the action's repo, so it is
refused; local actions (`./...`) and `docker://` images pinned by `@sha256:` digest are allowed.

Workflows (`.github/workflows/*.yml`) and action metadata (`action.yml` anywhere in the repo) are
parsed as YAML, and every `uses` key at any depth is checked, however the YAML spells it (flow
mappings, quoted or escaped keys, values on the next line). Fail closed: a file that does not
parse, or has a duplicate key, fails. A local action must resolve (symlinks included) inside the
repo and outside the paths this check skips.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

SHA = re.compile(r"[^@\s]+@[0-9a-f]{40}")
DOCKER = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
# Root-relative paths never scanned: git's own data and checkouts CI makes of other repos.
SKIP = (".git", ".venv", ".qq/infra-config")


class _StrictLoader(yaml.SafeLoader):
    pass


def _no_duplicates(loader, node, deep=False):
    keys = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in keys:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", k.start_mark)
        keys.add(key)
    return loader.construct_mapping(node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def skipped(root: Path, path: Path) -> bool:
    """True if `path`, fully resolved, is outside `root` or inside a skipped path."""
    real = Path(os.path.realpath(path))
    try:
        rel = real.relative_to(Path(os.path.realpath(root))).as_posix()
    except ValueError:
        return True
    return any(rel == s or rel.startswith(s + "/") for s in SKIP)


def files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if not skipped(root, d / n)]
        for name in filenames:
            p = d / name
            in_workflows = d == root / ".github" / "workflows"
            ext = p.suffix.lower()
            if (in_workflows and ext in (".yml", ".yaml")) or name.lower() in ("action.yml", "action.yaml"):
                out.append(p)
    return sorted(out)


def _uses(node):
    """Every value of a `uses` key, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "uses":
                yield v
            yield from _uses(v)
    elif isinstance(node, list):
        for v in node:
            yield from _uses(v)


def problems(root: Path) -> list[str]:
    out = []
    for f in files(root):
        rel = f.relative_to(root)
        try:
            docs = list(yaml.load_all(f.read_text(), Loader=_StrictLoader))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            out.append(f"{rel}: cannot parse: {e}".replace("\n", " "))
            continue
        for ref in _uses(docs):
            if not isinstance(ref, str):
                out.append(f"{rel}: `uses: {ref!r}` is not a string")
            elif ref.startswith("./"):
                if skipped(root, root / ref):
                    out.append(f"{rel}: {ref} resolves outside the repo or into a path this check skips")
            elif not (SHA.fullmatch(ref) or DOCKER.fullmatch(ref)):
                out.append(f"{rel}: {ref} is not pinned by a full commit SHA")
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

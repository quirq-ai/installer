import json
import subprocess

import pytest

C1 = "a" * 40
C2 = "b" * 40
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def doc(**repos):
    return {"schema": "qq-channels/1", "repos": repos}


def entry(commit=C1, digest=D1, generation=1, op="op1", updated_at="2026-10-04T00:00:00Z"):
    return {"commit": commit, "digest": digest, "generation": generation, "op": op, "updated_at": updated_at}


@pytest.fixture
def write(tmp_path):
    def _write(obj, name="channels.json"):
        p = tmp_path / name
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
        return str(p)
    return _write


@pytest.fixture
def checkout(tmp_path):
    d = tmp_path / "checkout"
    d.mkdir()
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    (d / "f").write_text("1\n")
    subprocess.run([*g, "-C", str(d), "add", "f"], check=True)
    subprocess.run([*g, "-C", str(d), "commit", "-qm", "c1"], check=True)
    sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    return d, sha

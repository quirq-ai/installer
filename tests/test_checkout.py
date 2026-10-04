import subprocess

import pytest

from conftest import D1, doc, entry
from qqinstall import cli, manifest

G = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def git(d, *a):
    return subprocess.run([*G, "-C", str(d), *a], check=True, capture_output=True, text=True).stdout.strip()


def commit(d, text):
    (d / "server.py").write_text(text)
    git(d, "add", "server.py")
    git(d, "commit", "-qm", text)
    return git(d, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path):
    d = tmp_path / "upstream"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    good = [commit(d, "good 1\n"), commit(d, "good 2\n")]
    git(d, "checkout", "-q", "-b", "side")
    evil = commit(d, "evil\n")
    git(d, "checkout", "-q", "main")
    return d, good, evil


def run(capsys, *argv):
    rc = cli.main(list(argv))
    capsys.readouterr()
    return rc


def co(capsys, write, remote, dest, sha, name="m.json"):
    src = write(doc(app={"canary": entry(commit=sha)}), name=name)
    return run(capsys, "checkout", "--repo", "app", "--channel", "canary", "--source", src,
               "--remote", remote, "--dest", str(dest))


def test_checkout_follows_the_manifest_not_names(upstream, tmp_path, write, capsys):
    d, good, evil = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    git(d, "branch", "channels/canary", good[0])
    assert co(capsys, write, remote, dest, good[0]) == cli.OK
    assert git(dest, "rev-parse", "HEAD") == good[0]
    # names the remote could point elsewhere play no part
    git(d, "tag", "channels/canary", evil)
    git(d, "update-ref", "refs/channels/canary", evil)
    assert co(capsys, write, remote, dest, good[1], name="m2.json") == cli.OK
    assert git(dest, "rev-parse", "HEAD") == good[1]
    assert co(capsys, write, remote, dest, good[0], name="m3.json") == cli.OK    # rollback
    assert git(dest, "rev-parse", "HEAD") == good[0]


def test_checkout_refuses_dirty_and_foreign(upstream, tmp_path, write, capsys):
    d, good, _ = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    assert co(capsys, write, remote, dest, good[0]) == cli.OK
    (dest / "server.py").write_text("edited\n")
    assert co(capsys, write, remote, dest, good[1], name="m2.json") == cli.MISMATCH
    assert git(dest, "rev-parse", "HEAD") == good[0]          # left as it was
    git(dest, "checkout", "--", "server.py")
    assert co(capsys, write, f"file://{tmp_path}/other", dest, good[1], name="m3.json") == cli.ERROR


def test_checkout_missing_commit(upstream, tmp_path, write, capsys):
    d, _, _ = upstream
    assert co(capsys, write, f"file://{d}", tmp_path / "install", "c" * 40) == cli.ERROR


def test_verify_remote_and_replace_refs(upstream, tmp_path, write, capsys):
    d, good, evil = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    assert co(capsys, write, remote, dest, good[0]) == cli.OK
    src = write(doc(app={"canary": entry(commit=good[0])}), name="v.json")
    base = ["verify", "--repo", "app", "--channel", "canary", "--source", src, "--checkout", str(dest)]
    assert run(capsys, *base, "--remote", remote) == cli.OK
    assert run(capsys, *base, "--remote", "file:///elsewhere") == cli.ERROR
    # A replace ref makes plain git show the evil tree under the good commit's id.
    git(dest, "fetch", "-q", "origin", "side")
    git(dest, "replace", good[0], evil)
    subprocess.run([*G, "-C", str(dest), "checkout", "-q", "-f", "--detach", good[0]], check=True)
    assert (dest / "server.py").read_text() == "evil\n"
    assert run(capsys, *base) == cli.MISMATCH


def test_check_on_release_state(tmp_path):
    d = tmp_path / "release"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    on_main = commit(d, "main\n")
    git(d, "checkout", "-q", "-b", "release-state")
    first = commit(d, "state 1\n")
    tip = commit(d, "state 2\n")
    remote = f"file://{d}"
    manifest.check_on_release_state(first, remote)
    manifest.check_on_release_state(tip, remote)
    manifest.check_on_release_state(on_main, remote)     # an ancestor of the branch is on it
    git(d, "checkout", "-q", "main")
    other = commit(d, "other branch\n")
    for bad in [other, "d" * 40]:
        with pytest.raises(manifest.InstallerError):
            manifest.check_on_release_state(bad, remote)
    with pytest.raises(manifest.InstallerError):
        manifest.check_on_release_state(tip, f"file://{tmp_path}/missing")


def test_default_source_names_the_branch():
    assert "/refs/heads/release-state/channels.json" in manifest.DEFAULT_SOURCE
    assert D1

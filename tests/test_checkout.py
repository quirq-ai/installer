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


def co(capsys, write, remote, dest, sha, name="m.json", generation=1):
    src = write(doc(app={"canary": entry(commit=sha, generation=generation)}), name=name)
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


def test_checkout_refuses_an_older_generation(upstream, tmp_path, write, capsys):
    d, good, _ = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    assert co(capsys, write, remote, dest, good[0], name="g1.json", generation=1) == cli.OK
    assert co(capsys, write, remote, dest, good[1], name="g2.json", generation=2) == cli.OK
    assert git(dest, "config", "--local", "qqinstall.app/canary.generation") == "2"
    # a replayed generation-1 manifest is refused and the checkout stays put
    assert co(capsys, write, remote, dest, good[0], name="g1b.json", generation=1) == cli.ERROR
    assert git(dest, "rev-parse", "HEAD") == good[1]
    # a rollback is a new move with a higher generation, so it goes through
    assert co(capsys, write, remote, dest, good[0], name="g3.json", generation=3) == cli.OK
    assert git(dest, "rev-parse", "HEAD") == good[0]


def test_unreadable_recorded_generation_is_an_error(upstream, tmp_path, write, capsys):
    d, good, _ = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    assert co(capsys, write, remote, dest, good[0]) == cli.OK
    git(dest, "config", "--local", "qqinstall.app/canary.generation", "nope")
    assert co(capsys, write, remote, dest, good[1], name="m2.json", generation=5) == cli.ERROR


def test_checkout_refuses_a_commit_off_the_branch(upstream, tmp_path, write, capsys):
    d, good, evil = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    # a manifest naming an unmerged commit is refused, and the failed first checkout leaves nothing
    assert co(capsys, write, remote, dest, evil) == cli.ERROR
    assert not dest.exists()
    assert co(capsys, write, remote, dest, good[0], name="m2.json") == cli.OK
    assert co(capsys, write, remote, dest, evil, name="m3.json", generation=2) == cli.ERROR
    assert git(dest, "rev-parse", "HEAD") == good[0]
    src = write(doc(app={"canary": entry(commit=evil)}), name="m4.json")
    assert run(capsys, "checkout", "--repo", "app", "--channel", "canary", "--source", src,
               "--remote", remote, "--dest", str(tmp_path / "other"), "--branch", "side") == cli.OK
    for bad in ["../x", "a:b", "-x"]:
        assert run(capsys, "checkout", "--repo", "app", "--channel", "canary", "--source", src,
                   "--remote", remote, "--dest", str(tmp_path / "x"), f"--branch={bad}") == cli.ERROR


def test_failed_first_checkout_is_undone(upstream, tmp_path, write, capsys):
    d, good, _ = upstream
    remote = f"file://{d}"
    dest = tmp_path / "install"
    assert co(capsys, write, remote, dest, "c" * 40) == cli.ERROR      # not on the remote (yet)
    assert not dest.exists()
    assert co(capsys, write, remote, dest, good[0], name="m2.json") == cli.OK
    empty = tmp_path / "empty"
    empty.mkdir()
    assert co(capsys, write, remote, empty, "c" * 40, name="m3.json") == cli.ERROR
    assert empty.is_dir() and not any(empty.iterdir())                    # an empty --dest stays, empty
    assert co(capsys, write, remote, empty, good[0], name="m4.json") == cli.OK
    assert git(empty, "rev-parse", "HEAD") == good[0]


def test_symlinks_must_stay_inside(upstream, tmp_path, write, capsys):
    d, _, _ = upstream
    remote = f"file://{d}"
    (d / "docs").mkdir()
    (d / "docs" / "readme").symlink_to("../server.py")                  # inside: fine
    git(d, "add", "docs")
    git(d, "commit", "-qm", "inside link")
    inside = git(d, "rev-parse", "HEAD")
    assert co(capsys, write, remote, tmp_path / "a", inside) == cli.OK
    (d / "install.sh").symlink_to(tmp_path / "outside.sh")
    git(d, "add", "install.sh")
    git(d, "commit", "-qm", "outside link")
    outside = git(d, "rev-parse", "HEAD")
    assert co(capsys, write, remote, tmp_path / "b", outside, name="m2.json") == cli.ERROR
    assert not (tmp_path / "b").exists()


def test_origin_is_compared_before_insteadof(upstream, tmp_path, write, capsys, monkeypatch):
    d, good, _ = upstream
    home = tmp_path / "home"
    home.mkdir()
    remote = "https://example.invalid/app.git"
    (home / ".gitconfig").write_text(f'[url "file://{d}"]\n\tinsteadOf = {remote}\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    dest = tmp_path / "install"
    assert co(capsys, write, remote, dest, good[0]) == cli.OK
    assert co(capsys, write, remote, dest, good[1], name="m2.json") == cli.OK


def test_git_keeps_only_ca_settings(monkeypatch):
    monkeypatch.setenv("GIT_SSL_CAINFO", "/ca.pem")
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.update(kw["env"]) or subprocess.CompletedProcess(argv, 0))
    cli._git(None, "version")
    assert seen.get("GIT_SSL_CAINFO") == "/ca.pem" and "GIT_DIR" not in seen

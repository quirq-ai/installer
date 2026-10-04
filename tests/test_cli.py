import json

from conftest import C1, D1, doc, entry
from qqinstall import cli


def run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_resolve_formats(write, capsys):
    src = write(doc(app={"canary": entry()}))
    rc, out, _ = run(capsys, "resolve", "--repo", "app", "--channel", "canary", "--source", src, "--format", "json")
    assert rc == 0 and json.loads(out)["commit"] == C1 and json.loads(out)["digest"] == D1
    rc, out, _ = run(capsys, "resolve", "--repo", "app", "--channel", "canary", "--source", src, "--format", "env")
    assert rc == 0 and out.splitlines() == [f"QQ_CHANNEL_COMMIT={C1}", f"QQ_CHANNEL_DIGEST={D1}",
                                            "QQ_CHANNEL_GENERATION=1"]


def test_exit_codes(write, tmp_path, capsys):
    assert run(capsys, "resolve", "--repo", "app", "--channel", "canary",
               "--source", str(tmp_path / "absent.json"))[0] == cli.NOT_PUBLISHED
    assert run(capsys, "resolve", "--repo", "app", "--channel", "canary",
               "--source", write("{}"))[0] == cli.ERROR
    assert run(capsys, "resolve", "--repo", "app", "--channel", "canary",
               "--source", "x", "--at", C1)[0] == cli.ERROR
    assert run(capsys, "resolve", "--repo", "app", "--channel", "canary", "--at", "main")[0] == cli.ERROR


def test_verify(write, checkout, capsys):
    d, sha = checkout
    src = write(doc(app={"canary": entry(commit=sha)}))
    base = ["verify", "--repo", "app", "--channel", "canary", "--source", src, "--checkout", str(d)]
    assert run(capsys, *base)[0] == cli.OK
    (d / "f").write_text("edited\n")
    assert run(capsys, *base)[0] == cli.MISMATCH          # tracked edits are not the channel's code
    (d / "f").write_text("1\n")
    other = write(doc(app={"canary": entry()}), name="other.json")
    assert run(capsys, "verify", "--repo", "app", "--channel", "canary", "--source", other,
               "--checkout", str(d))[0] == cli.MISMATCH


def test_verify_not_a_checkout(write, tmp_path, capsys):
    src = write(doc(app={"canary": entry()}))
    assert run(capsys, "verify", "--repo", "app", "--channel", "canary", "--source", src,
               "--checkout", str(tmp_path))[0] == cli.ERROR


def test_show(write, capsys):
    rc, out, _ = run(capsys, "show", "--source", write(doc(app={"canary": entry()})))
    assert rc == 0 and json.loads(out)["app canary"]["commit"] == C1


def test_verify_untracked_and_hidden_changes(write, checkout, capsys):
    import subprocess
    d, sha = checkout
    src = write(doc(app={"canary": entry(commit=sha)}))
    base = ["verify", "--repo", "app", "--channel", "canary", "--source", src, "--checkout", str(d)]
    (d / "new").write_text("x\n")
    assert run(capsys, *base)[0] == cli.MISMATCH
    (d / "new").unlink()
    subprocess.run(["git", "-C", str(d), "update-index", "--skip-worktree", "f"], check=True)
    assert run(capsys, *base)[0] == cli.MISMATCH
    subprocess.run(["git", "-C", str(d), "update-index", "--no-skip-worktree", "f"], check=True)
    assert run(capsys, *base)[0] == cli.OK


def test_verify_subdirectory_is_an_error(write, checkout, capsys):
    d, sha = checkout
    (d / "sub").mkdir()
    src = write(doc(app={"canary": entry(commit=sha)}))
    assert run(capsys, "verify", "--repo", "app", "--channel", "canary", "--source", src,
               "--checkout", str(d / "sub"))[0] == cli.ERROR


def test_unexpected_errors_exit_2(write, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(cli.manifest, "resolve", boom)
    assert run(capsys, "resolve", "--repo", "app", "--channel", "canary", "--source", write(doc()))[0] == cli.ERROR

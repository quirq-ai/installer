import json

import pytest

from conftest import C1, C2, D1, D2, doc, entry
from qqinstall import manifest
from qqinstall.manifest import ManifestError, NotPublished


def test_resolves_commit_and_digest(write):
    src = write(doc(app={"canary": entry(), "dev": entry(C2, D2, 4)}))
    ch = manifest.resolve(src, "app", "canary")
    assert (ch.commit, ch.digest, ch.generation) == (C1, D1, 1)
    assert manifest.resolve(src, "app", "dev").commit == C2


def test_absent_file_is_not_published(tmp_path):
    with pytest.raises(NotPublished):
        manifest.resolve(str(tmp_path / "nope.json"), "app", "canary")


@pytest.mark.parametrize("repo,channel", [("app", "stable"), ("other", "canary")])
def test_absent_repo_or_channel_is_not_published(write, repo, channel):
    with pytest.raises(NotPublished):
        manifest.resolve(write(doc(app={"canary": entry()})), repo, channel)


def test_empty_repos_is_not_published(write):
    with pytest.raises(NotPublished):
        manifest.resolve(write(doc()), "app", "canary")


@pytest.mark.parametrize("bad", [
    {"schema": "qq-channels/2", "repos": {}},
    {"repos": {}},
    {"schema": "qq-channels/1"},
    {"schema": "qq-channels/1", "repos": []},
    [],
    doc(app=[]),
    doc(app={"canary": "x"}),
    doc(app={"canary": entry(commit="A" * 40)}),
    doc(app={"canary": entry(commit="a" * 39)}),
    doc(app={"canary": entry(commit="main")}),
    doc(app={"canary": entry(commit=None)}),
    doc(app={"canary": entry(digest="sha256:" + "1" * 63)}),
    doc(app={"canary": entry(digest="sha512:" + "1" * 64)}),
    doc(app={"canary": entry(digest="")}),
    doc(app={"canary": entry(generation=0)}),
    doc(app={"canary": entry(generation=True)}),
    doc(app={"canary": entry(generation="1")}),
    doc(app={"canary": entry(op=1)}),
    doc(**{"../x": {"canary": entry()}}),
    doc(app={"can ary": entry()}),
])
def test_invalid_manifest_is_refused(write, bad):
    with pytest.raises(ManifestError):
        manifest.resolve(write(bad), "app", "canary")


def test_invalid_entry_elsewhere_still_refuses(write):
    # One bad entry means the file is not what release wrote: trust none of it.
    with pytest.raises(ManifestError):
        manifest.resolve(write(doc(app={"canary": entry()}, other={"canary": entry(commit="x")})), "app", "canary")


def test_duplicate_keys_are_refused(write):
    text = ('{"schema": "qq-channels/1", "repos": {"app": {"canary": %s, "canary": %s}}}'
            % (json.dumps(entry()), json.dumps(entry(C2, D2))))
    with pytest.raises(ManifestError, match="duplicate"):
        manifest.resolve(write(text), "app", "canary")


def test_not_json_is_refused(write):
    with pytest.raises(ManifestError):
        manifest.resolve(write("not json"), "app", "canary")


def test_oversize_is_refused(write):
    big = doc(app={"canary": entry(op="x" * (manifest.MAX_BYTES + 1))})
    with pytest.raises(ManifestError, match="larger"):
        manifest.resolve(write(big), "app", "canary")


@pytest.mark.parametrize("src", ["http://example.invalid/channels.json", "file:///etc/passwd",
                                 "ftp://example.invalid/x"])
def test_only_https_or_paths(src):
    with pytest.raises(ManifestError):
        manifest.read(src)


@pytest.mark.parametrize("name", ["", "../x", "a/b", "-x", "a b", "x" * 101])
def test_bad_names_are_refused(write, name):
    with pytest.raises(manifest.InstallerError):
        manifest.resolve(write(doc()), name, "canary")


def test_source_at_needs_full_sha():
    assert manifest.source_at(C1).endswith(f"/quirq-ai/release/{C1}/channels.json")
    for bad in ["release-state", "abc123", "A" * 40]:
        with pytest.raises(manifest.InstallerError):
            manifest.source_at(bad)


def test_redirect_to_http_is_refused():
    h = manifest._HttpsOnly()
    with pytest.raises(ManifestError):
        h.redirect_request(None, None, 302, "Found", {}, "http://example.invalid/x")

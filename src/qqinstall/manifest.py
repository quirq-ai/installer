"""V0-INS-01: read the channel manifest and resolve a channel to a commit and digest.

The manifest is release's `channels.json` (schema `qq-channels/1`) on its `release-state` branch.
Only release's executor writes it, after every channel move, so it is the record of what each
repo's channels name:

    {"schema": "qq-channels/1",
     "repos": {"<repo>": {"<channel>": {"commit": "<40 hex>", "digest": "sha256:<64 hex>",
                                        "generation": 3, "op": "...", "updated_at": "..."}}}}

The file appears only after the first channel move, so "absent" is a normal state: it raises
`NotPublished`, as does a repo or channel the file does not name yet. Anything malformed raises
`ManifestError`: every field a client acts on is checked, and nothing is guessed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "qq-channels/1"
RELEASE_REPO = "quirq-ai/release"
STATE_BRANCH = "release-state"
FILE = "channels.json"
RELEASE_GIT = f"https://github.com/{RELEASE_REPO}"
# The moving record: what the channels name now. `refs/heads/` asks for the branch, so a tag that
# happens to share its name is never served instead.
DEFAULT_SOURCE = f"https://raw.githubusercontent.com/{RELEASE_REPO}/refs/heads/{STATE_BRANCH}/{FILE}"
# TODO(expert): raw.githubusercontent.com is GitHub-specific; read it through a `backend` field
# (github now, launchpad later) once a second backend exists.
MAX_BYTES = 1 << 20
TIMEOUT_S = 30

# Always used with fullmatch: `$` with match would accept a trailing newline.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class InstallerError(Exception):
    """A failure the caller should report as is."""


class ManifestError(InstallerError):
    """The manifest could not be read, or is not a valid qq-channels/1 document."""


class NotPublished(InstallerError):
    """The manifest, or the repo or channel in it, does not exist yet."""


@dataclass(frozen=True)
class Channel:
    repo: str
    channel: str
    commit: str
    digest: str
    generation: int
    op: str
    updated_at: str


def source_at(commit: str) -> str:
    """The manifest as it was at one commit of release: a reproducible read. Only trust it after
    `check_on_release_state`: raw serves any commit of the repo (other branches, PR heads, and
    possibly fork commits through GitHub's shared object store)."""
    if not COMMIT.fullmatch(commit):
        raise InstallerError(f"--at must be a full 40-character commit SHA, not {commit!r}")
    return f"https://raw.githubusercontent.com/{RELEASE_REPO}/{commit}/{FILE}"


def check_on_release_state(commit: str, remote: str = RELEASE_GIT) -> None:
    """Raise unless `commit` is on `remote`'s release-state branch (the branch's tip or an ancestor)."""
    source_at(commit)   # the same SHA check
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"

    def git(*argv, cwd):
        try:
            return subprocess.run(["git", "--no-replace-objects", *argv], cwd=cwd, env=env,
                                  capture_output=True, text=True)
        except OSError as e:
            raise InstallerError(f"could not run git: {e}") from None

    with tempfile.TemporaryDirectory() as tmp:
        if git("init", "-q", cwd=tmp).returncode:
            raise InstallerError("could not create a scratch git repository")
        fetched = git("fetch", "--quiet", "--no-tags", "--filter=blob:none", remote,
                      f"+refs/heads/{STATE_BRANCH}:refs/qq/{STATE_BRANCH}", cwd=tmp)
        if fetched.returncode:
            raise InstallerError(f"could not read the {STATE_BRANCH} branch of {remote}")
        if git("merge-base", "--is-ancestor", commit, f"refs/qq/{STATE_BRANCH}", cwd=tmp).returncode:
            raise InstallerError(f"{commit[:12]} is not on {remote}'s {STATE_BRANCH} branch; refusing to "
                                 "read a manifest from it")


def check_name(kind: str, value: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise InstallerError(f"{kind} {value!r} is not a valid name")
    return value


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to https URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ManifestError(f"refusing a redirect to a non-https URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read(source: str) -> bytes:
    """The manifest's bytes from an https URL or a local path. Raises NotPublished if absent."""
    if source.startswith("https://"):
        opener = urllib.request.build_opener(_HttpsOnly)
        req = urllib.request.Request(source, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
        try:
            with opener.open(req, timeout=TIMEOUT_S) as resp:
                data = resp.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotPublished(f"no channel manifest at {source} yet (release publishes it after the "
                                   "first channel move)") from None
            raise ManifestError(f"could not read {source}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ManifestError(f"could not read {source}: {getattr(e, 'reason', e)}") from None
    elif "://" in source:
        raise ManifestError(f"only https URLs or local paths are accepted, not {source}")
    else:
        p = Path(source)
        if not p.exists():
            raise NotPublished(f"no channel manifest at {p}")
        try:
            with p.open("rb") as f:
                data = f.read(MAX_BYTES + 1)
        except OSError as e:
            raise ManifestError(f"could not read {p}: {e}") from None
    if len(data) > MAX_BYTES:
        raise ManifestError(f"{source} is larger than {MAX_BYTES} bytes; refusing it")
    return data


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ManifestError(f"duplicate key {k!r} in the manifest")
        out[k] = v
    return out


def parse(data: bytes) -> dict[tuple[str, str], Channel]:
    """Every (repo, channel) the manifest names, each fully checked."""
    try:
        doc = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ManifestError(f"the manifest is not valid JSON: {e}") from None
    except (ValueError, RecursionError) as e:   # e.g. a huge integer, or absurd nesting
        raise ManifestError(f"the manifest could not be parsed: {type(e).__name__}") from None
    if not isinstance(doc, dict):
        raise ManifestError("the manifest is not a JSON object")
    if doc.get("schema") != SCHEMA:
        raise ManifestError(f"the manifest's schema is {doc.get('schema')!r}, not {SCHEMA!r}")
    repos = doc.get("repos")
    if not isinstance(repos, dict):
        raise ManifestError("the manifest has no `repos` object")
    out = {}
    for repo, chans in repos.items():
        if not isinstance(repo, str) or not NAME.fullmatch(repo):
            raise ManifestError(f"the manifest names an invalid repo {repo!r}")
        if not isinstance(chans, dict):
            raise ManifestError(f"{repo}: channels are not an object")
        for name, entry in chans.items():
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise ManifestError(f"{repo}: invalid channel name {name!r}")
            out[(repo, name)] = _entry(repo, name, entry)
    return out


def _entry(repo: str, channel: str, e) -> Channel:
    where = f"{repo} {channel}"
    if not isinstance(e, dict):
        raise ManifestError(f"{where}: entry is not an object")
    commit, digest, gen = e.get("commit"), e.get("digest"), e.get("generation")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ManifestError(f"{where}: commit {commit!r} is not a 40-character lowercase hex SHA")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ManifestError(f"{where}: digest {digest!r} is not sha256:<64 lowercase hex>")
    if isinstance(gen, bool) or not isinstance(gen, int) or gen < 1:
        raise ManifestError(f"{where}: generation {gen!r} is not a positive integer")
    op, updated_at = e.get("op", ""), e.get("updated_at", "")
    if not isinstance(op, str) or not isinstance(updated_at, str):
        raise ManifestError(f"{where}: op and updated_at must be strings")
    return Channel(repo, channel, commit, digest, gen, op, updated_at)


def load(source: str = DEFAULT_SOURCE) -> dict[tuple[str, str], Channel]:
    return parse(read(source))


def resolve(source: str, repo: str, channel: str) -> Channel:
    """What `channel` names for `repo`: a commit and the digest of the artifact built from it."""
    check_name("repo", repo)
    check_name("channel", channel)
    found = load(source).get((repo, channel))
    if found is None:
        raise NotPublished(f"the manifest does not name {repo} {channel} yet")
    return found

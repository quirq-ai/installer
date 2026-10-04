# installer

Part of **quirq infra** ("qq"), quirq-ai's CI/CD system for repos in any language. This repo makes
clients follow a release **channel** instead of `main`: it reads which commit and artifact digest
each channel names, and documents how an install follows one.

Today xo-space's `install.sh` and in-app updater follow `main`. In v0 only xo-space's **test
installs** follow `channels/canary`, and only in quirq's research and test environments, never on
real users' machines. Installer work is low priority in v0 (P4, decision D8).

**Chromium counterpart:** `chrome/updater` (Omaha), which asks which version a channel names and
installs exactly that.

Plan and every v0 item: [quirq-ai/infra-config](https://github.com/quirq-ai/infra-config),
`docs/plan.md` and `docs/v0.md`. Channels themselves are moved by
[quirq-ai/release](https://github.com/quirq-ai/release); this repo only reads them.

## Resolving a channel (V0-INS-01)

The channel manifest is release's `channels.json` (schema `qq-channels/1`) on its `release-state`
branch: per repo and channel, the commit, the artifact digest and the generation. Only release's
executor writes it, after every channel move. `qqinstall` reads it and resolves a channel:

```sh
pip install "qqinstall @ git+https://github.com/quirq-ai/installer@<commit>"
qqinstall resolve --repo xo-space --channel canary                 # commit, digest, generation
qqinstall resolve --repo xo-space --channel canary --format env    # QQ_CHANNEL_COMMIT=... for scripts
qqinstall resolve --repo xo-space --channel canary --at <release-state commit>   # reproducible read
qqinstall verify  --repo xo-space --channel canary --checkout ./xo-space         # is this install it?
qqinstall show                                                     # every channel
```

Exit codes, which scripts should decide on (never on the text): `0` resolved (or, for `verify`, the
checkout is exactly the channel's commit with no local changes), `1` `verify` mismatch, `2` error
(unreadable or invalid manifest, bad arguments), `3` not published yet. The manifest appears only
after release's first channel move, so until the daily canary pipeline (V0-REL-03) ships one,
every resolve exits `3`.

Every field is checked before it is used: the schema, a 40-hex commit, a `sha256:` digest, a
positive generation, names, no duplicate keys, a 1 MiB cap, https only (redirects too). One bad
entry anywhere and the whole file is refused.

**What it trusts.** The commit and digest come from the manifest, never from the artifact or
checkout being checked. The manifest is trusted because only release's executor can write
`release-state`. TODO(suraj): put `release-state` under the `qq-release-refs` ruleset (or its own)
so only the release executor identity can push it; until then anyone with push on quirq-ai/release
can change what a channel resolves to. TODO(expert): once `channels/<name>` refs are written
(release executor identity), cross-check the manifest's commit against the ref.

`tools/contract_check.py` is the done-when: release's own code, at the commit in `pins.toml`, ships
two canaries and rolls one back, and after each move `qqinstall resolve` must name the right commit
and digest (and exit `3` before the first move). Presubmit runs it, and also resolves the live
manifest, accepting `0` or `3`.

## v0 status

| Item | What | PR | State |
|---|---|---|---|
| V0-INS-01 | Channel manifest: resolve a channel to a commit and digest | #2 | in review |
| V0-INS-02 | xo-space test installs follow canary | | waits on V0-INS-01 |

Out of scope for v0: test installs following dev (v1); real installs following a channel and a
desktop updater evaluation (v2).

## Working here

See [AGENTS.md](AGENTS.md).

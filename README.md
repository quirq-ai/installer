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
qqinstall resolve  --repo xo-space --channel canary                 # commit, digest, generation
qqinstall resolve  --repo xo-space --channel canary --format env    # QQ_CHANNEL_COMMIT=... for scripts
qqinstall resolve  --repo xo-space --channel canary --at <release-state commit>   # a fixed read
qqinstall checkout --repo xo-space --channel canary --remote <git URL> --dest ./xo-space   # put an install on it
qqinstall verify   --repo xo-space --channel canary --checkout ./xo-space [--remote <git URL>]
qqinstall show                                                      # every channel
```

Exit codes, which scripts should decide on (never on the text): `0` resolved (or, for `checkout`
and `verify`, the checkout is exactly the channel's commit with no local changes), `1` mismatch or
local changes, `2` error (unreadable or invalid manifest, bad arguments, broken checkout or remote),
`3` not published yet. Treat anything but `0` as "not verified": Python itself exits `1` if
qqinstall cannot start. The manifest appears only after release's first channel move, so until the
daily canary pipeline (V0-REL-03) ships one, every resolve exits `3`.

`checkout` clones or fetches, then detaches the checkout at the manifest's commit by its id (never
by a branch or tag name the remote could point elsewhere) and verifies it. The commit must be on
the remote's `--branch` (default `main`), so a manifest cannot name an unmerged commit; that is
asked of a scratch repo fetched fresh from `--remote`, so nothing in the checkout's own `.git`
(grafts, a local `url.insteadOf`) can change the answer. Every symlink in the commit's tree must
name a path that tree has (not `.git`, an ignored path, or anything outside), checked before the
checkout moves. A shallow checkout is refused with a clear message. A first checkout that fails
after its clone is removed (an empty `--dest` is kept, empty), so the next run starts clean.
`--at` gets the same git hardening (CA settings kept, timeouts). It records the
manifest's generation in the checkout's git config (`qqinstall.<repo>/<channel>.generation`) and
refuses (exit `2`) a later `channels.json` with a lower generation, so a replayed old manifest
cannot move an install back; a rollback is a new move with a higher generation and goes through.
`resolve`, `verify` and `show` keep no state and cannot make that check (TODO(expert)). Anything
running later as the same user can change the record; recovery is in
[docs/xo-space-canary.md](docs/xo-space-canary.md#things-to-know).

**Limits.** `verify` checks the checkout's top-level commit (replace refs ignored), that its tree
has no tracked, untracked (non-ignored) or hidden (skip-worktree, assume-unchanged) changes, and,
with `--remote`, its `origin` as stored (before `url.insteadOf` rewrites), and refuses (exit `2`)
symlinks to paths outside its tree. It does not check the digest (that is for artifact installs) or
submodules, and it trusts the checkout's own `.git` (its filters, excludes and `core.fileMode` can
hide changes or run code), so it checks an install you control, not a hostile tree. The default
URL (`refs/heads/release-state`, so a same-named tag is never served) goes through GitHub's CDN,
which can lag a channel move by a few minutes; `--at <commit>` reads a fixed version and is
refused unless that commit is on release's `release-state` branch. Exit `3` also covers a 404 from
a renamed or private release repo; the scheduled `live-manifest` workflow tells the two apart.

Every field is checked before it is used: the schema, a 40-hex commit, a `sha256:` digest, a
positive generation, names, no duplicate keys, a 1 MiB cap, https only (redirects too). Anything
unexpected exits `2`, never `1`. One bad entry anywhere and the whole file is refused.

**What it trusts.** The commit and digest come from the manifest, never from the artifact or
checkout being checked. The manifest is trusted because only release's executor should write
`release-state`. TODO(suraj): today release pushes `release-state` with its workflows'
`GITHUB_TOKEN`, so a ruleset cannot tell the executor from any other workflow in release; it needs
release to push with the executor App's token and a ruleset whose only bypass is that App. Until
then anyone with push on quirq-ai/release can change what a channel resolves to.

`tools/contract_check.py` is the done-when: release's own code, at the commit in `pins.toml`, ships
two canaries and rolls one back, and after each move `qqinstall resolve` must name the right commit
and digest (and exit `3` before the first move). Presubmit runs it. The live manifest is read by the
scheduled, non-required `live-manifest` workflow, so release's live state never blocks a PR here.

## xo-space test installs on canary (V0-INS-02)

A test install checks before it runs, with no xo-space code change: `qqinstall checkout` puts
`./xo-space` on exactly canary's commit and verifies it, then that checkout's own install.sh,
started as `./xo-space/install.sh` from the test directory, runs it in place. Do not use `curl | sh` with `QUIRQ_SOURCE_REF` for canary: it installs whatever
the name `channels/canary` resolves to, and nothing in that chain is verified. Commands, caveats
and what the live half waits on: [docs/xo-space-canary.md](docs/xo-space-canary.md).
`tools/canary_install_drill.py` runs the flow against a local stand-in repo holding xo-space's real
`install.sh`, through two promotions, planted refs and a rollback; presubmit runs it.

TODO(suraj): V0-INS-02's scope changed. v0.md says test installs follow canary "through xo-space's
existing `QUIRQ_SOURCE_REF` override"; that flow resolves `channels/canary` by name and verifies
nothing, so it is now forbidden for canary and installs go through `qqinstall checkout` instead.
Please accept the change; v0.md needs the same update.

## v0 status

| Item | What | PR | State |
|---|---|---|---|
| V0-INS-01 | Channel manifest: resolve a channel to a commit and digest | #2 | merged; live resolve waits on the first canary (V0-REL-03) |
| V0-INS-02 | xo-space test installs follow canary | #3, audit fixes #5, #6 | merged; offline drill passes; live half waits on V0-REL-03 and suraj (canary machines, `release-state` protection) |

Out of scope for v0: test installs following dev (v1); real installs following a channel and a
desktop updater evaluation (v2).

## Working here

See [AGENTS.md](AGENTS.md).

## Licence

[Apache License 2.0](LICENSE).

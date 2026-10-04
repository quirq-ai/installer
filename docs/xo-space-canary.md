# xo-space test installs on canary (V0-INS-02)

How a test install of xo-space follows `channels/canary` instead of `main`, with no xo-space code
change. **Only in quirq's research and test environments, never on real users' machines.** Real
installs keep following `main` until v2.

## How it works

release's executor moves `channels/canary` in quirq-ai/xo-space: a branch naming the commit the
canary channel ships (the same commit `channels.json` names, with its artifact digest). xo-space's
`install.sh` already takes the branch to follow from `QUIRQ_SOURCE_REF` (default `main`): it clones
with `--branch "$QUIRQ_SOURCE_REF"`, and on later runs fetches that branch and resets a clean
checkout to it. So pointing `QUIRQ_SOURCE_REF` at `channels/canary` is all it takes.

## Install a test machine on canary

In a fresh directory on the test machine (a test install must be its own checkout, see below):

```sh
curl -fsSL https://quirq.ai/install | QUIRQ_SOURCE_REF=channels/canary sh
```

The variable goes on `sh`, not on `curl`. Per xo-space's INSTALLATION.md the bootstrap behind the
short URL downloads `install.sh` and runs it under bash, so the variable reaches install.sh, and
install.sh's own restart banner prints the command this way. The bootstrap's source is not in the
xo-space repo and could not be read from here, so which ref it fetches `install.sh` from is
unverified (inferred: `main`). TODO(expert): confirm against the served bootstrap.

Do **not** run `QUIRQ_SOURCE_REF=channels/canary ./install.sh` from inside an xo-space checkout:
install.sh then runs that checkout in place and never fetches, whatever the variable says. Without
the short URL, pipe the script from a fresh directory so it takes the managed path:
`QUIRQ_SOURCE_REF=channels/canary bash < /path/to/install.sh`.

To update, run the same command again. Each run moves the install to whatever canary names now,
including back to an older commit after a rollback.

Check what is installed against the channel (exit 0 = exactly canary's commit, no local changes;
1 = stale or edited; 3 = no canary published yet):

```sh
pip install "qqinstall @ git+https://github.com/quirq-ai/installer@<commit>"
qqinstall verify --repo xo-space --channel canary --checkout ./xo-space
```

## Things to know

- **A test install must be its own checkout.** install.sh never moves a clean checkout that is on
  another branch: an existing install on `main` stays on `main` even with
  `QUIRQ_SOURCE_REF=channels/canary` set, and install.sh then **starts the server on `main`
  anyway**, after printing "leaving it as is". Use a fresh directory, or `QUIRQ_APP_DIR`, and
  check with `qqinstall verify`.
- **Update by re-running install.sh, never the in-app updaters, to follow rollbacks.** Both in-app
  paths follow the checkout's current branch, so a canary install picks up newer canaries, but they
  only fast-forward and stay **silently** on a rolled-back commit: the Setup tab's status
  (`services/cowork_agent/self_update.py`, `check_update_status`) reports "up to date", applying
  reports "diverged", and `POST /app/update` (`cowork-update.sh`, `git pull --ff-only`) says
  "Already up to date" and exits 0. Re-running install.sh resets to the rolled-back commit.
  (Read from the code at the pinned xo-space commit and checked by the PR reviewer against the
  real module; the drill covers install.sh only.)
- **Local edits stop updates.** install.sh skips the update for a checkout with local changes;
  `qqinstall verify` exits 1 for one.
- **The installer script itself** is whatever the bootstrap serves, not necessarily canary's copy;
  only the code it installs follows the channel.
- **What is trusted.** The branch `channels/canary` in xo-space is the source of truth for the
  install; `channels.json` is the record `qqinstall verify` checks against. Both are written only
  by release's executor once the rulesets and identity below exist. A git install is identified by
  its commit; the digest in `channels.json` is for artifact installs (v1/v2).

## Status

`tools/canary_install_drill.py` is the offline half of the done-when, and presubmit runs it: it
runs xo-space's real `install.sh` (pinned commit and sha256 in `pins.toml`) with
`QUIRQ_SOURCE_REF=channels/canary` against a local repo whose `channels/canary` branch release's
own executor moves, through two promotions and a rollback, and checks with `qqinstall verify`
after each step. It also checks that an install on `main` is left alone.

The live half, a test install in the canary environment following the channel, waits on:

- TODO(suraj): the release executor identity (its GitHub App and `QQ_RELEASE_TOKEN`). Until it
  exists release records channel moves but does not write `channels/canary` in xo-space, so
  `QUIRQ_SOURCE_REF=channels/canary` has no branch to clone.
- V0-REL-03 (daily canary pipeline): the first canary promotion.
- TODO(suraj): name the canary test environment (which machines), and put `release-state` and
  `channels/*` under rulesets that only the release executor can write.

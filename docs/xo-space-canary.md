# xo-space test installs on canary (V0-INS-02)

How a test install of xo-space follows the canary channel instead of `main`, with no xo-space code
change. **Only in quirq's research and test environments, never on real users' machines.** Real
installs keep following `main` until v2.

## The rule: check, then run

A test install runs only a checkout that `qqinstall` has already checked is exactly the commit
release's `channels.json` names for xo-space canary. Nothing asks a git server what a branch or tag
name means, and nothing starts before the check passes.

## Install or update a test machine

Once, on the test machine (Python 3.11+ and git):

```sh
python3 -m venv ~/.qqinstall && ~/.qqinstall/bin/pip install \
  "qqinstall @ git+https://github.com/quirq-ai/installer@<reviewed installer commit>"
```

Then, in a directory used only for this test install (the test directory), every time you install
or update, run from that directory:

```sh
~/.qqinstall/bin/qqinstall checkout --repo xo-space --channel canary \
  --remote https://github.com/quirq-ai/xo-space.git --dest ./xo-space \
  && ./xo-space/install.sh
```

Run `./xo-space/install.sh` from the test directory, never `cd xo-space && ./install.sh`: install.sh
makes the directory it is started from the workspace (projects and `.quirq` state), so started from
inside the checkout the first project lands in the checkout and every later `checkout` exits `1`.

- `qqinstall checkout` reads `channels.json`, clones `./xo-space` the first time (fetches on
  later runs), refuses the commit unless it is on xo-space's `main`, detaches it at exactly that
  commit by its id, then verifies it: the commit, no tracked, untracked or hidden changes, no
  symlink to a path outside the commit's own tree, and `origin` is the URL given. Only exit `0` lets
  `install.sh` run. A first checkout that fails is removed, so the next run starts clean.
- `./xo-space/install.sh` runs that checkout in place and never runs git, so the code that starts,
  and install.sh itself, are canary's verified copy. What install.sh then downloads is **not**
  verified: if `uv` is missing it pipes uv's installer to `sh` unpinned, and it runs
  `uv pip install -r requirements.txt` without hashes. To narrow that, install uv beforehand by a
  pinned method (a release you checked, or your OS package manager), so install.sh finds it.
- Running the same command again follows promotions and rollbacks alike. It refuses (exit `2`)
  a `channels.json` older than the last one this checkout was put on (see "The generation record").
- Exit `1` (local changes) leaves the checkout as it is; `3` means nothing is published for
  canary yet; `2` is an error. Treat anything but `0` as "do not run" (Python itself exits `1` if
  qqinstall cannot start).
- To check a running install later: `qqinstall verify --repo xo-space --channel canary
  --remote https://github.com/quirq-ai/xo-space.git --checkout ./xo-space`.

## Do not use these for canary

- **`curl -fsSL https://quirq.ai/install | QUIRQ_SOURCE_REF=channels/canary sh`.** install.sh's
  update step is `git fetch origin channels/canary` then `reset --hard FETCH_HEAD`. git resolves
  that short name as `refs/channels/canary` first, then a **tag** `channels/canary`, and only then
  the branch, so whoever can create such a ref decides what runs. Branch and tag rulesets cannot
  cover `refs/channels/*`. The drill shows a planted ref being installed this way. The whole chain
  also verifies nothing: the bootstrap behind the short URL (its source is not in the xo-space repo
  and could not be read), the `install.sh` it fetches (inferred from `main`, no digest), uv's
  installer piped to `sh`, and `requirements.txt` without hashes. The server starts before
  anything could be checked.
- **The in-app updaters** (the Setup tab's `self_update.py` and `POST /app/update` →
  `cowork-update.sh`). They fetch by the same short name, and they only fast-forward: after a
  rollback the Setup tab says "up to date", applying says "diverged", and `/app/update` says
  "Already up to date" and exits 0, all while staying on the rolled-back commit. A checkout made by
  `qqinstall checkout` is detached, so they have no branch to follow; update with the command above.
- **`QUIRQ_APP_DIR` to put a canary install beside a `main` one.** In the same launch directory it
  shares the `main` install's `.quirq` state, projects root and port 5002. Use its own directory.

## Things to know

- **The generation record.** `checkout` stores the last generation it installed in the checkout's
  `.git/config` as `qqinstall.xo-space/canary.generation`. Repo content cannot change it during
  `checkout` (hooks and fsmonitor are off, includes are not read), but anything that runs later as
  the same user can, canary's own install.sh and server included: unsetting it lets a replayed
  older manifest through, raising it wedges updates. So it only defends against an old
  `channels.json` replayed from the network side. If release-state is ever reset (generations
  restart at 1) or a bad manifest raised it, every run exits `2`; after checking what canary
  should be, clear it with
  `git -C xo-space config --local --unset-all qqinstall.xo-space/canary.generation` and run the
  command again.

- `verify` trusts the checkout's own `.git` (filters, excludes, `core.fileMode`): it checks an
  install the operator controls, not a hostile tree. Replace refs are ignored. It counts files
  install.sh or the server create only if xo-space's `.gitignore` does not list them.
- The digest in `channels.json` is for artifact installs (v1/v2); a git install is identified by
  its commit.
- The trust root is `channels.json` on release's `release-state` branch. TODO(suraj): until only
  the release executor can push `release-state` (release pushes it with the workflow's
  `GITHUB_TOKEN` today, since release does not push as its executor App yet, so a ruleset cannot single the executor out; the audit routes this to
  release), anyone with push on quirq-ai/release can change what canary resolves to.
- Reads go through GitHub's CDN and can lag a channel move by a few minutes;
  `--at <release-state commit>` reads a fixed version and is refused unless that commit is on
  release's `release-state` branch.

## Status

`tools/canary_install_drill.py` is the offline half of the done-when, and presubmit runs it,
against a local stand-in repo holding xo-space's real `install.sh` (pinned commit and sha256 in
`pins.toml`), with release's own executor moving canary:

1. a fresh `qqinstall checkout` lands on canary's commit, and `./xo-space/install.sh` run from the
   test directory would run it in place (its `resolve_repo_dir` gives managed mode off, so no git)
   with the test directory as the workspace;
2. after a promotion the install reads as stale until `checkout` runs again;
3. a planted tag and a planted `refs/channels/canary` change nothing (and, for contrast, the
   `QUIRQ_SOURCE_REF` flow installs the planted commit);
4. after a rollback `checkout` goes back;
5. a checkout with local changes is left alone.

It does not run the rest of install.sh (uv, venv, server), the bootstrap, or anything on GitHub
(release's github backend, raw.githubusercontent.com).

The live half, a test install in the canary environment following the channel, waits on the
items below. (Release's first canary promotion, on 2026-10-05, published `channels.json`; the
`resolve` half works live.)

- TODO(suraj): name the canary test environment (which machines).
- TODO(suraj): protect `release-state` so only the release executor can push it (see above).

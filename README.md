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

## v0 status

| Item | What | PR | State |
|---|---|---|---|
| V0-INS-01 | Channel manifest: resolve a channel to a commit and digest | | not started |
| V0-INS-02 | xo-space test installs follow canary | | waits on V0-INS-01 |

Out of scope for v0: test installs following dev (v1); real installs following a channel and a
desktop updater evaluation (v2).

## Working here

See [AGENTS.md](AGENTS.md).

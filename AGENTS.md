# Agent guide

How an agent changes this repo safely. Read `README.md` first.

- Every change is a pull request against `main`, titled with its work item id (for example
  `V0-INS-01: ...`). It lands only with the `presubmit` check green.
- installer is a core repo: it names no product repo in code. Which repos and channels exist comes
  from infra-config and release's published `channels.json`; product names appear only in docs and
  test fixtures.
- It reads channels, it never moves them. Only release's executor moves `channels/*`.
- Trust a commit or digest only from a trusted record (`channels.json` from release, at a pinned
  commit when reproducibility matters), never from the artifact or checkout being checked.
- Decide outcomes from exit codes, not by matching another tool's output text.
- Other qq repos are used by pinned commit (`pins.toml`, `pyproject.toml`), never copied.
- Pin GitHub Actions by full commit SHA; `tools/check_pins.py` enforces it in presubmit.
- Leave `.github/CODEOWNERS` and any `owners` list empty; suraj assigns people.
- Mark a decision you cannot make with a one-line `TODO(suraj):` or `TODO(expert):`.
- This repo is public: no secrets, tokens or internal hostnames.

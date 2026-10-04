import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("check_pins", Path(__file__).parent.parent / "tools" / "check_pins.py")
check_pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_pins)
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def problems(tmp_path, text, rel=".github/workflows/x.yml"):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return check_pins.problems(tmp_path)


@pytest.mark.parametrize("line", [
    f"      - uses: actions/checkout@{SHA}  # v7",
    f"      - uses: 'actions/checkout@{SHA}'",
    f'        uses: "o/r/.github/workflows/w.yml@{SHA}"',
    "      - uses: ./.github/actions/x",
    "      - uses: docker://alpine@sha256:" + "a" * 64,
])
def test_accepts_pinned(tmp_path, line):
    assert problems(tmp_path, f"steps:\n{line}\n") == []


@pytest.mark.parametrize("line", [
    "      - uses: actions/checkout@v4",
    "      - uses: actions/checkout@main",
    "      - uses: actions/checkout",
    f"      - uses: actions/checkout@{SHA.upper()}",
    f"      - uses: actions/checkout@{SHA}#v4",
    "      - uses: docker://alpine:3",
    "      - {uses: actions/checkout@v4}",
    '      - "uses": actions/checkout@v4',
    "      - uses:\n          actions/checkout@v4",
])
def test_refuses_unpinned_or_unreadable(tmp_path, line):
    assert problems(tmp_path, f"steps:\n{line}\n")


def test_ignores_run_blocks(tmp_path):
    text = f"steps:\n  - run: |\n      echo uses: x@v1\n      uses: y@v1\n  - uses: a/b@{SHA}\n"
    assert problems(tmp_path, text) == []


def test_checks_composite_actions_anywhere(tmp_path):
    assert problems(tmp_path, "runs:\n  steps:\n    - uses: a/b@v1\n", rel="actions/foo/action.YML")


def test_this_repo_is_clean():
    assert check_pins.problems(Path(__file__).parent.parent) == []


@pytest.mark.parametrize("text", [
    "steps:\n  - name: x  # see: |\n    uses: actions/checkout@main\n",
    "steps:\n  - ? uses\n    : actions/checkout@main\n",
    'steps:\n  - "u\\x73es": actions/checkout@main\n',
    "steps:\n  - uses: ./.qq/infra-config/act\n",
    "steps:\n  - uses: ./.qq/x/../infra-config/act\n",
    "steps:\n  - uses: ./.git/act\n",
])
def test_review_fail_open_cases(tmp_path, text):
    assert problems(tmp_path, text)


def test_local_actions_in_other_dirs_are_scanned(tmp_path):
    assert problems(tmp_path, "runs:\n  steps:\n    - uses: a/b@main\n", rel=".qq/act/action.yml")
    assert problems(tmp_path, "runs:\n  steps:\n    - uses: a/b@main\n", rel="tools/.venv/act/action.yml")

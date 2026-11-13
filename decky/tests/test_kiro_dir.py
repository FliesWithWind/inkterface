"""The .kiro/ folder (Kiro specs, steering, hooks) is committed, well formed, and not gitignored.

The hackathon-style rule is: `.kiro` must be in the repository root and must not be gitignored. These tests keep that
true: the spec files exist, steering files have valid front matter, hook files parse into Kiro's v1 hook schema and
point at commands that exist in this repo, and `git check-ignore` does not match anything under `.kiro`.
"""
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KIRO = ROOT / ".kiro"
SPEC = KIRO / "specs" / "inkterface-v1"
STEERING_REQUIRED = {"product.md", "tech.md", "structure.md", "testing.md", "device-safety.md", "decky-ui.md"}
INCLUSION = {"always", "fileMatch", "manual", "auto"}
TRIGGERS = {"SessionStart", "Stop", "UserPromptSubmit", "PreTaskExec", "PostTaskExec", "PreToolUse", "PostToolUse",
            "PostFileCreate", "PostFileSave", "PostFileDelete"}


def front_matter(path: Path) -> dict[str, str]:
    """Parse the leading `---` block (flat `key: value` lines, which is all Kiro's inclusion modes use)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path.name}: no front matter at the very top of the file"
    fm = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        assert sep and re.fullmatch(r"[A-Za-z]+", key), f"{path.name}: bad front matter line {line!r}"
        fm[key] = value.strip()
    return fm


def hooks():
    for f in sorted((KIRO / "hooks").glob("*.json")):
        for h in json.loads(f.read_text(encoding="utf-8"))["hooks"]:
            yield f, h


@pytest.mark.parametrize("name", ["requirements.md", "design.md", "tasks.md"])
def test_spec_files_exist_and_are_not_empty(name):
    assert (SPEC / name).stat().st_size > 500


def test_requirements_are_ears_style_and_tasks_reference_them():
    req = (SPEC / "requirements.md").read_text(encoding="utf-8")
    numbered = re.findall(r"^\d+\. (.+)$", req, re.M)
    assert len(numbered) > 30
    assert all(re.search(r"\bSHALL\b", c) for c in numbered), [c for c in numbered if "SHALL" not in c]
    tasks = (SPEC / "tasks.md").read_text(encoding="utf-8")
    assert re.search(r"^\s*- \[x\] ", tasks, re.M) and re.search(r"^\s*- \[ \] ", tasks, re.M)
    refs = {m for line in re.findall(r"_Requirements: ([^_]+)_", tasks) for m in re.findall(r"\d+\.\d+", line)}
    defined = set()
    for m in re.finditer(r"### Requirement (\d+):(.*?)(?=\n### |\Z)", req, re.S):
        defined |= {f"{m.group(1)}.{i}" for i in range(1, len(re.findall(r"^\d+\. ", m.group(2), re.M)) + 1)}
    assert refs and refs <= defined, sorted(refs - defined)


def test_steering_files_exist():
    assert STEERING_REQUIRED <= {p.name for p in (KIRO / "steering").glob("*.md")}


@pytest.mark.parametrize("path", sorted((KIRO / "steering").glob("*.md")), ids=lambda p: p.name)
def test_steering_front_matter_is_valid(path):
    fm = front_matter(path)
    assert fm["inclusion"] in INCLUSION
    if fm["inclusion"] == "fileMatch":
        assert fm.get("fileMatchPattern"), "fileMatch needs fileMatchPattern"
    if fm["inclusion"] == "auto":
        assert fm.get("name") and fm.get("description"), "auto needs name and description"


def test_front_matter_parser_rejects_a_file_without_it(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("# no front matter\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        front_matter(f)


def test_hook_files_parse_into_the_v1_schema():
    files = sorted((KIRO / "hooks").glob("*.json"))
    assert len(files) >= 3
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["version"] == "v1" and data["hooks"], f.name
    seen = set()
    for f, h in hooks():
        assert h["name"] and h["name"] not in seen, (f.name, h["name"])
        seen.add(h["name"])
        assert h["trigger"] in TRIGGERS, h
        if "matcher" in h:
            re.compile(h["matcher"])
        action = h["action"]
        assert action["type"] in {"command", "agent"}
        assert action.get("prompt" if action["type"] == "agent" else "command")
        assert h.get("timeout", 60) >= 0


def test_hooks_run_the_real_checks():
    by_name = {h["name"]: h for _, h in hooks()}
    py, fe = by_name["pytest-on-python-edit"], by_name["frontend-checks-on-edit"]
    assert py["action"]["command"].startswith("python3 -m pytest decky/tests")
    assert fe["action"]["command"] == "cd decky && npx pnpm@9 typecheck && npx pnpm@9 test"
    # the matchers fire for the files the checks are about, and not for unrelated ones
    assert re.search(py["matcher"], "decky/py_modules/inkterface_core/diff.py") and not re.search(py["matcher"], "decky/src/api.ts")
    assert re.search(fe["matcher"], "decky/src/pages/StorePage.tsx") and re.search(fe["matcher"], "decky/test/api.test.ts")
    assert not re.search(fe["matcher"], "decky/main.py")
    fw = by_name["firmware-host-tests-on-edit"]
    assert re.search(fw["matcher"], "firmware/src/ee05/usbproto.cpp") and not re.search(fw["matcher"], "firmware/platformio.ini")


def test_hook_commands_name_files_that_exist():
    for _, h in hooks():
        cmd = h["action"].get("command", "")
        for tok in shlex.split(cmd):
            if re.match(r"(decky|tools|firmware|host_test|src)/[\w./-]+\.(py|cpp|sh)$", tok):
                base = ROOT / "firmware" if tok.startswith(("host_test/", "src/")) else ROOT
                assert (base / tok).exists(), (h["name"], tok)
    assert (ROOT / "decky" / "tests").is_dir() and (ROOT / "tools" / "check_file_sizes.py").is_file()


def test_kiro_is_not_gitignored():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    paths = [p.relative_to(ROOT).as_posix() for p in KIRO.rglob("*") if p.is_file()]
    assert len(paths) >= 13
    # (--no-index: a file already tracked would otherwise never report as ignored) git check-ignore exits 1 when none of the paths is ignored, 0 when at least one is
    r = subprocess.run(["git", "check-ignore", "--no-index", "-v", "--", ".kiro", ".kiro/", *paths], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1, f"ignored: {r.stdout}"
    for p in paths:
        assert subprocess.run(["git", "check-ignore", "--no-index", "-q", "--", p], cwd=ROOT).returncode != 0, p


def test_kiro_files_carry_no_machine_specific_paths():
    for p in KIRO.rglob("*"):
        if p.is_file():
            assert not re.search(r"/home/|/Users/|[A-Za-z]:\\\\", p.read_text(encoding="utf-8")), p

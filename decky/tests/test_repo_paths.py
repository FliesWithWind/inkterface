"""Docs and CI must not point at files that do not exist (e.g. after a file is moved or deleted).

Checks, over every *.md in the repo and .github/workflows/*.yml:
  1. markdown links [text](relative/path#anchor): target exists, and for .md targets the anchor is a heading;
  2. inline-code repo paths (`decky/...`, `firmware/...`, `./docs/...`): exist (globs must match something);
  3. workflow references to repo paths (decky/..., firmware/..., tools/..., docs/...): exist.
Build outputs and gitignored files are allowed to be missing, but a path a doc names that exists only
because it is gitignored (a local log) is an error: a clean checkout would not have it.
"""
import glob
import re
import subprocess
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOP = ("decky", "firmware", "docs", "tools", "boards", "design", "assets", ".github")
SKIP_DIRS = {".git", "node_modules", ".pio", "py_modules", "dist", "out", "__pycache__"}
GENERATED = ("/.pio/", "/dist/", "/out/", "/node_modules/", "src/ee05/idle_config.h",
             "decky/py_modules/PIL", "decky/py_modules/certifi")  # build output (vendor.sh), gitignored

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+|<[^>]+>)(?:\s+\"[^\"]*\")?\)")
CODE = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
WF_PATH = re.compile(r"(?<![\w/.-])((?:%s)/[\w./*+-]+)" % "|".join(re.escape(t) for t in TOP))


def md_files(root: Path):
    for p in sorted(root.rglob("*.md")):
        if not SKIP_DIRS & set(p.relative_to(root).parts):
            yield p


def slug(heading: str) -> str:
    """GitHub heading anchor: lowercase, drop punctuation except - and _, spaces to -."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(md: Path) -> set[str]:
    text = FENCE.sub("", md.read_text(encoding="utf-8"))
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)}


def exists(base: Path, rel: str) -> bool:
    if any(g in "/" + rel for g in GENERATED):
        return True
    return bool(glob.glob(str(base / rel))) if any(c in rel for c in "*?[") else (base / rel).exists()


def untracked_ignored(root: Path, rel: str) -> bool:
    """True if `rel` exists here only as a gitignored file (so it is missing on a clean checkout). False outside a git repo."""
    if any(g in "/" + rel for g in GENERATED) or any(c in rel for c in "*?[") or not (root / ".git").exists() or not (root / rel).exists():
        return False
    return subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=root).returncode == 0


def check(root: Path = ROOT) -> list[str]:
    errs: list[str] = []
    for md in md_files(root):
        rel_md = md.relative_to(root).as_posix()
        text = md.read_text(encoding="utf-8")
        prose = FENCE.sub("", text)
        for m in LINK.finditer(prose):
            target = m.group(1).strip("<>")
            if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):  # https:, mailto:, ...
                continue
            path, _, frag = urllib.parse.unquote(target).partition("#")
            dest = md if not path else (md.parent / path)
            if not dest.exists():
                errs.append(f"{rel_md}: broken link ({target})")
            elif frag and dest.suffix == ".md" and slug(frag) not in anchors(dest):
                errs.append(f"{rel_md}: broken anchor ({target})")
        for m in CODE.finditer(prose):
            tok = m.group(1).strip()
            tok = re.sub(r"^\./", "", tok).rstrip("/,.:;")
            if " " in tok or re.search(r"[<>{}$|=()]", tok) or not tok.startswith(tuple(t + "/" for t in TOP)):
                continue
            if not (exists(root, tok) or exists(md.parent, tok)):  # repo-relative, or relative to the doc
                errs.append(f"{rel_md}: missing path `{tok}`")
            elif untracked_ignored(root, tok):
                errs.append(f"{rel_md}: path `{tok}` is gitignored, so a clean checkout does not have it")
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        for m in WF_PATH.finditer(wf.read_text(encoding="utf-8")):
            tok = m.group(1).rstrip("/.,")
            if not exists(root, tok):
                errs.append(f"{wf.relative_to(root).as_posix()}: missing path {tok}")
    return errs


def test_repo_references_resolve():
    errs = check()
    assert not errs, "\n".join(errs)


def test_docs_index_links_every_doc():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    linked = {(ROOT / "docs" / urllib.parse.unquote(m.group(1).strip("<>").partition("#")[0])).resolve() for m in LINK.finditer(index)}
    docs = {p.resolve() for p in (ROOT / "docs").rglob("*") if p.is_file() and p.name != "README.md"}
    assert docs - linked == set(), sorted(str(p.relative_to(ROOT)) for p in docs - linked)


# --- the checker itself must fail when it should -------------------------------------------------

def _mk(tmp_path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_checker_accepts_good_refs(tmp_path):
    root = _mk(tmp_path, {
        "readme.md": "# T\n[a](docs/a.md#the-heading) [b](<docs/with space.md>) `./docs/a.md` `firmware/*.ini` [x](https://e.com/nope)\n",
        "docs/a.md": "# A\n## The heading\n[up](../readme.md)\n```\n[bad](nope.md) `decky/gone`\n```\n",
        "docs/with space.md": "x",
        "firmware/p.ini": "",
        ".github/workflows/w.yml": "run: python decky/tests/x.py firmware/.pio/build/ee05/firmware.bin\n",
        "decky/tests/x.py": "",
    })
    assert check(root) == []


def test_checker_flags_each_kind_of_breakage(tmp_path):
    root = _mk(tmp_path, {
        "readme.md": "[gone](docs/gone.md) [anchor](docs/a.md#nope) `src/main.cpp` `decky/removed/` `firmware/*.nothing`\n",
        "docs/a.md": "# A\n",
        ".github/workflows/w.yml": "run: ./scripts/build.sh && cp decky/missing.py x\n",
    })
    errs = "\n".join(check(root))
    for needle in ("broken link (docs/gone.md)", "broken anchor (docs/a.md#nope)", "missing path `decky/removed`",
                   "missing path `firmware/*.nothing`", "w.yml: missing path decky/missing.py"):
        assert needle in errs, (needle, errs)
    assert "src/main.cpp" not in errs  # not under a known top-level dir: not a repo-path claim


def test_a_documented_path_that_is_only_a_gitignored_local_file_is_flagged(tmp_path):
    root = _mk(tmp_path, {"docs/a.md": "# A\n`decky/local-runs/run.log` `decky/local-runs/kept.log` `decky/local-runs/ok.json`\n",
                          "decky/.gitignore": "*.log\n!local-runs/kept.log\n",
                          "decky/local-runs/run.log": "", "decky/local-runs/kept.log": "", "decky/local-runs/ok.json": "{}"})
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert check(root) == ["docs/a.md: path `decky/local-runs/run.log` is gitignored, so a clean checkout does not have it"]

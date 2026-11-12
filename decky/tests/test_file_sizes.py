"""tools/check_file_sizes.py: no tracked file over 2 MB unless allowlisted, and the allowlist has no padding."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_file_sizes", REPO / "tools" / "check_file_sizes.py")
cfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfs)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def add(name, size):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x" * size)
        subprocess.run(["git", "add", name], cwd=tmp_path, check=True)
    return tmp_path, add


def test_flags_only_tracked_files_over_the_limit(repo):
    root, add = repo
    add("small.bin", 100)
    add("exact.bin", 1000)
    add("big.bin", 1001)
    add("sub dir/huge.bin", 5000)
    (root / "untracked.bin").write_bytes(b"x" * 9999)   # not tracked: not our business
    assert sorted(cfs.too_big(root, set(), limit=1000)) == [("big.bin", 1001), ("sub dir/huge.bin", 5000)]


def test_allowlisted_old_files_pass(repo):
    root, add = repo
    add("old.bin", 5000)
    add("new.bin", 5000)
    assert cfs.too_big(root, {"old.bin"}, limit=1000) == [("new.bin", 5000)]


def test_a_tracked_file_deleted_from_disk_is_skipped(repo):
    root, add = repo
    add("gone.bin", 5000)
    (root / "gone.bin").unlink()
    assert cfs.too_big(root, set(), limit=1000) == []


def test_allowlist_parsing_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("# why\n\nsome dir/file.pdf\n  # indented comment\nb.bin\n")
    assert cfs.allowlist(f) == {"some dir/file.pdf", "b.bin"}


def test_this_repo_has_no_unlisted_large_file_and_the_allowlist_is_honest():
    allowed = cfs.allowlist(REPO / "tools" / "large-files-allowlist.txt")
    assert cfs.too_big(REPO, allowed) == []
    tracked = set(cfs.tracked(REPO))
    for rel in allowed:   # every entry is a real tracked file that really is over the limit (no stale or padding entries)
        assert rel in tracked and (REPO / rel).stat().st_size > cfs.LIMIT, rel

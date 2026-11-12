"""docs/screen-packages.md must match the code's limits and the CLI it documents."""
from pathlib import Path

from inkterface_core.packages import extract as X
from inkterface_core.packages import sandbox as S

DOC = (Path(__file__).resolve().parents[3] / "docs" / "screen-packages.md").read_text()
MiB = 1024 * 1024


def test_limits_in_doc_match_code():
    assert X.MAX_ZIP_BYTES == 5 * MiB and "| Zip file size | 5 MiB |" in DOC
    assert X.MAX_FILE_BYTES == 4 * MiB and X.MAX_TOTAL_BYTES == 20 * MiB and "4 MiB per file, 20 MiB total" in DOC
    assert X.MAX_FILES == 200 and "at most 200" in DOC
    assert X.MAX_RATIO == 100 and "ratio above 100:1" in DOC
    assert S.TIMEOUT_S == 2.0 and "timeout of 2 s" in DOC
    assert S.MEMORY_MB == 512 and "(512 MB)" in DOC
    assert S.FSIZE_MB == 2 and "file size (2 MB)" in DOC
    assert S.MAX_RESPONSE_BYTES == MiB and "1 MiB cap" in DOC


def test_doc_covers_the_workflow_and_the_limits():
    for needle in ("--allow-code", "--package", "make_package.py", "not a sandbox", "sha256", "hello-code-screen",
                   "revokes the approval", "RLIMIT_NPROC"):
        assert needle in DOC, needle
    assert (Path(__file__).resolve().parents[2] / "examples" / "make_package.py").is_file()

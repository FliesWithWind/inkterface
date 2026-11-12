"""readme.md: the Printables link is in the top badge row and in "Build one", the badges are there, the sections are in
order, and every relative link, image and anchor resolves (test_repo_paths skips images and HTML tags)."""
import json
import re
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "readme.md").read_text(encoding="utf-8")
PRINTABLES = json.loads((ROOT / "hardware" / "bom.yaml").read_text(encoding="utf-8"))["printables_url"]
H2 = re.findall(r"^## (.+)$", README, re.M)


def section(name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", README, re.M | re.S)
    assert m, name
    return m.group(1)


def test_printables_link_is_in_the_top_badge_row():
    top = README[:README.index("\n## ")]
    badge = re.search(rf'<a href="{re.escape(PRINTABLES)}"><img alt="Print it on Printables" src="(https://img\.shields\.io/badge/[^"]+)"', top)
    assert badge and "logo=printables" in badge.group(1)
    assert top.index(PRINTABLES) < top.index("actions/workflows/ci.yml")  # first in the row


def test_top_row_has_ci_release_and_licence_badges():
    top = README[:README.index("\n## ")]
    assert "actions/workflow/status/FliesWithWind/inkterface/ci.yml" in top and "(/actions/workflows/ci.yml)" not in top
    assert re.search(r'github/v/release/FliesWithWind/inkterface\?[^"]*include_prereleases', top)
    assert 'github/license/FliesWithWind/inkterface' in top and 'href="LICENSE"' in top


def test_build_one_has_three_steps_with_the_printables_link_in_bold():
    body = section("Build one")
    steps = re.findall(r"^(\d)\. (.+)$", body, re.M)
    assert [n for n, _ in steps] == ["1", "2", "3"]
    assert "(#parts-list)" in steps[0][1]
    assert f"**[Print the case on Printables]({PRINTABLES})**" in steps[1][1]
    assert "(docs/flashing.md)" in steps[2][1] and "(#install)" in steps[2][1]
    assert "no battery" in body.lower() and "no soldering" in body.lower() and "under the Steam Machine" in body


def test_printables_url_matches_the_bom_and_is_not_a_placeholder():
    assert PRINTABLES.startswith("https://www.printables.com/model/")
    assert README.count(PRINTABLES) >= 3  # badge, Build one, generated case text


def test_section_order_and_unique_headings():
    assert len(H2) == len(set(H2))
    order = ["Support", "Build one", "Features", "Screenshots", "Hardware", "Install", "Quick start", "Screens and the Screen Store", "Docs",
             "Contributing", "Security", "License and credits"]
    assert H2 == order


def _local(ref: str) -> bool:
    return not re.match(r"[a-z][a-z0-9+.-]*:|#", ref, re.I)


def test_support_is_in_the_upper_part_between_the_notice_and_the_hero():
    assert README.index("**Use at your own risk.**") < README.index("## Support") < README.index("live-telemetry.webp") < README.index("## Build one")
    assert README.count("## Support") == 1 and README.count("githubbutton_sm.svg") == 1


def test_every_relative_link_image_and_anchor_resolves():
    prose = re.sub(r"^```.*?^```", "", README, flags=re.S | re.M)
    refs = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)", prose) + re.findall(r'(?:src|href)="([^"]+)"', prose)
    assert len(refs) > 20
    headings = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in re.findall(r"^#{1,6} (.+)$", prose, re.M)}
    for ref in refs:
        if ref.startswith("#"):
            assert ref[1:] in headings, ref
        elif _local(ref):
            assert (ROOT / urllib.parse.unquote(ref.partition("#")[0])).exists(), ref
    toc = re.findall(r"\]\(#([\w-]+)\)", prose)
    assert {"build-one", "install", "support", "license-and-credits"} <= set(toc) and all(t in headings for t in toc)

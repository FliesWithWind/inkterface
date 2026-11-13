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
    assert top.index(PRINTABLES) < top.index("https://ko-fi.com/inkterface") < top.index("actions/workflows/ci.yml")  # Ko-fi next to it


def test_top_row_has_ci_release_and_licence_badges():
    top = README[:README.index("\n## ")]
    assert "actions/workflow/status/FliesWithWind/inkterface/ci.yml" in top and "(/actions/workflows/ci.yml)" not in top
    fw = re.search(r'github/v/release/FliesWithWind/inkterface\?([^"]*)', top)
    assert fw and "include_prereleases" in fw.group(1)
    assert "display_name=release" in fw.group(1) and "filter=Firmware*" in fw.group(1)  # reads "Firmware 1.0.0-beta.2", not the raw fw-v tag
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
    order = ["Build one", "Features", "Screenshots", "Hardware", "Install", "Quick start", "Screens and the Screen Store", "Docs",
             "Contributing", "Security", "Support", "License and credits"]
    assert H2 == order


def _local(ref: str) -> bool:
    return not re.match(r"[a-z][a-z0-9+.-]*:|#", ref, re.I)


def test_support_badge_is_up_top_and_the_full_section_is_just_before_license():
    assert README.index("https://ko-fi.com/inkterface") < README.index("## Build one") < README.index("## Support") < README.index("## License and credits")
    assert H2.index("Support") == H2.index("License and credits") - 1
    assert README.count("## Support") == 1 and README.count("githubbutton_sm.svg") == 1


def test_header_is_compact_and_the_hero_follows_the_badges():
    head = README[:README.index("\n## ")]
    assert re.search(r'<h1 align="center"><img src="assets/valve/icon256.png" alt="" width="(9\d|1[0-2]\d)"', head)  # icon 90-129 px, not 256
    assert 'Library_Logo.png" alt="Inkterface" width="320"' in head
    assert head.index("Library_Logo.png") < head.index("<b>An e-ink faceplate") < head.index("ci.yml") < head.index("steam-machine-front.jpg") < head.index("live-telemetry.webp")


def test_beta_notice_is_a_github_warning_alert():
    assert "> [!WARNING]\n> **Beta.**" in README and "\n> **Use at your own risk.**" in README


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

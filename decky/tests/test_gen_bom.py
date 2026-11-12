"""tools/gen_bom.py: the generated tables match hardware/bom.yaml, no affiliate tag appears unless one is set,
the disclosure is shown only then, and the committed readme.md / docs/hardware.md are up to date."""
import copy
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gen_bom", ROOT / "tools" / "gen_bom.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

DATA = gen.load()
MARKETS = ["amazon.pl", "amazon.de", "amazon.fr", "amazon.es", "amazon.it", "amazon.co.uk", "amazon.com"]


def blank():
    """The BOM with every affiliate setting emptied."""
    d = copy.deepcopy(DATA)
    d["affiliate"]["amazon"] = {k: "" for k in d["affiliate"]["amazon"]}
    d["affiliate"]["seeed"]["code"] = ""
    d["affiliate"]["openelab"]["code"] = ""
    d["affiliate"]["aliexpress"]["short_key"] = ""
    for part in d["parts"]:  # the Portals short links are affiliate links by themselves
        part["links"].pop("aliexpress", None)
    return d


def with_tags(**amazon):
    d = blank()
    d["affiliate"]["amazon"].update(amazon)
    return d


def test_bom_file_is_valid_and_has_the_five_parts():
    assert json.loads((ROOT / "hardware" / "bom.yaml").read_text())  # valid JSON (and so valid YAML)
    assert [(p["qty"], p["part"]) for p in DATA["parts"]] == [
        ("1", "XIAO ePaper Display Board (ESP32-S3) EE05"), ("1", '5.83" monochrome ePaper display'),
        ("4", "Magnets"), ("13", "M2.5x5 flat head hex socket screw (304 stainless)"), ("1", "USB-C to USB 2.0 cable, 0.5 m")]
    cable = {k: u for k, u in DATA["parts"][4]["links"].items() if k.startswith("amazon.")}
    assert list(cable) == MARKETS and all(u == f"https://www.{k}/dp/B07NSQ56DP" for k, u in cable.items())
    assert set(DATA["affiliate"]["amazon"]) == set(MARKETS)
    assert not any("hagglezon" in u for p in DATA["parts"] for u in p["links"].values())


def test_screw_row_is_the_flat_head_hex_socket_screw():
    row = next(r for r in gen.render_bom(DATA).splitlines() if "screw" in r)
    assert row.startswith("| 13 | M2.5x5 flat head hex socket screw (304 stainless) | M2.5 thread, 5 mm long, flat head, hex socket |")
    assert "pan head" not in gen.render_bom(DATA) and "[AliExpress](https://s.click.aliexpress.com/e/_c2Izsl8P)" in row


def test_table_has_one_row_per_part_with_every_link():
    rows = gen.render_bom(DATA).splitlines()[2:]
    assert len(rows) == len(DATA["parts"])
    for row, part in zip(rows, DATA["parts"]):
        assert row.startswith(f"| {part['qty']} | {part['part']} |")
        for k, url in part["links"].items():
            assert f"]({gen._tag(url, k, DATA['affiliate'])})" in row
        if not part["links"]:
            assert "no link yet" in row


def test_amazon_links_are_one_compact_country_row():
    cable = gen.render_bom(DATA).splitlines()[-1]
    assert " Amazon [PL](" in cable and cable.count("Amazon") == 1
    order = [cable.index(f"[{c}](") for c in ("PL", "DE", "FR", "ES", "IT", "UK", "US")]
    assert order == sorted(order) and cable.count(" · ") == 6


def test_every_generated_amazon_url_carries_its_own_marketplace_tag():
    out = gen.render_bom(DATA)
    for host in MARKETS:
        assert f"](https://www.{host}/dp/B07NSQ56DP?tag={DATA['affiliate']['amazon'][host]})" in out
    assert len({t for t in DATA["affiliate"]["amazon"].values()}) == len(MARKETS)  # one distinct tag each
    assert out.count("tag=") == len(MARKETS) * 3  # cable, screws and magnets


def amazon_links(part_name):
    part = next(p for p in DATA["parts"] if p["part"].startswith(part_name))
    return {k: u for k, u in part["links"].items() if k.startswith("amazon.")}


def test_screws_and_magnets_have_confirmed_amazon_asins_with_the_right_tag():
    screws, magnets = amazon_links("M2.5x5"), amazon_links("Magnets")
    assert list(screws) == MARKETS and all(u == f"https://www.{k}/dp/B0G6ZCMST1" for k, u in screws.items())  # one ASIN everywhere
    assert list(magnets) == MARKETS
    for k, u in magnets.items():  # that ASIN is a 404 on amazon.com, which has its own listing
        assert u == f"https://www.{k}/dp/{'B0H3TFLG55' if k == 'amazon.com' else 'B075P5M649'}"
    out = gen.render_bom(DATA)
    for host in MARKETS:
        tag = DATA["affiliate"]["amazon"][host]
        assert f"](https://www.{host}/dp/B0G6ZCMST1?tag={tag})" in out
        asin = "B0H3TFLG55" if host == "amazon.com" else "B075P5M649"
        assert f"](https://www.{host}/dp/{asin}?tag={tag})" in out


def test_each_amazon_row_is_one_compact_country_row():
    rows = [r for r in gen.render_bom(DATA).splitlines() if "Amazon [PL]" in r]
    assert len(rows) == 3 and all(r.count(" · ") == 6 and r.count("Amazon") == 1 for r in rows)


def test_untagged_when_settings_are_empty_for_the_new_rows():
    out = gen.render_bom(blank())
    assert "B0G6ZCMST1)" in out and "B075P5M649)" in out and "B0H3TFLG55)" in out and "tag=" not in out


def test_seeed_urls_carry_the_code_and_openelab_is_never_tagged():
    out = gen.render_bom(DATA)
    assert out.count("sensecap_affiliate=dlPNUD2&referring_service=link)") == 2  # board and panel
    for p in DATA["parts"]:
        for k, u in p["links"].items():
            if k == "openelab":
                assert f"]({u})" in out and "?" not in u  # exact, untagged URL
    assert gen._tag("https://openelab.io/products/x", "openelab", DATA["affiliate"]) == "https://openelab.io/products/x"


def test_disclosure_is_present_and_short():
    text = gen.render_affiliate(DATA)
    assert text.startswith("**Affiliate links.**") and "no extra cost" in text and "Amazon Associate" in text
    assert len(text) < 300
    for rel in gen.TARGETS:
        assert text in (ROOT / rel).read_text(encoding="utf-8")


def test_no_tag_anywhere_when_none_is_set():
    d = blank()
    assert not gen.has_affiliate(d)
    out = gen.render_bom(d)
    assert "tag=" not in out and "sensecap_affiliate" not in out and "referring_service" not in out and "?" not in out
    assert gen.render_affiliate(d) == ""


def test_amazon_tag_is_added_only_to_its_marketplace():
    d = with_tags(**{"amazon.pl": "me-21"})
    out = gen.render_bom(d)
    assert "https://www.amazon.pl/dp/B07NSQ56DP?tag=me-21)" in out
    assert "amazon.de/dp/B07NSQ56DP)" in out and "amazon.com/dp/B07NSQ56DP)" in out  # untouched
    assert "seeedstudio.com/XIAO-ePaper-Display-Board-EE05-p-6755.html)" in out
    assert "Amazon Associate" in gen.render_affiliate(d)


def test_seeed_link_has_the_form_seeeds_own_generator_emits():
    d = blank()
    d["affiliate"]["seeed"]["code"] = "  "
    assert "sensecap_affiliate" not in gen.render_bom(d) and not gen.has_affiliate(d)  # blank code: no tag
    d["affiliate"]["seeed"]["code"] = "WsITKVq"
    out = gen.render_bom(d)
    assert gen.has_affiliate(d)
    assert "-p-6755.html?sensecap_affiliate=WsITKVq&referring_service=link)" in out
    assert "-p-5785.html?sensecap_affiliate=WsITKVq&referring_service=link)" in out
    assert "openelab.io/products/seeed-studio-xiao-epaper-board-ee05)" in out  # other stores stay plain
    assert "tag=" not in out  # and the Amazon links are untouched


def test_seeed_tag_replaces_a_stale_one_and_keeps_other_query():
    aff = {"seeed": {"code": "NEW"}, "amazon": {}}
    url = gen._tag("https://www.seeedstudio.com/x-p-1.html?a=1&sensecap_affiliate=OLD", "seeed", aff)
    assert url == "https://www.seeedstudio.com/x-p-1.html?a=1&sensecap_affiliate=NEW&referring_service=link"


def test_each_amazon_marketplace_uses_its_own_tag_and_unknown_stores_are_never_tagged():
    aff = {"seeed": {"code": "C"}, "amazon": {"amazon.pl": "pl-21", "amazon.de": "de-21", "amazon.co.uk": "uk-21"},
           "openelab": {"code": ""}, "aliexpress": {"short_key": ""}}
    assert gen._tag("https://www.amazon.de/dp/X", "amazon.de", aff).endswith("?tag=de-21")
    assert gen._tag("https://www.amazon.co.uk/dp/X", "amazon.co.uk", aff).endswith("?tag=uk-21")
    assert gen._tag("https://www.amazon.com/dp/X", "amazon.com", aff) == "https://www.amazon.com/dp/X"  # no tag for .com
    assert gen._tag("https://openelab.io/products/x", "openelab", aff) == "https://openelab.io/products/x"


def test_existing_query_is_kept_and_a_stale_tag_replaced():
    url = gen._tag("https://www.amazon.de/dp/X?th=1&tag=old", "amazon.de", {"amazon": {"amazon.de": "new-21"}})
    assert url == "https://www.amazon.de/dp/X?th=1&tag=new-21"


def test_openelab_referral_is_sca_ref_and_only_when_a_code_is_set():
    aff = blank()["affiliate"]
    url = "https://openelab.io/products/seeed-studio-xiao-epaper-board-ee05"
    assert gen._tag(url, "openelab", aff) == url  # empty: plain link
    aff["openelab"]["code"] = " 123456.AbCdEf "
    assert gen._tag(url, "openelab", aff) == url + "?sca_ref=123456.AbCdEf"
    assert gen._tag(url + "?a=1&sca_ref=old", "openelab", aff) == url + "?a=1&sca_ref=123456.AbCdEf"
    assert gen._tag("https://www.seeedstudio.com/x-p-1.html", "seeed", aff) == "https://www.seeedstudio.com/x-p-1.html"  # no cross-over


def test_aliexpress_link_is_a_deep_link_wrapper_only_when_a_key_is_set():
    aff = blank()["affiliate"]
    url = "https://www.aliexpress.com/item/1005000000000000.html?spm=a2g0o"
    assert gen._tag(url, "aliexpress", aff) == url  # empty: plain link
    aff["aliexpress"]["short_key"] = "_AbC123"
    out = gen._tag(url, "aliexpress", aff)
    assert out == ("https://s.click.aliexpress.com/deep_link.htm?dl_target_url="
                   "https%3A%2F%2Fwww.aliexpress.com%2Fitem%2F1005000000000000.html%3Fspm%3Da2g0o&aff_short_key=_AbC123")
    assert gen._tag("https://openelab.io/x", "openelab", aff) == "https://openelab.io/x"  # only AliExpress links are wrapped


def test_new_stores_render_in_the_table_and_switch_the_disclosure_on():
    d = blank()
    d["parts"][2]["links"] = {"aliexpress": "https://www.aliexpress.com/item/1.html", "openelab": "https://openelab.io/products/m"}
    plain = gen.render_bom(d)
    assert "| [OpenELAB](https://openelab.io/products/m) | [AliExpress](https://www.aliexpress.com/item/1.html) |" in plain
    assert "deep_link" not in plain and "sca_ref" not in plain and gen.render_affiliate(d) == ""
    d["affiliate"]["aliexpress"]["short_key"] = "K"
    assert "[AliExpress](https://s.click.aliexpress.com/deep_link.htm?" in gen.render_bom(d) and gen.has_affiliate(d)
    d["affiliate"]["aliexpress"]["short_key"] = ""
    d["affiliate"]["openelab"]["code"] = "1.X"
    assert "[OpenELAB](https://openelab.io/products/m?sca_ref=1.X)" in gen.render_bom(d)
    out = gen.render_affiliate(d)
    assert out.startswith("**Affiliate links.**") and "Amazon Associate" not in out


PORTALS = {"magnets": "https://s.click.aliexpress.com/e/_c3g72c8j", "screws": "https://s.click.aliexpress.com/e/_c2Izsl8P"}


def test_portals_short_links_pass_through_untouched_even_with_a_short_key():
    aff = blank()["affiliate"]
    for short in PORTALS.values():
        assert gen._tag(short, "aliexpress", aff) == short
        aff["aliexpress"]["short_key"] = "_AbC123"
        assert gen._tag(short, "aliexpress", aff) == short  # never wrapped again
        aff["aliexpress"]["short_key"] = ""


def test_aliexpress_column_renders_links_and_leaves_other_rows_blank():
    out = gen.render_bom(DATA).splitlines()
    assert "| AliExpress |" in out[0] and out[1].count("|") == out[0].count("|")
    rows = [[c.strip() for c in r.strip("|").split(" | ")] for r in out[2:]]
    ali = {r[1]: r[4] for r in rows}
    for part in DATA["parts"]:
        url = part["links"].get("aliexpress")
        assert ali[part["part"]] == (f"[AliExpress]({url})" if url else "")
    # a part without an AliExpress link has a blank cell, and keeps "no link yet" only when it has no link at all
    d = copy.deepcopy(DATA)
    d["parts"][2]["links"] = {}
    d["parts"][3]["links"] = {"aliexpress": PORTALS["screws"]}
    r2, r3 = gen.render_bom(d).splitlines()[4:6]
    assert r2.split(" | ")[3:5] == ["no link yet", ""] and r3.split(" | ")[3].strip() == ""


def test_shipped_aliexpress_links_are_portals_short_links_for_every_part():
    links = [p["links"].get("aliexpress") for p in DATA["parts"]]
    assert all(u and u.startswith("https://s.click.aliexpress.com/e/_") and "?" not in u for u in links) and len(set(links)) == len(links)
    assert all(f"]({u})" in gen.render_bom(DATA) for u in links)


def test_a_short_link_alone_switches_the_disclosure_on():
    d = blank()
    assert gen.render_affiliate(d) == ""
    d["parts"][2]["links"] = {"aliexpress": PORTALS["magnets"]}
    assert gen.has_affiliate(d) and gen.render_affiliate(d).startswith("**Affiliate links.**")
    assert "Amazon Associate" not in gen.render_affiliate(d)  # that sentence needs an Amazon tag


def test_disclosure_only_when_a_tag_is_set():
    d = blank()
    d["affiliate"]["seeed"]["code"] = "C"  # a Seeed code alone also triggers it, without the Amazon sentence
    assert gen.render_affiliate(d).startswith("**Affiliate links.**") and "Amazon" not in gen.render_affiliate(d)
    assert gen.render_affiliate(with_tags(**{"amazon.com": "x-20"})).startswith("**Affiliate links.**")
    assert gen.render_affiliate(with_tags(**{"amazon.com": "  "})) == ""


def with_support(sponsors="", kofi="", button=""):
    d = copy.deepcopy(DATA)
    d["support"].update(github_sponsors=sponsors, kofi=kofi, kofi_button_id=button)
    return d


def funding(tmp_path, data, write=True):
    """Run the sync and return the FUNDING.yml text, or None when the file does not exist."""
    stale = gen.sync_funding(data, tmp_path, write=write)
    f = tmp_path / ".github" / "FUNDING.yml"
    return stale, (f.read_text() if f.exists() else None)


SECTION = "Inkterface is free and open source. If it is useful to you, you can support its development on "


def test_support_with_nothing_set_has_no_section_and_no_funding_file(tmp_path):
    d = with_support("  ", " ")
    assert gen.render_support(d) == ""
    text = "a\n<!-- gen:support -->\nstale\n<!-- /gen:support -->\nb\n"
    assert gen.update(text, d) == "a\n<!-- gen:support -->\n<!-- /gen:support -->\nb\n"  # no heading, no link
    assert funding(tmp_path, d) == (False, None) and not (tmp_path / ".github").exists()


def test_support_with_only_github_sponsors(tmp_path):
    d = with_support(sponsors="Some-User")
    assert gen.render_support(d) == f"## Support\n\n{SECTION}[GitHub Sponsors](https://github.com/sponsors/Some-User)."
    assert "ko-fi" not in gen.render_support(d)
    assert funding(tmp_path, d) == (True, "github: [Some-User]\n")


def test_support_with_only_kofi(tmp_path):
    d = with_support(kofi="some_user1")
    assert gen.render_support(d) == f"## Support\n\n{SECTION}[Ko-fi](https://ko-fi.com/some_user1)."
    assert "sponsors" not in gen.render_support(d)
    assert funding(tmp_path, d) == (True, "ko_fi: some_user1\n")


BUTTON = "[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/W7W72AIPDL)"


def test_kofi_button_is_generated_when_kofi_and_the_button_id_are_set():
    d = with_support(kofi="inkterface", button="W7W72AIPDL")
    assert gen.kofi_button(d) == BUTTON
    assert gen.render_support(d).endswith(f"[Ko-fi](https://ko-fi.com/inkterface).\n\n{BUTTON}")  # the text link stays, the button follows it


def test_kofi_button_is_absent_when_unset():
    assert gen.kofi_button(with_support(kofi="inkterface")) == ""  # no button id
    assert gen.kofi_button(with_support(button="W7W72AIPDL")) == ""  # no Ko-fi username: nothing to point at
    assert gen.kofi_button(with_support(sponsors="U", button="W7W72AIPDL")) == ""
    assert "ko-fi.com/img" not in gen.render_support(with_support(kofi="inkterface"))


def test_kofi_button_id_must_be_upper_case_letters_and_digits():
    for bad in ("w7w72aipdl", "W7W 72", "W7W/72", "W7W72\nx", "https://ko-fi.com/W7W72AIPDL", "W7_W"):
        try:
            gen.kofi_button(with_support(kofi="inkterface", button=bad))
        except ValueError:
            continue
        raise AssertionError(bad)


NOTICE = ("> ⚠️ **Beta.** Inkterface is beta software and hardware. Expect bugs, rough edges and changes between releases.\n>\n"
          "> **Use at your own risk.** This is a hobby project, provided as is, with no warranty of any kind (see [LICENSE](LICENSE)). "
          "The author is not responsible for any damage to your Steam Machine, the display, the EE05 board, USB ports or any other hardware, "
          "or for any data loss, from building, flashing or using it.")


def test_readme_top_order_notice_before_build_one_and_support_once_after_the_security_section():
    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    assert NOTICE in readme
    assert readme.index("<h1") < readme.index(NOTICE) < readme.index("## Build one") < readme.index("## Features")
    assert readme.index("## Security") < readme.index("## Support") < readme.index("## License and credits")
    assert readme.count("<!-- gen:support -->") == 1


def test_support_appears_once_and_the_button_once():
    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    assert readme.count("## Support") == 1 and readme.count("https://ko-fi.com/img/githubbutton_sm.svg") == 1
    assert readme.count(BUTTON) == 1 and gen.kofi_button(DATA) == BUTTON


def test_support_with_both(tmp_path):
    d = with_support("Some-User", "some_user1")
    assert gen.render_support(d) == (f"## Support\n\n{SECTION}[GitHub Sponsors](https://github.com/sponsors/Some-User) "
                                     "or [Ko-fi](https://ko-fi.com/some_user1).")
    assert funding(tmp_path, d) == (True, "github: [Some-User]\nko_fi: some_user1\n")


def test_funding_file_follows_the_settings_over_time(tmp_path):
    both, only_kofi, none = with_support("U", "k"), with_support(kofi="k"), with_support()
    assert funding(tmp_path, both, write=False) == (True, None)  # --check writes nothing
    assert funding(tmp_path, both)[0] is True and funding(tmp_path, both) == (False, "github: [U]\nko_fi: k\n")  # then up to date
    assert funding(tmp_path, only_kofi) == (True, "ko_fi: k\n")  # a key is dropped
    assert funding(tmp_path, none) == (True, None)  # unset: the file is removed


def test_support_usernames_must_be_plain():
    for key in ("kofi", "github_sponsors"):
        for bad in ("a b", "x/y", "me\nfoo: bar", "https://ko-fi.com/x", "[x]"):
            d = with_support()
            d["support"][key] = bad
            try:
                gen.render_support(d)
            except ValueError:
                continue
            raise AssertionError((key, bad))


def test_repo_state_matches_the_support_settings():
    funding_file = ROOT / ".github" / "FUNDING.yml"
    sponsors, kofi = gen.support_user(DATA, "github_sponsors"), gen.support_user(DATA, "kofi")
    assert funding_file.exists() == bool(sponsors or kofi)
    assert ("## Support" in (ROOT / "readme.md").read_text(encoding="utf-8")) == bool(sponsors or kofi)


def test_enclosure_text_credits_valve_and_follows_the_printables_setting():
    unset = copy.deepcopy(DATA)
    unset["printables_url"] = ""  # the shipped file has the link; the empty setting keeps the placeholder
    out = gen.render_printed(unset)
    assert "[Valve's Inkterface case](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface)" in out
    assert "remix" in out and "modified for the EE05" in out and "cad/" not in out and "Battery" not in out
    assert "Its print files are on Printables (link coming) under CC BY-NC-SA 4.0." in out
    assert "Valve's original design is MIT (Copyright (c) 2026 Valve Software)" in out and "THIRD_PARTY_NOTICES.md" in out
    d = copy.deepcopy(DATA)
    d["printables_url"] = "https://www.printables.com/model/1"
    out = gen.render_printed(d)
    assert "Its print files are on [Printables](https://www.printables.com/model/1) under CC BY-NC-SA 4.0." in out and "coming" not in out


def test_shipped_printables_link_replaces_the_placeholder_everywhere():
    url = DATA["printables_url"]
    assert url.startswith("https://www.printables.com/model/1897091-")
    for rel in gen.TARGETS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "<!-- gen:printed -->" in text:
            assert text.count(f"[Printables]({url})") == 1 and "link coming" not in text, rel
    assert "link coming" not in gen.render_printed(DATA) and f"[Printables]({url}) under CC BY-NC-SA 4.0" in gen.render_printed(DATA)


def test_update_is_idempotent_and_rewrites_only_the_blocks():
    text = "head\n<!-- gen:bom -->\nold\n<!-- /gen:bom -->\n<!-- gen:affiliate -->\nstale\n<!-- /gen:affiliate -->\ntail\n"
    once = gen.update(text, DATA)
    assert once.startswith("head\n<!-- gen:bom -->\n| Qty |") and once.endswith("<!-- /gen:affiliate -->\ntail\n")
    assert "stale" not in once and gen.update(once, DATA) == once


def test_committed_docs_are_up_to_date():
    for rel in gen.TARGETS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert gen.update(text, DATA) == text, f"{rel} is stale: run python3 tools/gen_bom.py"
        assert "<!-- gen:bom -->\n| Qty |" in text

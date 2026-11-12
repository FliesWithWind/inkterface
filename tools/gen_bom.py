#!/usr/bin/env python3
"""Generate the bill-of-materials blocks from hardware/bom.yaml (JSON syntax, so no YAML library is needed).

    python3 tools/gen_bom.py            rewrite the generated blocks in readme.md and docs/hardware.md
    python3 tools/gen_bom.py --check    exit 1 if a block is stale (nothing is written)

A block sits between `<!-- gen:NAME -->` and `<!-- /gen:NAME -->`; NAME is bom, printed, affiliate or support.
`support.github_sponsors` and `support.kofi` in bom.yaml feed the README "Support" section and .github/FUNDING.yml, each listing only the
keys that are set; with neither set the section and the file do not exist.
An affiliate tag is added to a link only when its setting in bom.yaml is non-empty. The disclosure block is empty
unless at least one tag is set.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("readme.md", "docs/hardware.md")
STORE_LABEL = {"seeed": "Seeed", "openelab": "OpenELAB", "aliexpress": "AliExpress"}  # any other key is shown as is
AMAZON_COUNTRY = {"amazon.pl": "PL", "amazon.de": "DE", "amazon.fr": "FR", "amazon.es": "ES", "amazon.it": "IT", "amazon.co.uk": "UK", "amazon.com": "US"}


def load(path: Path = ROOT / "hardware" / "bom.yaml") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tag(url: str, key: str, aff: dict) -> str:
    """Return `url` with the store's affiliate tracking added, or unchanged when its setting is empty."""
    if key == "aliexpress":  # Portals deep link: the product URL wrapped in a tracking redirect that carries the short key
        short_key = aff["aliexpress"]["short_key"].strip()
        if not short_key or urlsplit(url).hostname == "s.click.aliexpress.com":  # a short link made in Portals is used verbatim
            return url
        return "https://s.click.aliexpress.com/deep_link.htm?" + urlencode({"dl_target_url": url, "aff_short_key": short_key})
    if key == "seeed":  # Seeed's own link generator ("PROMO DATA" in the account) emits ?sensecap_affiliate=CODE&referring_service=link
        code = aff["seeed"]["code"].strip()
        params = [("sensecap_affiliate", code), ("referring_service", "link")] if code else []
    elif key == "openelab":  # the store's Shopify affiliate app appends ?sca_ref=<id>.<key> to any store URL (copy it from the dashboard link)
        code = aff["openelab"]["code"].strip()
        params = [("sca_ref", code)] if code else []
    elif key.startswith("amazon."):  # one Associates tag per marketplace
        tag = aff["amazon"].get(key, "").strip()
        params = [("tag", tag)] if tag else []
    else:
        params = []
    if not params:
        return url
    parts = urlsplit(url)
    drop = {k for k, _ in params}
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in drop] + params
    return urlunsplit(parts._replace(query=urlencode(query)))


def has_affiliate(data: dict) -> bool:
    aff = data["affiliate"]
    short_links = any(urlsplit(u).hostname == "s.click.aliexpress.com" for p in data["parts"] for u in p["links"].values())
    return short_links or any(v.strip() for v in aff["amazon"].values()) or any(
        aff[store][field].strip() for store, field in (("seeed", "code"), ("openelab", "code"), ("aliexpress", "short_key")))


def render_bom(data: dict) -> str:
    rows = ["| Qty | Part | Spec | Where to buy | AliExpress | Notes |", "|----:|------|------|--------------|------------|-------|"]
    for p in data["parts"]:
        link = lambda k, u, text: f"[{text}]({_tag(u, k, data['affiliate'])})"
        shops = [link(k, u, STORE_LABEL.get(k, k)) for k, u in p["links"].items() if k not in AMAZON_COUNTRY and k != "aliexpress"]
        amazon = [link(k, u, AMAZON_COUNTRY[k]) for k, u in p["links"].items() if k in AMAZON_COUNTRY]  # one compact row: PL · DE · ...
        links = " / ".join(shops + (["Amazon " + " · ".join(amazon)] if amazon else []))
        ali = link("aliexpress", p["links"]["aliexpress"], "AliExpress") if "aliexpress" in p["links"] else ""
        cells = [p["qty"], p["part"], p["spec"], links or ("" if ali else "no link yet"), ali, p["notes"]]
        rows.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(rows)


def render_printed(data: dict) -> str:
    url = data["printables_url"].strip()
    where = f"[Printables]({url})" if url else "Printables (link coming)"
    return ("The enclosure is a remix of [Valve's Inkterface case](https://gitlab.steamos.cloud/SteamHardware/SteamMachine/inkterface), "
            f"modified for the EE05. Its print files are on {where} under CC BY-NC-SA 4.0. Valve's original design is MIT "
            "(Copyright (c) 2026 Valve Software), see THIRD_PARTY_NOTICES.md and Valve's GitLab repository.")


def render_affiliate(data: dict) -> str:
    if not has_affiliate(data):
        return ""
    text = ("**Affiliate links.** Some store links in the parts list are affiliate links: if you buy through them, "
            "the project may earn a small commission at no extra cost to you.")
    if any(v.strip() for v in data["affiliate"]["amazon"].values()):
        text += " As an Amazon Associate I earn from qualifying purchases."  # wording Amazon's Operating Agreement asks for
    return text


def support_user(data: dict, key: str) -> str:
    """A funding username from `support` ('' when unset). Only letters, digits, `_` and `-` are accepted: it goes into a URL and a YAML file."""
    user = data.get("support", {}).get(key, "").strip()
    if user and not re.fullmatch(r"[\w-]+", user):
        raise ValueError(f"support.{key} must be a plain username, got {user!r}")
    return user


def kofi_button(data: dict) -> str:
    """The Ko-fi button image link, or '' unless both `support.kofi` and `support.kofi_button_id` (Ko-fi's button code, A-Z and 0-9) are set."""
    code = data.get("support", {}).get("kofi_button_id", "").strip()
    if code and not re.fullmatch(r"[A-Z0-9]+", code):
        raise ValueError(f"support.kofi_button_id must be upper-case letters and digits, got {code!r}")
    return f"[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/{code})" if code and support_user(data, "kofi") else ""


def render_support(data: dict) -> str:
    sponsors, kofi = support_user(data, "github_sponsors"), support_user(data, "kofi")
    links = ([f"[GitHub Sponsors](https://github.com/sponsors/{sponsors})"] if sponsors else []) + ([f"[Ko-fi](https://ko-fi.com/{kofi})"] if kofi else [])
    if not links:
        return ""
    button = kofi_button(data)
    return ("## Support\n\nInkterface is free and open source. If it is useful to you, you can support its development on "
            + " or ".join(links) + "." + (f"\n\n{button}" if button else ""))


def sync_funding(data: dict, root: Path = ROOT, write: bool = True) -> bool:
    """Make .github/FUNDING.yml match the settings: only the keys that are set, and no file when none is (GitHub shows a broken
    button for an empty value). Returns True when the file was out of date."""
    path = root / ".github" / "FUNDING.yml"
    sponsors, kofi = support_user(data, "github_sponsors"), support_user(data, "kofi")
    lines = ([f"github: [{sponsors}]"] if sponsors else []) + ([f"ko_fi: {kofi}"] if kofi else [])
    want = "\n".join(lines) + "\n" if lines else None
    have = path.read_text(encoding="utf-8") if path.exists() else None
    if want == have:
        return False
    if write:
        if want is None:
            path.unlink()
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(want, encoding="utf-8")
    return True


def render(name: str, data: dict) -> str:
    return {"bom": render_bom, "printed": render_printed, "affiliate": render_affiliate, "support": render_support}[name](data)


def update(text: str, data: dict) -> str:
    """Replace every generated block in `text`."""
    def sub(m: re.Match) -> str:
        body = render(m.group(1), data)
        return f"<!-- gen:{m.group(1)} -->\n{body}\n<!-- /gen:{m.group(1)} -->" if body else f"<!-- gen:{m.group(1)} -->\n<!-- /gen:{m.group(1)} -->"
    return re.sub(r"<!-- gen:(\w+) -->.*?<!-- /gen:\1 -->", sub, text, flags=re.S)


def main(argv: list[str]) -> int:
    data, stale = load(), []
    for rel in TARGETS:
        path = ROOT / rel
        old = path.read_text(encoding="utf-8")
        new = update(old, data)
        if new != old:
            stale.append(rel)
            if "--check" not in argv:
                path.write_text(new, encoding="utf-8")
    if sync_funding(data, write="--check" not in argv):
        stale.append(".github/FUNDING.yml")
    if "--check" in argv and stale:
        print("stale (run python3 tools/gen_bom.py):", *stale)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

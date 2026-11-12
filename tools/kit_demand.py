#!/usr/bin/env python3
"""Summarise the DIY kit requests (issues with the `kit-request` label made from .github/ISSUE_TEMPLATE/kit-request.yml).

    python3 tools/kit_demand.py                       read the issues with `gh issue list` (needs the gh CLI, run inside a clone)
    python3 tools/kit_demand.py --repo OWNER/NAME     the same for another repository
    python3 tools/kit_demand.py --file issues.json    read `gh issue list --json number,body,reactionGroups` output instead

Prints requests by kit type, the quantity sum, the region and the price band. A thumbs-up on a request counts as one more person asking for
the same kit type, region and price band, with a quantity of 1.
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter

KITS = ("Full DIY kit", "Electronics only", "Printed case only", "Fully assembled panel")  # the options of the form start with these
UNSET = "(not given)"
MAX_QUANTITY = 1000  # one absurd number must not drown the totals


def sections(body: str) -> dict[str, str]:
    """The `### Heading` blocks of a rendered issue form: {heading: text}. GitHub writes `_No response_` for an empty optional field."""
    out = {}
    for m in re.finditer(r"^### (.+?)\s*\n(.*?)(?=^### |\Z)", body or "", re.S | re.M):
        text = m.group(2).strip()
        out[m.group(1).strip()] = "" if text == "_No response_" else text
    return out


def quantity(text: str) -> int:
    m = re.search(r"\d+", text or "")
    return min(max(int(m.group()), 1), MAX_QUANTITY) if m else 1


def thumbs_up(issue: dict) -> int:
    return sum(g.get("users", {}).get("totalCount", 0) for g in issue.get("reactionGroups") or [] if g.get("content") == "THUMBS_UP")


def summarise(issues: list[dict]) -> dict:
    kits, regions, prices = Counter(), Counter(), Counter()
    units = Counter()
    requests = people = 0
    for issue in issues:
        s = sections(issue.get("body", ""))
        kit_text = s.get("What would you like?", "")
        if not kit_text:  # not made from the form
            continue
        kit = next((name for name in KITS if kit_text.startswith(name)), kit_text)
        region, price = s.get("Where would it ship?") or UNSET, s.get("Price you would pay for a full kit") or UNSET
        extra = thumbs_up(issue)
        n = quantity(s.get("How many?", ""))
        requests += 1
        people += 1 + extra
        kits[kit] += 1 + extra
        units[kit] += n + extra
        regions[region] += 1 + extra
        prices[price] += 1 + extra
    return {"requests": requests, "people": people, "kits": kits, "units": units, "regions": regions, "prices": prices}


def render(summary: dict) -> str:
    lines = [f"{summary['requests']} requests, {summary['people']} people (thumbs-ups included), {sum(summary['units'].values())} units", ""]
    for title, counter in (("Kit type (people)", summary["kits"]), ("Kit type (units)", summary["units"]),
                           ("Region (people)", summary["regions"]), ("Price band for a full kit (people)", summary["prices"])):
        lines.append(title)
        lines += [f"  {n:4d}  {name}" for name, n in counter.most_common()] or ["     -"]
        lines.append("")
    return "\n".join(lines).rstrip()


def load(args: argparse.Namespace) -> list[dict]:
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return json.load(f)
    cmd = ["gh", "issue", "list", "--label", "kit-request", "--state", "all", "--limit", "1000", "--json", "number,body,reactionGroups"]
    if args.repo:
        cmd += ["--repo", args.repo]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo")
    ap.add_argument("--file")
    print(render(summarise(load(ap.parse_args(argv)))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

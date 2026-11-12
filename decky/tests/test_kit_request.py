"""The DIY kit request issue form, its readme link and tools/kit_demand.py (which reads the issues the form creates)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
spec = importlib.util.spec_from_file_location("kit_demand", ROOT / "tools" / "kit_demand.py")
kd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kd)

# The form is JSON, which is valid YAML 1.2 (like hardware/bom.yaml), so the test needs no YAML library.
FORM = json.loads((TEMPLATES / "kit-request.yml").read_text(encoding="utf-8"))
FIELDS = {b["attributes"]["label"]: b for b in FORM["body"] if "label" in b["attributes"]}


def test_form_header_and_label():
    assert FORM["name"] and FORM["description"]
    assert FORM["title"] == "[Kit request] " and FORM["labels"] == ["kit-request"]
    assert [b["type"] for b in FORM["body"]][0] == "markdown"
    intro = FORM["body"][0]["attributes"]["value"]
    assert "One request per person" in intro and "👍" in intro


def test_required_and_optional_fields():
    required = {k for k, b in FIELDS.items() if b.get("validations", {}).get("required")}
    assert required == {"What would you like?", "How many?", "Where would it ship?"}
    optional = {"Price you would pay for a full kit", "Case colour", "Anything else?"}
    assert optional <= set(FIELDS) and all(FIELDS[k]["validations"]["required"] is False for k in optional)
    assert FIELDS["How many?"]["type"] == "input" and FIELDS["How many?"]["attributes"]["value"] == "1"


def test_dropdown_options():
    opts = lambda k: FIELDS[k]["attributes"]["options"]
    assert [o.split(" (")[0] for o in opts("What would you like?")] == list(kd.KITS)
    assert opts("Where would it ship?") == ["Poland", "Other EU country", "United Kingdom", "United States", "Canada", "Other"]
    assert opts("Price you would pay for a full kit") == ["under 60 EUR", "60-80 EUR", "80-100 EUR", "100-130 EUR", "over 130 EUR"]
    assert opts("Case colour") == ["Black", "White", "Grey", "No preference"]


def test_both_acknowledgements_are_required_checkboxes():
    boxes = next(b for b in FORM["body"] if b["type"] == "checkboxes")["attributes"]["options"]
    assert len(boxes) == 2 and all(o["required"] for o in boxes)
    assert "demand check, not an order" in boxes[0]["label"] and "no payment is taken" in boxes[0]["label"]
    assert "address, email or phone number" in boxes[1]["label"]


def test_existing_templates_are_kept_and_blank_issues_stay_on():
    assert (TEMPLATES / "bug_report.md").exists() and (TEMPLATES / "screen_package.md").exists()
    assert "blank_issues_enabled: true" in (TEMPLATES / "config.yml").read_text()


def test_readme_links_the_form_once_in_build_one():
    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    link = "https://github.com/FliesWithWind/inkterface/issues/new?template=kit-request.yml"
    assert readme.count(link) == 1 and readme.count("Want a DIY kit?") == 1
    assert readme.index("## Build one") < readme.index(link) < readme.index("## Features")


def issue_body(kit, qty, region, price="_No response_", colour="_No response_"):
    return (f"### What would you like?\n\n{kit}\n\n### How many?\n\n{qty}\n\n### Where would it ship?\n\n{region}\n\n"
            f"### Price you would pay for a full kit\n\n{price}\n\n### Case colour\n\n{colour}\n\n### Anything else?\n\n_No response_\n\n"
            "### Before you submit\n\n- [x] I understand this is a demand check, not an order\n- [x] I will not post my address")


def thumbs(n):
    return [{"content": "THUMBS_UP", "users": {"totalCount": n}}, {"content": "HEART", "users": {"totalCount": 9}}]


ISSUES = [
    {"number": 1, "body": issue_body("Full DIY kit (EE05 board, 5.83\" panel, printed case, magnets, screws, USB cable)", "2", "Poland", "60-80 EUR"),
     "reactionGroups": thumbs(2)},
    {"number": 2, "body": issue_body("Electronics only (EE05 board + panel)", "1", "United States"), "reactionGroups": []},
    {"number": 3, "body": issue_body("Full DIY kit (EE05 board, 5.83\" panel, printed case, magnets, screws, USB cable)", "lots", "Other EU country", "60-80 EUR"),
     "reactionGroups": None},
    {"number": 4, "body": issue_body("Printed case only (with magnets and screws)", "5000", "Canada"), "reactionGroups": []},
    {"number": 5, "body": "A normal issue, not made from the form", "reactionGroups": thumbs(7)},
    {"number": 6, "body": None, "reactionGroups": []},
]


def test_sections_and_empty_fields():
    s = kd.sections(ISSUES[1]["body"])
    assert s["What would you like?"].startswith("Electronics only") and s["How many?"] == "1"
    assert s["Case colour"] == "" and s["Before you submit"].startswith("- [x]")
    assert kd.sections("") == {} and kd.sections(None) == {}


def test_quantity_parsing_is_forgiving():
    assert [kd.quantity(t) for t in ("2", " 3 kits ", "lots", "", "0", "5000", "-4")] == [2, 3, 1, 1, 1, kd.MAX_QUANTITY, 4]


def test_summary_counts_thumbs_up_as_one_more_person_and_skips_other_issues():
    s = kd.summarise(ISSUES)
    assert s["requests"] == 4 and s["people"] == 4 + 2  # the two thumbs-ups on request 1
    assert s["kits"] == {"Full DIY kit": 4, "Electronics only": 1, "Printed case only": 1}
    assert s["units"]["Full DIY kit"] == (2 + 2) + 1  # request 1: 2 units + one per thumbs-up, request 3: "lots" counts as 1
    assert s["units"]["Printed case only"] == kd.MAX_QUANTITY
    assert s["regions"] == {"Poland": 3, "United States": 1, "Other EU country": 1, "Canada": 1}
    assert s["prices"] == {"60-80 EUR": 4, kd.UNSET: 2}


def test_render_and_file_input(tmp_path, capsys):
    path = tmp_path / "issues.json"
    path.write_text(json.dumps(ISSUES), encoding="utf-8")
    assert kd.main(["--file", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("4 requests, 6 people (thumbs-ups included), ") and "Full DIY kit" in out and "Poland" in out
    assert kd.render(kd.summarise([])).startswith("0 requests, 0 people") and "-" in kd.render(kd.summarise([]))

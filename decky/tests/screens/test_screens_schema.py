import copy
import json
import os

import pytest
from PIL import Image
from screens_testlib import png_bytes, valid_doc

from inkterface_core.screens import ScreenError, load_screen, render, validate_screen
from inkterface_core.screens.schema import resolve_settings


def W(**kw):
    base = {"type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": "hi"}
    base.update(kw)
    return base


def doc_with(*widgets, **over):
    d = valid_doc(**over)
    d["active"] = {"widgets": list(widgets)}
    return d


def errs(doc, **kw):
    return validate_screen(doc, **kw)


def has(errors, fragment):
    assert any(fragment in e for e in errors), f"{fragment!r} not in {errors}"


# ---- file level -----------------------------------------------------------------------

def test_valid_package_loads(make_pkg):
    s = load_screen(make_pkg(valid_doc(description="d", settings=[{"key": "k", "type": "bool", "default": True}])))
    assert (s.id, s.name, s.version, s.author, s.description) == ("demo", "Demo", "1.0.0", "me", "d")
    assert s.refresh_s == 1.0 and s.code_path is None and s.idle is None
    assert s.assets_dir.name == "assets"
    assert s.resolve_settings() == {"k": True}


def test_missing_screen_json(tmp_path):
    with pytest.raises(ScreenError, match="screen.json: not found"):
        load_screen(tmp_path)


def test_invalid_json(make_pkg):
    with pytest.raises(ScreenError, match="invalid JSON"):
        load_screen(make_pkg(None, raw="{not json"))


def test_duplicate_json_keys(make_pkg):
    with pytest.raises(ScreenError, match="duplicate key 'id'"):
        load_screen(make_pkg(None, raw='{"id": "a", "id": "b"}', name="a"))


def test_nan_constant_rejected(make_pkg):
    with pytest.raises(ScreenError, match="invalid number"):
        load_screen(make_pkg(None, raw='{"x": NaN}'))


def test_non_utf8(tmp_path):
    (tmp_path / "screen.json").write_bytes(b"\xff\xfe")
    with pytest.raises(ScreenError, match="invalid JSON"):
        load_screen(tmp_path)


def test_oversized_json(make_pkg):
    with pytest.raises(ScreenError, match="too large"):
        load_screen(make_pkg(None, raw=" " * (256 * 1024 + 1)))


def test_all_errors_reported_with_prefix(make_pkg):
    d = doc_with(W(bind="x"), W(type="nope"), id="demo")
    with pytest.raises(ScreenError) as e:
        load_screen(make_pkg(d))
    assert len(e.value.errors) >= 2 and all(m.startswith("screen.json: ") for m in e.value.errors)


# ---- top level --------------------------------------------------------------------------

def test_top_level_not_object():
    has(errs([]), "must be an object")


def test_unknown_top_key_has_suggestion():
    has(errs(valid_doc(descripton="x")), "descripton: unknown key (did you mean 'description'?)")


@pytest.mark.parametrize("key", ["schema", "id", "name", "version", "author"])
def test_required_top_keys(key):
    d = valid_doc()
    del d[key]
    has(errs(d), f"{key}: required")


def test_schema_version():
    has(errs(valid_doc(schema=2)), "schema: must be 1")


@pytest.mark.parametrize("bad", ["Demo", "has space", "a_b", "-a", "a--b", "", 5])
def test_bad_ids(bad):
    assert any(e.startswith("id:") for e in errs(valid_doc(id=bad)))


def test_id_must_match_folder(make_pkg):
    with pytest.raises(ScreenError, match="must match the folder name 'other'"):
        load_screen(make_pkg(valid_doc(), name="other"))


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "", "1.0.x", 1])
def test_bad_versions(bad):
    has(errs(valid_doc(version=bad)), "version:")


def test_semver_prerelease_ok():
    assert errs(valid_doc(version="1.2.3-rc.1+build5")) == []


def test_long_strings_rejected():
    has(errs(valid_doc(name="x" * 81)), "name: too long")
    has(errs(valid_doc(author="")), "author: must be a non-empty string")
    has(errs(valid_doc(description="x" * 501)), "description: too long")


def test_needs_active_or_idle():
    d = valid_doc()
    del d["active"]
    has(errs(d), "needs an 'active' or an 'idle' section")


# ---- active section ---------------------------------------------------------------------------

def test_active_not_object_and_unknown_key():
    has(errs(valid_doc(active=[])), "active: must be an object")
    d = valid_doc()
    d["active"]["widgetz"] = []
    has(errs(d), "active.widgetz: unknown key")


def test_active_widgets_required():
    has(errs(valid_doc(active={})), "active.widgets: required")


def test_active_size():
    d = valid_doc()
    d["active"]["size"] = [800, 600]
    has(errs(d), "active.size: must be [648, 480]")
    d["active"]["size"] = [648, 480]
    assert errs(d) == []


@pytest.mark.parametrize("bad", [0, 0.05, 4000, "1", True])
def test_refresh_s_bounds(bad):
    d = valid_doc()
    d["active"]["refresh_s"] = bad
    has(errs(d), "active.refresh_s")


def test_refresh_s_property(make_pkg):
    d = valid_doc()
    d["active"]["refresh_s"] = 0.5
    assert load_screen(make_pkg(d)).refresh_s == 0.5


def test_triggers():
    d = valid_doc()
    d["active"]["triggers"] = {"on_game_launch": "yes"}
    has(errs(d), "active.triggers.on_game_launch: must be bool")
    d["active"]["triggers"] = {"on_boot": True}
    has(errs(d), "active.triggers.on_boot: unknown key")
    d["active"]["triggers"] = {"on_game_launch": True}
    assert errs(d) == []


def test_widgets_container_checks():
    has(errs(valid_doc(active={"widgets": {}})), "active.widgets: must be an array")
    has(errs(doc_with(*[W() for _ in range(201)])), "too many widgets")
    has(errs(doc_with(5)), "active.widgets[0]: must be an object")
    has(errs(doc_with(W(id="a"), W(id="a"))), "active.widgets[1].id: duplicate id 'a'")


def test_unknown_and_missing_widget_type():
    has(errs(doc_with(W(type="sparkine"))), "active.widgets[0].type: unknown widget type 'sparkine' (did you mean 'sparkline'?)")
    w = W()
    del w["type"]
    has(errs(doc_with(w)), "active.widgets[0].type: required")


# ---- geometry -----------------------------------------------------------------------------------

@pytest.mark.parametrize("kw,frag", [
    ({"x": "1"}, "x: must be an integer"),
    ({"y": 1.5}, "y: must be an integer"),
    ({"x": -1}, "x: must be >= 0"),
    ({"y": -1}, "y: must be >= 0"),
    ({"w": 0}, "w: must be >= 1"),
    ({"h": 0}, "h: must be >= 1"),
    ({"x": 600, "w": 100}, "x: widget extends past the 648px edge (x+w = 700)"),
    ({"y": 470, "h": 20}, "y: widget extends past the 480px edge (y+h = 490)"),
])
def test_geometry_rules(kw, frag):
    has(errs(doc_with(W(**kw))), "active.widgets[0]." + frag)


def test_geometry_exact_fit_ok():
    assert errs(doc_with(W(x=0, y=0, w=648, h=480))) == []


def test_geometry_required():
    w = W()
    del w["w"]
    has(errs(doc_with(w)), "active.widgets[0].w: required")


# ---- keys, bindings, options ----------------------------------------------------------------------

def num(**kw):
    return {"type": "number", "x": 0, "y": 0, "w": 100, "h": 30, "bind": "system.cpu.load", **kw}


def test_unknown_widget_key():
    has(errs(doc_with(W(colour="red"))), "active.widgets[0].colour: unknown key")
    has(errs(doc_with(W(bind="system.cpu.load"))), "active.widgets[0].bind: unknown key")


def test_unknown_source_message_is_path_pointing():
    d = doc_with(W(), W(), W(), num(bind="cpu.tmp"))
    has(errs(d), "active.widgets[3].bind: unknown source 'cpu.tmp'")


def test_unknown_source_suggestion():
    has(errs(doc_with(num(bind="system.cpu.tmp"))), "(did you mean 'system.cpu.temp'?)")


def test_bind_required_and_type():
    w = num()
    del w["bind"]
    has(errs(doc_with(w)), "active.widgets[0].bind: required")
    has(errs(doc_with(num(bind=5))), "bind: must be string")


def test_bind_kind_mismatch():
    has(errs(doc_with(num(bind="system.cpu.load.history"))), "source 'system.cpu.load.history' is series; 'number' needs number")
    has(errs(doc_with({"type": "sparkline", "x": 0, "y": 0, "w": 50, "h": 20, "bind": "system.cpu.load"})), "needs series")


def test_settings_binding_kinds():
    d = doc_with(num(bind="settings.limit"), settings=[{"key": "limit", "type": "number", "default": 3}])
    assert errs(d) == []
    d = doc_with(num(bind="settings.name"), settings=[{"key": "name", "type": "string", "default": "x"}])
    has(errs(d), "source 'settings.name' is string")
    has(errs(doc_with(num(bind="settings.zzz"))), "unknown source 'settings.zzz'")


@pytest.mark.parametrize("kw,frag", [
    ({"invert": 1}, "invert: must be bool"),
    ({"border": 9}, "border: must be between 0 and 4"),
    ({"border": "1"}, "border: must be an integer"),
    ({"precision": 1.5}, "precision: must be an integer"),
    ({"precision": 99}, "precision: must be between 0 and 6"),
    ({"align": "middle"}, "align: must be one of"),
    ({"align": 5}, "align: must be one of"),
    ({"font": "comic-12"}, "font: font must look like"),
    ({"font": "bold-2"}, "font: font must look like"),
    ({"font": 5}, "font: font must look like"),
    ({"unit": 5}, "unit: must be string"),
    ({"unit": "x" * 501}, "unit: too long"),
    ({"unit": "{"}, "unit: bad template"),
    ({"unit": "{nope.thing}"}, "unit: unknown source 'nope.thing'"),
    ({"unit": "{system.cpu.load.history}"}, "is series; a template needs"),
    ({"unit": "{system.cpu.load:zzz}"}, "bad format spec ':zzz'"),
    ({"format": "{:.1f} {:.1f}"}, "format: must contain exactly one field"),
    ({"format": "{0.real}"}, "format: must contain exactly one field"),
    ({"format": "nofield"}, "format: must contain exactly one field"),
    ({"id": 5}, "id: must be string"),
])
def test_number_option_rules(kw, frag):
    has(errs(doc_with(num(**kw))), frag)


def test_num_option_type():
    g = {"type": "gauge", "x": 0, "y": 0, "w": 100, "h": 100, "bind": "system.cpu.load"}
    has(errs(doc_with({**g, "sweep": "x"})), "sweep: must be a number")
    has(errs(doc_with({**g, "sweep": 5})), "sweep: must be between 10 and 360")
    has(errs(doc_with({**g, "format": "bad"})), "format: must contain exactly one field")
    assert errs(doc_with({**g, "sweep": 180.5, "start_angle": -90})) == []


@pytest.mark.parametrize("rng", [[1], [1, 2, 3], "x", [1, "a"], [5, 5], [9, 1], [0, float("inf")]])
def test_bad_ranges(rng):
    bar = {"type": "bar", "x": 0, "y": 0, "w": 50, "h": 10, "bind": "system.cpu.load", "range": rng}
    has(errs(doc_with(bar)), "active.widgets[0].range")


def test_sparkline_label_format():
    sp = {"type": "sparkline", "x": 0, "y": 0, "w": 50, "h": 20, "bind": "system.cpu.load.history", "label_format": "x"}
    has(errs(doc_with(sp)), "label_format: must contain exactly one field")


def test_icon_needs_name_or_bind():
    ic = {"type": "icon", "x": 0, "y": 0, "w": 20, "h": 20}
    has(errs(doc_with(ic)), "active.widgets[0]: needs 'name' or 'bind'")
    assert errs(doc_with({**ic, "name": "cpu"})) == []
    assert errs(doc_with({**ic, "bind": "weather.icon"})) == []
    has(errs(doc_with({**ic, "name": "rocket"})), "name: must be one of")


def test_image_src_xor_bind():
    im = {"type": "image", "x": 0, "y": 0, "w": 20, "h": 20}
    has(errs(doc_with(im)), "needs exactly one of 'src' or 'bind'")
    has(errs(doc_with({**im, "src": "a.png", "bind": "steam.now_playing.box_art_path"})), "exactly one")
    assert errs(doc_with({**im, "bind": "steam.now_playing.box_art_path"})) == []
    has(errs(doc_with({**im, "bind": "steam.now_playing.name"})), "is string; 'image' needs path")


def test_clock_timezone():
    ck = {"type": "clock", "x": 0, "y": 0, "w": 100, "h": 30}
    has(errs(doc_with({**ck, "timezone": "Mars/Base"})), "timezone: unknown timezone 'Mars/Base'")
    assert errs(doc_with({**ck, "timezone": "Europe/Berlin"})) == []
    assert errs(doc_with({**ck, "timezone": "{settings.tz}"}, settings=[{"key": "tz", "type": "string", "default": ""}])) == []
    assert errs(doc_with({**ck, "bind": "weather.sunrise"})) == []


def test_now_playing_options():
    npw = {"type": "now-playing", "x": 0, "y": 0, "w": 300, "h": 300}
    assert errs(doc_with(npw)) == []
    has(errs(doc_with({**npw, "art_width": 700})), "art_width: must be between")
    has(errs(doc_with({**npw, "bind": "x"})), "bind: unknown key")


# ---- assets --------------------------------------------------------------------------------------------

def img(**kw):
    return {"type": "image", "x": 0, "y": 0, "w": 50, "h": 50, "src": "a.png", **kw}


def test_asset_ok(make_pkg):
    load_screen(make_pkg(doc_with(img()), assets={"a.png": png_bytes()}))
    nested = valid_doc(id="demo2", active={"widgets": [img(src="sub/b.png")]})
    load_screen(make_pkg(nested, assets={"sub/b.png": png_bytes()}))


def test_asset_missing(make_pkg):
    with pytest.raises(ScreenError, match=r"active.widgets\[0\].src: asset 'a.png' not found in assets/"):
        load_screen(make_pkg(doc_with(img())))


@pytest.mark.parametrize("bad", ["../screen.json", "a/../../x.png", "/etc/passwd", "..\\x.png", "a\\b.png", "a\x00.png", "..", ""])
def test_asset_path_traversal_rejected(bad):
    assert any("src" in e for e in errs(doc_with(img(src=bad)))), bad


def test_asset_path_syntax_checked_without_root():
    has(errs(doc_with(img(src="../x.png")), root=None), "must be relative to assets/")
    assert errs(doc_with(img(src="x.png")), root=None) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_asset_symlink_escape_rejected(make_pkg, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(png_bytes())
    root = make_pkg(doc_with(img()), assets={"keep.png": png_bytes()})
    os.symlink(secret, root / "assets" / "a.png")
    with pytest.raises(ScreenError, match="escapes the package"):
        load_screen(root)


def test_asset_oversized(make_pkg):
    with pytest.raises(ScreenError, match="too large"):
        load_screen(make_pkg(doc_with(img()), assets={"a.png": b"0" * (2 * 1024 * 1024 + 1)}))


def test_asset_not_an_image(make_pkg):
    with pytest.raises(ScreenError, match="not a readable image"):
        load_screen(make_pkg(doc_with(img()), assets={"a.png": b"just text"}))


def test_asset_too_many_pixels(make_pkg):
    big = png_bytes(size=(4100, 4100), mode="1", color=1)
    with pytest.raises(ScreenError, match="too many pixels"):
        load_screen(make_pkg(doc_with(img()), assets={"a.png": big}))


def test_asset_wrong_type():
    has(errs(doc_with(img(src=5))), "src: must be a non-empty string")


# ---- code ------------------------------------------------------------------------------------------------

def code_doc(code):
    d = valid_doc()
    d["active"]["code"] = code
    return d


def test_code_ok(make_pkg):
    s = load_screen(make_pkg(code_doc("screen.py"), files={"screen.py": "def render(canvas, data): pass\n"}))
    assert s.code_path.name == "screen.py" and s.code_path.is_file()


def test_code_missing(make_pkg):
    with pytest.raises(ScreenError, match="active.code: 'screen.py' not found"):
        load_screen(make_pkg(code_doc("screen.py")))


@pytest.mark.parametrize("bad", ["../x.py", "/abs.py", "x.txt", "a\\b.py", 5, ""])
def test_code_bad_paths(bad):
    has(errs(code_doc(bad)), "active.code")


def test_code_too_large(make_pkg):
    with pytest.raises(ScreenError, match="too large"):
        load_screen(make_pkg(code_doc("screen.py"), files={"screen.py": "#" * (256 * 1024 + 1)}))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_code_symlink_escape(make_pkg, tmp_path):
    outside = tmp_path / "evil.py"
    outside.write_text("x=1")
    root = make_pkg(code_doc("screen.py"))
    os.symlink(outside, root / "screen.py")
    with pytest.raises(ScreenError, match="escapes the package"):
        load_screen(root)


def test_code_checked_syntactically_without_root():
    assert errs(code_doc("screen.py"), root=None) == []


# ---- settings -----------------------------------------------------------------------------------------------

def S(**kw):
    return {"key": "k", "type": "number", "default": 5, **kw}


def test_settings_container_and_keys():
    has(errs(valid_doc(settings={})), "settings: must be an array")
    has(errs(valid_doc(settings=[5])), "settings[0]: must be an object")
    has(errs(valid_doc(settings=[{"key": "k"}])), "settings[0].type: required")
    has(errs(valid_doc(settings=[S(extra=1)])), "settings[0].extra: unknown key")


@pytest.mark.parametrize("key", ["K", "1a", "a-b", "", 5])
def test_setting_key_pattern(key):
    has(errs(valid_doc(settings=[S(key=key)])), "settings[0].key")


def test_setting_duplicate_key():
    has(errs(valid_doc(settings=[S(), S()])), "settings[1].key: duplicate setting 'k'")


def test_setting_type_must_be_known():
    has(errs(valid_doc(settings=[S(type="int")])), "settings[0].type: must be one of ['bool', 'enum', 'number', 'string']")


@pytest.mark.parametrize("setting,frag", [
    (S(default="x"), "default: must be a number"),
    (S(default=True), "default: must be a number"),
    (S(min=10), "default: must be >= 10"),
    (S(max=1), "default: must be <= 1"),
    (S(min="a"), "min: must be a number"),
    (S(max=[]), "max: must be a number"),
    (S(min=9, max=2), "min must not exceed max"),
    ({"key": "k", "type": "bool", "default": 1}, "default: must be bool"),
    ({"key": "k", "type": "string", "default": 1}, "default: must be string"),
    ({"key": "k", "type": "enum", "default": "a"}, "values: required for enum"),
    ({"key": "k", "type": "enum", "default": "a", "values": []}, "values: required for enum"),
    ({"key": "k", "type": "enum", "default": "a", "values": ["a", "a"]}, "values: required for enum"),
    ({"key": "k", "type": "enum", "default": "a", "values": [1]}, "values: required for enum"),
    ({"key": "k", "type": "enum", "default": "z", "values": ["a", "b"]}, "default: must be one of"),
    ({"key": "k", "type": "bool", "default": True, "min": 1}, "min: not allowed for type 'bool'"),
    ({"key": "k", "type": "string", "default": "", "values": ["a"]}, "values: not allowed"),
    ({"key": "k", "type": "bool", "default": True, "label": ""}, "label:"),
])
def test_setting_rules(setting, frag):
    has(errs(valid_doc(settings=[setting])), "settings[0]." + frag if not frag.startswith("min must") else frag)


def test_valid_settings_all_types():
    s = [S(min=0, max=10), {"key": "b", "type": "bool", "default": False},
         {"key": "s", "type": "string", "default": "x", "label": "S"},
         {"key": "e", "type": "enum", "default": "a", "values": ["a", "b"]}]
    assert errs(valid_doc(settings=s)) == []


def test_resolve_settings():
    schema = [S(min=0, max=10), {"key": "b", "type": "bool", "default": False},
              {"key": "e", "type": "enum", "default": "a", "values": ["a", "b"]},
              {"key": "s", "type": "string", "default": "x"}]
    assert resolve_settings(schema) == {"k": 5, "b": False, "e": "a", "s": "x"}
    assert resolve_settings(schema, {"k": 7.5, "b": True, "e": "b", "s": "y"}) == {"k": 7.5, "b": True, "e": "b", "s": "y"}
    for bad, frag in [({"nope": 1}, "settings.nope: unknown setting"), ({"k": 11}, "must be <= 10"), ({"k": -1}, "must be >= 0"),
                      ({"k": "3"}, "must be a number"), ({"k": True}, "must be a number"), ({"b": 1}, "must be bool"),
                      ({"e": "z"}, "must be one of"), ({"e": 1}, "must be one of"), ({"s": 3}, "must be string")]:
        with pytest.raises(ScreenError, match=frag):
            resolve_settings(schema, bad)


def test_resolve_settings_reports_all():
    with pytest.raises(ScreenError) as e:
        resolve_settings([S()], {"a": 1, "b": 2})
    assert len(e.value.errors) == 2


# ---- idle section ------------------------------------------------------------------------------------------------

def idle_doc(**idle):
    base = {"render_mode": "mono", "widgets": [{"type": "clock", "x": 0, "y": 0, "w": 100, "h": 40, "source": "time", "format": "%H:%M"}]}
    base.update(idle)
    d = valid_doc()
    del d["active"]
    d["idle"] = base
    return d


def test_idle_valid_and_untouched_by_engine(make_pkg):
    d = idle_doc(sources={"time": {"type": "clock"}}, refresh_interval_s=900, full_refresh_every=60)
    s = load_screen(make_pkg(d))
    assert s.active is None and s.idle["render_mode"] == "mono" and s.refresh_s == 1.0
    with pytest.raises(ScreenError, match="no 'active' section"):
        render(s)


def test_idle_rules():
    has(errs(idle_doc(render_mode="rgb")), "idle.render_mode: must be 'mono' or 'gray4'")
    has(errs(idle_doc(refresh_interval_s=0)), "idle.refresh_interval_s: must be an integer >= 1")
    has(errs(idle_doc(full_refresh_every="a")), "idle.full_refresh_every")
    has(errs(idle_doc(sources=[])), "idle.sources: must be an object")
    has(errs(idle_doc(sources={"x": {"type": "ftp"}})), "idle.sources.x")
    has(errs(idle_doc(sources={"x": 5})), "idle.sources.x")
    has(errs(idle_doc(widgets={})), "idle.widgets: must be an array")
    has(errs(idle_doc(widgets=[5])), "idle.widgets[0]: must be an object")
    has(errs(idle_doc(widgets=[{"type": "gauge", "x": 0, "y": 0, "w": 5, "h": 5}])), "idle.widgets[0].type: must be one of")
    has(errs(idle_doc(widgets=[{"type": "text", "x": 0, "y": 0, "w": 900, "h": 5}])), "idle.widgets[0].x: widget extends")
    has(errs(idle_doc(widgets=[{"type": "text", "x": 0, "y": 0, "w": 9}])), "idle.widgets[0].h: required")
    has(errs(idle_doc(bogus=1)), "idle.bogus: unknown key")
    has(errs(idle_doc(extra=1) | {"idle": []}), "idle: must be an object")
    d = idle_doc()
    del d["idle"]["render_mode"]
    has(errs(d), "idle.render_mode: required")


def test_validate_does_not_mutate():
    d = doc_with(num())
    before = copy.deepcopy(d)
    errs(d)
    assert d == before and json.dumps(d)


# ---- folder_name (the package store keeps versions in <id>/<version>/) ------------------------------------
def test_load_screen_folder_name_override(make_pkg, tmp_path):
    p = make_pkg(valid_doc(id="demo"), name="1.0.0")
    with pytest.raises(ScreenError, match="must match the folder name '1.0.0'"):
        load_screen(p)
    assert load_screen(p, folder_name="demo").id == "demo"
    with pytest.raises(ScreenError, match="must match the folder name 'other'"):
        load_screen(p, folder_name="other")


# ---------------------------------------------------------------- bound_paths (what the engine watches)

def _screen(widgets, **active):
    from pathlib import Path

    from inkterface_core.screens.schema import Screen
    return Screen(root=Path("."), id="t", name="t", version="1.0.0", author="t", active={"widgets": widgets, **active})


def test_bound_paths_collects_bind_templates_and_clock_defaults():
    s = _screen([
        {"type": "number", "bind": "system.cpu.load"},
        {"type": "sparkline", "bind": "system.cpu.load.history"},
        {"type": "text", "text": "{system.gpu.temp:.0f} C and {{literal}} {settings.unit}"},
        {"type": "number", "bind": "system.fan.rpm", "unit": "{steam.recent[0].name}"},
        {"type": "clock"},
        {"type": "now-playing"},
        {"type": "text", "text": "static"},
    ])
    assert s.bound_paths() == {"steam.now_playing", "system.cpu.load", "system.cpu.load.history", "system.gpu.temp", "system.fan.rpm",
                               "steam.recent[0].name", "time.now", "time.tz"}


def test_bound_paths_clock_with_its_own_bind_does_not_add_the_default():
    s = _screen([{"type": "clock", "bind": "weather.sunrise"}])
    assert s.bound_paths() == {"weather.sunrise"}


def test_bound_paths_empty_and_unparseable_templates():
    assert _screen([]).bound_paths() == frozenset()
    assert _screen([{"type": "text", "text": "oops { unbalanced"}]).bound_paths() == frozenset()
    from pathlib import Path

    from inkterface_core.screens.schema import Screen
    assert Screen(root=Path("."), id="t", name="t", version="1", author="t", active=None).bound_paths() == frozenset()


def test_bound_paths_of_a_code_screen_is_none_meaning_everything():
    assert _screen([{"type": "number", "bind": "system.cpu.load"}], code="screen.py").bound_paths() is None


def test_bound_paths_of_the_builtin_screens_are_real_snapshot_keys():
    from conftest import BUILTIN_DIR

    from inkterface_core.engine import load_screens
    from inkterface_core.screens import data as D
    screens, errs = load_screens(BUILTIN_DIR)
    assert not errs and {"telemetry", "thermals-power", "big-clock", "now-playing"} <= set(screens)
    for sid, scr in screens.items():
        b = scr.bound_paths()
        assert b is not None
        for p in b:
            assert p == "steam.now_playing" or D.source_kind(p, {k["key"]: "string" for k in scr.settings if "key" in k}) is not None, (sid, p)
    assert "time.now" in screens["big-clock"].bound_paths()
    assert "steam.now_playing" in screens["now-playing"].bound_paths()

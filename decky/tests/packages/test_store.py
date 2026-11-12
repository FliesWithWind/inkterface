"""PackageStore: install / upgrade / rollback / uninstall / built-ins / approval lifecycle / rendering."""
import hashlib
import json
import logging
import os

import pytest
from packages_testlib import BUILTIN_DIR, GOOD_CODE, package_zip, screen_json
from PIL import Image

from inkterface_core.packages import PackageError, PackageStore
from inkterface_core.packages import store as ST
from inkterface_core.screens import ScreenError

sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def put(write_zip, name="p.zip", **kw):
    return write_zip(package_zip(**kw), name)


@pytest.fixture
def builtin_ids():
    return sorted(p.name for p in BUILTIN_DIR.iterdir() if (p / "screen.json").is_file())


# ---- built-ins -----------------------------------------------------------------------------------------------
def test_builtins_listed_enabled_and_readonly(store, builtin_ids):
    infos = store.list()
    assert [i.id for i in infos] == builtin_ids
    assert all(i.builtin and i.enabled and not i.needs_approval for i in infos)
    assert store.get("telemetry").path == BUILTIN_DIR / "telemetry"


def test_builtin_cannot_be_uninstalled(store):
    with pytest.raises(PackageError, match="built-in screens cannot be uninstalled"):
        store.uninstall("telemetry")
    assert (BUILTIN_DIR / "telemetry" / "screen.json").is_file()


def test_builtin_id_collision_refused(store, write_zip):
    with pytest.raises(PackageError, match="id: 'telemetry' collides with a built-in"):
        store.install(put(write_zip, pid="telemetry"))
    assert not (store.root / "telemetry").exists()


def test_builtin_disable_enable_persists(tmp_path):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    assert s.disable("telemetry").enabled is False
    assert PackageStore(tmp_path / "s", BUILTIN_DIR).get("telemetry").enabled is False
    with pytest.raises(PackageError, match="disabled"):
        s.render("telemetry")
    assert s.enable("telemetry").enabled is True
    assert s.render("telemetry").size == (648, 480)


def test_no_builtin_dir(tmp_path):
    assert PackageStore(tmp_path / "s").list() == []
    assert PackageStore(tmp_path / "s", tmp_path / "missing").list() == []


# ---- install -------------------------------------------------------------------------------------------------
def test_install_from_path(store, write_zip):
    info = store.install(put(write_zip))
    assert (info.id, info.version, info.author, info.builtin, info.enabled) == ("demo-pkg", "1.0.0", "Tester", False, True)
    assert not info.has_code and not info.needs_approval
    assert (store.root / "demo-pkg" / "1.0.0" / "screen.json").is_file()
    assert "demo-pkg" in [i.id for i in store.list()]


def test_install_accepts_str_path(store, write_zip):
    assert store.install(str(put(write_zip))).id == "demo-pkg"


def test_install_from_https_url_uses_injected_fetch(tmp_path):
    calls = []

    def fetch(url, max_bytes, timeout):
        calls.append((url, max_bytes, timeout))
        return package_zip()

    s = PackageStore(tmp_path / "s", BUILTIN_DIR, fetch=fetch, fetch_timeout=7)
    assert s.install("https://example.com/x.zip").id == "demo-pkg"
    assert calls == [("https://example.com/x.zip", 5 * 1024 * 1024, 7)]


@pytest.mark.parametrize("url", ["http://example.com/x.zip", "file:///tmp/x.zip"])
def test_non_https_urls_refused(tmp_path, url):
    s = PackageStore(tmp_path / "s", fetch=lambda *a: pytest.fail("must not fetch"))
    with pytest.raises(PackageError, match="only https:// URLs"):
        s.install(url)


def test_oversized_download_refused(tmp_path):
    s = PackageStore(tmp_path / "s", fetch=lambda u, m, t: b"x" * (m + 1))
    with pytest.raises(PackageError, match="zip is .* bytes"):
        s.install("https://example.com/big.zip")
    assert s.list() == []


def test_fetch_failure_propagates(tmp_path):
    def fetch(u, m, t):
        raise PackageError(f"{u}: download failed: boom")

    with pytest.raises(PackageError, match="download failed: boom"):
        PackageStore(tmp_path / "s", fetch=fetch).install("https://example.com/x.zip")


def test_invalid_package_installs_nothing(store, write_zip):
    doc = screen_json()
    doc["active"]["widgets"][0]["w"] = 9999
    with pytest.raises(PackageError, match=r"screen\.json: active\.widgets\[0\]"):
        store.install(put(write_zip, doc=doc))
    assert not (store.root / "demo-pkg").exists() and not [i for i in store.list() if not i.builtin]
    assert list((store.root / ".tmp").iterdir()) == []


def test_state_survives_restart(tmp_path, write_zip):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    s.install(put(write_zip))
    s.disable("demo-pkg")
    s2 = PackageStore(tmp_path / "s", BUILTIN_DIR)
    assert s2.get("demo-pkg").enabled is False
    assert json.loads((tmp_path / "s" / "state.json").read_text())["packages"]["demo-pkg"]["version"] == "1.0.0"


def test_stale_tmp_cleaned_at_start(tmp_path):
    (tmp_path / "s" / ".tmp" / "junk").mkdir(parents=True)
    PackageStore(tmp_path / "s")
    assert list((tmp_path / "s" / ".tmp").iterdir()) == []


def test_orphan_dir_is_replaced(store, write_zip):
    orphan = store.root / "demo-pkg" / "1.0.0"
    orphan.mkdir(parents=True)
    (orphan / "stale.txt").write_text("old")
    store.install(put(write_zip))
    assert not (orphan / "stale.txt").exists() and (orphan / "screen.json").is_file()


# ---- enable / disable ----------------------------------------------------------------------------------------
def test_enable_disable_and_render_gate(store, write_zip):
    store.install(put(write_zip))
    assert store.disable("demo-pkg").enabled is False
    with pytest.raises(PackageError, match="demo-pkg: disabled"):
        store.render("demo-pkg")
    assert store.enable("demo-pkg").enabled is True
    assert store.render("demo-pkg").size == (648, 480)


def test_unknown_package(store):
    for call in (store.get, store.enable, store.disable, store.uninstall, store.render):
        with pytest.raises(PackageError, match="nope: not installed"):
            call("nope")


# ---- uninstall -----------------------------------------------------------------------------------------------
def test_uninstall(tmp_path, write_zip):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    s.install(put(write_zip))
    s.uninstall("demo-pkg")
    assert not (tmp_path / "s" / "demo-pkg").exists()
    with pytest.raises(PackageError, match="not installed"):
        s.get("demo-pkg")
    assert "demo-pkg" not in PackageStore(tmp_path / "s", BUILTIN_DIR)._entries


def test_reinstall_after_uninstall(store, write_zip):
    store.install(put(write_zip))
    store.uninstall("demo-pkg")
    assert store.install(put(write_zip)).version == "1.0.0"


# ---- upgrade / rollback --------------------------------------------------------------------------------------
def test_upgrade_replaces_old_version(store, write_zip):
    store.install(put(write_zip, version="1.0.0"))
    info = store.install(put(write_zip, "v2.zip", version="1.1.0"))
    assert info.version == "1.1.0"
    assert not (store.root / "demo-pkg" / "1.0.0").exists() and (store.root / "demo-pkg" / "1.1.0").is_dir()


def test_upgrade_keeps_disabled_state(store, write_zip):
    store.install(put(write_zip, version="1.0.0"))
    store.disable("demo-pkg")
    assert store.install(put(write_zip, "v2.zip", version="1.0.1")).enabled is False


def test_invalid_upgrade_leaves_old_version_intact(store, write_zip):
    store.install(put(write_zip, version="1.0.0"))
    doc = screen_json(version="2.0.0")
    doc["active"]["widgets"][0]["h"] = 9999
    with pytest.raises(PackageError, match="screen.json"):
        store.install(put(write_zip, "bad.zip", version="2.0.0", doc=doc))
    info = store.get("demo-pkg")
    assert info.version == "1.0.0" and info.enabled
    assert store.render("demo-pkg").size == (648, 480)
    assert [p.name for p in (store.root / "demo-pkg").iterdir()] == ["1.0.0"]


def test_upgrade_rolled_back_when_state_cannot_be_saved(tmp_path, write_zip, monkeypatch):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    s.install(put(write_zip, version="1.0.0"))

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(s, "_save_state", boom)
    with pytest.raises(OSError, match="disk full"):
        s.install(put(write_zip, "v2.zip", version="2.0.0"))
    monkeypatch.undo()
    assert s.get("demo-pkg").version == "1.0.0"
    assert [p.name for p in (tmp_path / "s" / "demo-pkg").iterdir()] == ["1.0.0"]
    assert PackageStore(tmp_path / "s", BUILTIN_DIR).get("demo-pkg").version == "1.0.0"


@pytest.mark.parametrize("old,new", [("1.0.0", "1.0.0"), ("1.1.0", "1.0.9"), ("1.0.0", "1.0.0-rc.1")])
def test_not_newer_refused(store, write_zip, old, new):
    store.install(put(write_zip, version=old))
    with pytest.raises(PackageError, match=f"version: {new} is not newer than the installed {old}"):
        store.install(put(write_zip, "v2.zip", version=new))
    assert store.get("demo-pkg").version == old


@pytest.mark.parametrize("lo,hi", [("1.9.0", "1.10.0"), ("1.0.0-rc.1", "1.0.0"), ("1.0.0-alpha", "1.0.0-alpha.1"),
                                   ("1.0.0-alpha.2", "1.0.0-alpha.10"), ("1.0.0-9", "1.0.0-alpha"),
                                   ("1.0.0+a", "1.0.1")])
def test_version_ordering(lo, hi):
    assert ST._version_key(lo) < ST._version_key(hi)


# ---- state file robustness -----------------------------------------------------------------------------------
def test_corrupt_state_fails_closed(tmp_path, write_zip, caplog):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    s.install(put(write_zip))
    (tmp_path / "s" / "state.json").write_text("{not json")
    with caplog.at_level(logging.ERROR):
        s2 = PackageStore(tmp_path / "s", BUILTIN_DIR)
    assert [i.id for i in s2.list() if not i.builtin] == [] and (tmp_path / "s" / "state.json.bad").is_file()
    assert s2.install(put(write_zip)).id == "demo-pkg"   # reinstall works, old dir replaced


@pytest.mark.parametrize("raw", ["[]", '{"packages": []}', '{"packages": 3}', "\xff"])
def test_state_wrong_shape(tmp_path, raw):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "state.json").write_bytes(raw.encode("latin-1"))
    assert PackageStore(tmp_path / "s")._entries == {}


def test_malformed_entries_dropped(tmp_path):
    (tmp_path / "s").mkdir()
    good = {"version": "1.0.0", "enabled": True, "approval": None}
    bad = {"../evil": good, "ok-id": {**good, "version": "../../x"}, "no-flag": {"version": "1.0.0"},
           "bad-appr": {**good, "approval": {"version": 1}}, "not-dict": 3, "good": good}
    (tmp_path / "s" / "state.json").write_text(json.dumps({"schema": 1, "packages": bad}))
    assert list(PackageStore(tmp_path / "s")._entries) == ["good"]


def test_broken_installed_package_is_skipped_in_list(store, write_zip, caplog):
    store.install(put(write_zip))
    (store.root / "demo-pkg" / "1.0.0" / "screen.json").write_text("{")
    with caplog.at_level(logging.WARNING):
        assert [i.id for i in store.list() if not i.builtin] == []
    with pytest.raises(PackageError, match="invalid JSON"):
        store.get("demo-pkg")
    assert "skipping demo-pkg" in caplog.text


# ---- code tier: approval lifecycle ---------------------------------------------------------------------------
def install_code(store, write_zip, code=GOOD_CODE, **kw):
    return store.install(put(write_zip, code=code, **kw))


def test_code_package_installs_disabled_needing_approval(store, write_zip):
    info = install_code(store, write_zip)
    assert info.has_code and info.needs_approval and not info.approved and not info.enabled
    assert (info.id, info.version, info.author, info.code_sha256) == ("demo-pkg", "1.0.0", "Tester", sha(GOOD_CODE))


def test_unapproved_code_cannot_be_enabled_or_run(store, write_zip):
    install_code(store, write_zip)
    with pytest.raises(PackageError, match="needs approval first.*approve 1.0.0 sha256 " + sha(GOOD_CODE)):
        store.enable("demo-pkg")
    with pytest.raises(PackageError, match="needs approval"):
        store.render("demo-pkg")


def test_approve_enables_and_runs(store, write_zip):
    install_code(store, write_zip)
    info = store.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    assert info.approved and info.enabled and not info.needs_approval
    im = store.render("demo-pkg")
    assert im.size == (648, 480) and im.getpixel((0, 5)) == 0


def test_approve_accepts_uppercase_hash(store, write_zip):
    install_code(store, write_zip)
    assert store.approve("demo-pkg", "1.0.0", " " + sha(GOOD_CODE).upper() + " ").approved


def test_approve_rejects_wrong_hash_and_version(store, write_zip):
    install_code(store, write_zip)
    with pytest.raises(PackageError, match="sha256 mismatch"):
        store.approve("demo-pkg", "1.0.0", "0" * 64)
    with pytest.raises(PackageError, match="installed version is 1.0.0, not 9.9.9"):
        store.approve("demo-pkg", "9.9.9", sha(GOOD_CODE))
    assert store.get("demo-pkg").needs_approval


def test_approve_declarative_or_builtin_refused(store, write_zip):
    store.install(put(write_zip))
    for pid in ("demo-pkg", "telemetry"):
        with pytest.raises(PackageError, match="no installed screen.py to approve"):
            store.approve(pid, "1.0.0", "0" * 64)


def test_editing_screen_py_revokes_approval(tmp_path, write_zip):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    install_code(s, write_zip)
    s.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    marker = tmp_path / "ran"
    evil = GOOD_CODE + f"\nopen({str(marker)!r}, 'w').write('x')\n"
    (tmp_path / "s" / "demo-pkg" / "1.0.0" / "screen.py").write_text(evil)
    with pytest.raises(PackageError, match="needs approval"):
        s.render("demo-pkg")
    assert not marker.exists(), "modified code must never run"
    info = s.get("demo-pkg")
    assert info.needs_approval and not info.enabled and info.code_sha256 == sha(evil)
    assert PackageStore(tmp_path / "s", BUILTIN_DIR).get("demo-pkg").needs_approval   # persisted
    with pytest.raises(PackageError, match="needs approval first"):
        s.enable("demo-pkg")
    with pytest.raises(PackageError, match="sha256 mismatch"):
        s.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))   # approving what you saw before the edit fails
    assert s.approve("demo-pkg", "1.0.0", sha(evil)).enabled   # a fresh, informed approval works
    assert s.render("demo-pkg") is not None and marker.exists()


def test_revocation_when_listing(store, write_zip):
    install_code(store, write_zip)
    store.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    (store.root / "demo-pkg" / "1.0.0" / "screen.py").write_text("# changed\n" + GOOD_CODE)
    assert [i.needs_approval for i in store.list() if i.id == "demo-pkg"] == [True]


def test_hand_edited_state_cannot_fake_approval(tmp_path, write_zip):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    install_code(s, write_zip)
    st = json.loads((tmp_path / "s" / "state.json").read_text())
    st["packages"]["demo-pkg"]["enabled"] = True
    (tmp_path / "s" / "state.json").write_text(json.dumps(st))
    s2 = PackageStore(tmp_path / "s", BUILTIN_DIR)
    assert s2.get("demo-pkg").enabled is False
    with pytest.raises(PackageError, match="needs approval"):
        s2.render("demo-pkg")


def test_upgrading_code_package_requires_new_approval(store, write_zip):
    install_code(store, write_zip)
    store.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    info = install_code(store, write_zip, name="v2.zip", version="1.1.0")
    assert info.needs_approval and not info.enabled
    with pytest.raises(PackageError, match="installed version is 1.1.0, not 1.0.0"):
        store.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    assert store.approve("demo-pkg", "1.1.0", sha(GOOD_CODE)).enabled


def test_upgrade_from_code_to_declarative(store, write_zip):
    install_code(store, write_zip)
    store.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    info = store.install(put(write_zip, "v2.zip", version="2.0.0"))
    assert not info.has_code and info.enabled


def test_uninstall_code_package(store, write_zip):
    install_code(store, write_zip)
    store.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    store.uninstall("demo-pkg")
    assert not (store.root / "demo-pkg").exists()
    assert install_code(store, write_zip).needs_approval   # a reinstall starts unapproved again


# ---- rendering code screens: failures become frames ----------------------------------------------------------
BAD_SCREENS = {
    "raises": ("def render(c, d, s):\n    raise ValueError('kaboom')\n", "kaboom"),
    "hangs": ("import time\ndef render(c, d, s):\n    time.sleep(30)\n", "timed out"),
    "crashes": ("import os\nos._exit(9)\n", "exit code 9"),
    "garbage": ("def render(c, d, s):\n    return b'junk'\n", "not a PNG"),
}


@pytest.mark.parametrize("name", BAD_SCREENS)
def test_failing_code_screen_renders_error_frame(tmp_path, write_zip, name, caplog):
    code, text = BAD_SCREENS[name]
    s = PackageStore(tmp_path / "s", BUILTIN_DIR, code_timeout=0.7)
    install_code(s, write_zip, code)
    s.approve("demo-pkg", "1.0.0", sha(code))
    with caplog.at_level(logging.WARNING):
        im = s.render("demo-pkg")   # a frame, not an exception
    assert isinstance(im, Image.Image) and im.size == (648, 480) and im.mode == "1"
    assert text in caplog.text
    assert im.tobytes() != Image.new("1", (648, 480), 255).tobytes()
    assert s.get("demo-pkg").enabled   # a crashing screen stays approved; the host keeps going


def test_code_screen_gets_defaults_and_overrides(tmp_path, write_zip):
    code = ("import io\nfrom PIL import Image, ImageDraw\n"
            "def render(c, d, s):\n"
            "    im = Image.new('1', c, 1)\n"
            "    ImageDraw.Draw(im).rectangle((0, 0, 9, 9), fill=0 if s['dark'] else 1)\n"
            "    b = io.BytesIO(); im.save(b, 'PNG'); return b.getvalue()\n")
    doc = screen_json(code=True, settings=[{"key": "dark", "type": "bool", "label": "Dark", "default": True}])
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    s.install(write_zip(package_zip(code=code, doc=doc)))
    s.approve("demo-pkg", "1.0.0", sha(code))
    assert s.render("demo-pkg").getpixel((5, 5)) == 0
    assert s.render("demo-pkg", settings={"dark": False}).getpixel((5, 5)) == 255
    with pytest.raises(ScreenError, match="settings.nope: unknown setting"):
        s.render("demo-pkg", settings={"nope": 1})


def test_code_screen_receives_data_snapshot_raw(tmp_path, write_zip):
    from inkterface_core.screens import DataSnapshot
    code = ("import io\nfrom PIL import Image\n"
            "def render(c, d, s):\n"
            "    assert d == {'a': 1}, d\n"
            "    im = Image.new('1', c, 1); im.putpixel((0, 0), 0)\n"
            "    b = io.BytesIO(); im.save(b, 'PNG'); return b.getvalue()\n")
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    install_code(s, write_zip, code)
    s.approve("demo-pkg", "1.0.0", sha(code))
    assert s.render("demo-pkg", DataSnapshot({"a": 1})).getpixel((0, 0)) == 0
    assert s.render("demo-pkg", {"a": 1}).getpixel((0, 0)) == 0


def test_unserialisable_data_gives_error_frame(tmp_path, write_zip):
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    install_code(s, write_zip)
    s.approve("demo-pkg", "1.0.0", sha(GOOD_CODE))
    assert s.render("demo-pkg", {"x": object()}).convert("L").tobytes().count(0) > 1000


def test_declarative_render_uses_data(store, write_zip):
    store.install(put(write_zip))
    assert store.render("demo-pkg", {}).tobytes() != Image.new("1", (648, 480), 255).tobytes()


def test_concurrent_installs_are_serialised(tmp_path, write_zip):
    import threading
    s = PackageStore(tmp_path / "s", BUILTIN_DIR)
    zips = [put(write_zip, f"z{i}.zip", pid=f"pkg-{i}") for i in range(6)]
    errs = []

    def go(z):
        try:
            s.install(z)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go, args=(z,)) for z in zips]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len([i for i in s.list() if not i.builtin]) == 6
    assert len(json.loads((tmp_path / "s" / "state.json").read_text())["packages"]) == 6
    assert os.listdir(tmp_path / "s" / ".tmp") == []

"""Catalog callables end to end: refresh/list/updates/install/preview, the developer-mode gate and
catalog_url validation, driven through main.py (the Plugin wrappers) against an injected catalog fetch."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalog"))
from catalog_testlib import PUB, full_fetch, make_screen, preview_png, sha  # noqa: E402
from packages_testlib import raw_zip, with_json, zinfo  # noqa: E402

BASE = "https://flieswithwind.github.io/inkterface-screens/"


def arm(h, screens, *, key=PUB, serial=1000):
    """Build a fetch serving `screens` and wire it into the harness. Returns (fetch, doc)."""
    f, raw, sig, doc = full_fetch(screens=screens, serial=serial)
    h.use_catalog(f, key)
    return f, doc


async def view(p, sid):
    return next((e for e in (await p.catalog_list())["entries"] if e["id"] == sid), None)


# --- refresh / list -------------------------------------------------------------
def test_list_before_refresh_is_empty(h):
    arm(h, [make_screen("demo")])

    async def body(p):
        r = await p.catalog_list()
        assert r["ok"] and r["entries"] == [] and r["fetched_at"] is None and r["url"] == BASE
    h.run(body)


def test_refresh_then_list(h):
    arm(h, [make_screen("alpha"), make_screen("beta", "2.0.0")])

    async def body(p):
        r = await p.catalog_refresh()
        assert r["ok"] and r["count"] == 2 and r["fetched_at"] > 0
        lst = await p.catalog_list()
        assert [e["id"] for e in lst["entries"]] == ["alpha", "beta"] and lst["fetched_at"] > 0
        a = await view(p, "alpha")
        assert a["installed"] is False and a["update_available"] is False and a["compatible"] is True
        assert a["needs_approval"] is False and a["incompatible_reason"] is None
    h.run(body)


def test_refresh_offline_leaves_cache_and_lists_it(h):
    f, _ = arm(h, [make_screen("demo")])

    async def body(p):
        assert (await p.catalog_refresh())["ok"]
        f.offline = True
        r = await p.catalog_refresh()
        assert not r["ok"] and r["error"]
        assert (await p.catalog_list())["ok"] and len((await p.catalog_list())["entries"]) == 1
    h.run(body)


def test_empty_key_fails_closed(h):
    arm(h, [make_screen("demo")], key=None)

    async def body(p):
        assert not (await p.catalog_refresh())["ok"]
        assert not (await p.catalog_list())["ok"]
        r = await p.catalog_install("demo", "1.0.0")
        assert not r["ok"] and "catalog signing key" in r["error"]
    h.run(body)


# --- install --------------------------------------------------------------------
def test_install_declarative_happy_path(h):
    arm(h, [make_screen("deco", "1.0.0")])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("deco", "1.0.0")
        assert r["ok"] and not r["needs_approval"] and r["package"]["id"] == "deco"
        assert "deco" in p.core.engine.screens
        v = await view(p, "deco")
        assert v["installed"] and v["installed_version"] == "1.0.0" and not v["update_available"]
        assert "screens_changed" in h.names()
    h.run(body)


def test_install_code_screen_needs_approval_then_approve_works(h):
    screen = make_screen("codey", "1.0.0", has_code=True)
    arm(h, [screen])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("codey", "1.0.0")
        assert r["ok"] and r["needs_approval"] and r["package"]["has_code"]
        assert not r["package"]["enabled"] and "codey" not in p.core.engine.screens
        v = await view(p, "codey")
        assert v["installed"] and v["needs_approval"]
        # the existing approval callable enables it
        pkg = r["package"]
        ar = await p.approve_package("codey", pkg["version"], pkg["code_sha256"])
        assert ar["ok"] and ar["package"]["enabled"] and "codey" in p.core.engine.screens
    h.run(body)


def test_install_version_must_match_catalog(h):
    arm(h, [make_screen("demo", "1.0.0")])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("demo", "9.9.9")
        assert not r["ok"] and "1.0.0" in r["error"]
    h.run(body)


def test_install_unknown_screen(h):
    arm(h, [make_screen("demo")])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("nope", "1.0.0")
        assert not r["ok"] and "not in the catalog" in r["error"]
    h.run(body)


def test_install_before_refresh_is_an_error(h):
    arm(h, [make_screen("demo")])

    async def body(p):
        r = await p.catalog_install("demo", "1.0.0")
        assert not r["ok"] and "refresh" in r["error"]
    h.run(body)


def test_install_reuses_store_checks_path_traversal(h):
    """A signed index pointing at a hostile zip is refused by load_zip (same path as import_package)."""
    entry, _good_zip, prev = make_screen("demo", "1.0.0")
    evil = with_json([(zinfo("demo/../../evil.txt"), b"x")], pid="demo")
    entry["sha256"], entry["size"] = sha(evil), len(evil)
    arm(h, [(entry, evil, prev)])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("demo", "1.0.0")
        assert not r["ok"] and r["error"]
        assert "demo" not in p.core.engine.screens
    h.run(body)


def test_install_expect_mismatch_refused(h):
    """The index claims declarative but the zip actually carries code -> expect (has_code) mismatch refused."""
    entry, _declared_zip, prev = make_screen("demo", "1.0.0", has_code=False)
    code_entry, code_zip, _ = make_screen("demo", "1.0.0", has_code=True)
    # keep the declarative entry metadata (has_code False) but point sha/size at the CODE zip
    entry["sha256"], entry["size"] = sha(code_zip), len(code_zip)
    arm(h, [(entry, code_zip, prev)])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("demo", "1.0.0")
        assert not r["ok"] and "does not match the catalog" in r["error"]
        assert "demo" not in p.core.engine.screens
    h.run(body)


def test_install_min_plugin_version_incompatible_refused(h):
    arm(h, [make_screen("demo", "1.0.0", min_plugin_version="9.0.0")])

    async def body(p):
        await p.catalog_refresh()
        v = await view(p, "demo")
        assert v["compatible"] is False and "9.0.0" in v["incompatible_reason"]
        r = await p.catalog_install("demo", "1.0.0")
        assert not r["ok"] and "9.0.0" in r["error"]
    h.run(body)


def test_install_builtin_id_collision_refused(h):
    # 'telemetry' is a built-in screen id
    entry, zbytes, prev = make_screen("telemetry", "9.9.9")
    arm(h, [(entry, zbytes, prev)])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_install("telemetry", "9.9.9")
        assert not r["ok"] and "built-in" in r["error"]
    h.run(body)


# --- updates --------------------------------------------------------------------
def test_updates_and_update_available(h):
    arm(h, [make_screen("deco", "1.0.0")])

    async def body(p):
        await p.catalog_refresh()
        await p.catalog_install("deco", "1.0.0")
        assert (await p.catalog_updates())["updates"] == []
        # publish a newer version
        f2, _ = arm(h, [make_screen("deco", "1.1.0")])
    h.run(body)

    # a second run with the newer catalog, the package still installed at 1.0.0
    arm(h, [make_screen("deco", "1.1.0")])

    async def body2(p):
        await p.catalog_refresh()
        v = await view(p, "deco")
        assert v["installed_version"] == "1.0.0" and v["update_available"] is True
        ups = (await p.catalog_updates())["updates"]
        assert ups == [{"id": "deco", "name": "Demo deco", "installed_version": "1.0.0", "version": "1.1.0"}]
    h.run(body2)


def test_downgrade_refused_by_store_strictly_newer(h):
    # install 2.0.0, then a catalog that only offers 1.0.0: installing it is refused by the store's newer rule
    arm(h, [make_screen("deco", "2.0.0")])

    async def body(p):
        await p.catalog_refresh()
        assert (await p.catalog_install("deco", "2.0.0"))["ok"]
    h.run(body)

    arm(h, [make_screen("deco", "1.0.0")])

    async def body2(p):
        await p.catalog_refresh()
        r = await p.catalog_install("deco", "1.0.0")
        assert not r["ok"] and "not newer" in r["error"]
    h.run(body2)


# --- preview --------------------------------------------------------------------
def test_preview_returns_a_data_url(h):
    arm(h, [make_screen("demo", "1.0.0")])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_preview("demo")
        assert r["ok"] and r["data_url"].startswith("data:image/png;base64,")
    h.run(body)


def test_preview_unknown_screen(h):
    arm(h, [make_screen("demo")])

    async def body(p):
        await p.catalog_refresh()
        r = await p.catalog_preview("nope")
        assert not r["ok"] and "not in the catalog" in r["error"]
    h.run(body)


# --- developer-mode gate --------------------------------------------------------
def test_import_package_refused_when_developer_mode_off(h):
    h.dev_mode = False   # default production state

    async def body(p):
        s = await p.get_settings()
        assert s["developer_mode"] is False   # default off
        r = await p.import_package(h.package("d.zip", make_screen("deco")[1]))
        assert not r["ok"]
        assert r["error"] == "Importing a zip or link is a developer feature. Turn on Developer mode in Settings first."
    h.run(body)


def test_import_package_works_when_developer_mode_on(h):
    h.dev_mode = False

    async def body(p):
        assert (await p.set_settings({"developer_mode": True}))["ok"]
        assert (await p.get_settings())["developer_mode"] is True
        r = await p.import_package(h.package("d.zip", make_screen("deco")[1]))
        assert r["ok"] and "deco" in p.core.engine.screens
        # persisted
        assert h.host_file()["developer_mode"] is True
    h.run(body)


def test_developer_mode_persists_across_restart(h):
    h.dev_mode = False

    async def first(p):
        assert (await p.set_settings({"developer_mode": True}))["ok"]
    h.run(first)

    async def second(p):
        assert (await p.get_settings())["developer_mode"] is True
    h.run(second)


# --- catalog_url validation -----------------------------------------------------
def test_catalog_url_default_exposed(h):
    async def body(p):
        s = await p.get_settings()
        assert s["catalog_url"] == "" and s["catalog_url_default"] == BASE
    h.run(body)


@pytest.mark.parametrize("url,msg", [
    ("http://x.example/", "https"),
    ("https://user:pass@x.example/", "userinfo"),
    ("https://x.example/\tpath", None),
    ("https://" + "x" * 300, None),
    ("ftp://x.example/", "https"),
])
def test_catalog_url_rejected(h, url, msg):
    async def body(p):
        r = await p.set_settings({"catalog_url": url})
        assert not r["ok"] and "catalog_url" in r["error"]
        assert (await p.get_settings())["catalog_url"] == ""
    h.run(body)


def test_catalog_url_trailing_slash_added_and_applied(h):
    arm(h, [make_screen("demo")])   # a bundled key, so catalog_list reaches its ok path

    async def body(p):
        r = await p.set_settings({"catalog_url": "https://my.example/screens"})
        assert r["ok"]
        s = await p.get_settings()
        assert s["catalog_url"] == "https://my.example/screens/"
        assert p.core.catalog.url == "https://my.example/screens/"
        assert (await p.catalog_list())["url"] == "https://my.example/screens/"
    h.run(body)


def test_catalog_url_empty_resets_to_default(h):
    async def body(p):
        await p.set_settings({"catalog_url": "https://my.example/screens/"})
        assert (await p.set_settings({"catalog_url": ""}))["ok"]
        s = await p.get_settings()
        assert s["catalog_url"] == "" and p.core.catalog.url == BASE
    h.run(body)


def test_unwritable_cache_is_an_error_not_an_exception(h, monkeypatch):
    from inkterface_core.catalog.client import Catalog

    arm(h, [make_screen("demo")])

    def boom(self, path, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Catalog, "_atomic_write", boom)

    async def body(p):
        r = await p.catalog_refresh()
        assert r["ok"] is False and "No space left" in r["error"]
    h.run(body)

"""import / approve / uninstall callables and code screens on the panel."""
import hashlib
import json
import urllib.error

import pytest
from packages_testlib import GOOD_CODE, package_zip

from inkterface_core.packages import PackageError

CODE = GOOD_CODE
SHA = hashlib.sha256(CODE.encode()).hexdigest()


def code_zip(pid="code-demo", version="1.0.0", code=CODE):
    return package_zip(pid, version, code=code)


async def installed_state(p):
    """Only what an import can change: the screens' identity, versions, enabled/approval flags and settings, and the
    files in the package store. Live fields of list_screens (`current`, `pinned`, rotation) follow the running engine
    and are deliberately left out, so this does not depend on timing."""
    keys = ("id", "version", "builtin", "enabled", "has_code", "code_sha256", "approved", "needs_approval", "settings")
    screens = [{k: s[k] for k in keys} for s in await p.list_screens()]
    root = p.core.store.root
    files = sorted(str(f.relative_to(root)) for f in root.rglob("*") if f.is_file() and ".tmp" not in f.relative_to(root).parts)
    return screens, files


async def by_id(p, sid):
    return next((s for s in await p.list_screens() if s["id"] == sid), None)


def test_declarative_package_installs_enabled_and_joins_the_engine(h):
    async def body(p):
        r = await p.import_package(h.package("d.zip", package_zip("deco-pkg")))
        assert r["ok"] and not r["needs_approval"] and r["package"]["enabled"] and r["package"]["id"] == "deco-pkg"
        assert "deco-pkg" in p.core.engine.screens
        s = await by_id(p, "deco-pkg")
        assert not s["builtin"] and s["author"] == "Tester" and s["version"] == "1.0.0" and not s["has_code"]
        assert (await p.pin_screen("deco-pkg"))["ok"]
        await h.until(lambda: p.core.engine.status()["current_screen"] == "deco-pkg")
        assert "screens_changed" in h.names()
    h.run(body)


def test_code_package_waits_for_approval_and_shows_what_the_dialog_needs(h):
    async def body(p):
        r = await p.import_package(h.package("c.zip", code_zip()))
        pkg = r["package"]
        assert r["ok"] and r["needs_approval"] and not pkg["enabled"] and pkg["has_code"] and pkg["code_sha256"] == SHA
        assert (pkg["id"], pkg["version"], pkg["author"], pkg["approved"]) == ("code-demo", "1.0.0", "Tester", False)
        assert "code-demo" not in p.core.engine.screens
        assert not (await p.pin_screen("code-demo"))["ok"]
        r = await p.set_screen_enabled("code-demo", True)
        assert not r["ok"] and "needs approval" in r["error"]
        pr = await p.preview_screen("code-demo")
        assert not pr["ok"] and "approval" in pr["error"]
    h.run(body)


def test_approval_enables_the_screen_and_the_code_reaches_the_panel(h):
    async def body(p):
        await p.import_package(h.package("c.zip", code_zip()))
        r = await p.approve_package("code-demo", "1.0.0", SHA.upper())   # case-insensitive hash
        assert r["ok"] and r["package"]["approved"] and r["package"]["enabled"]
        assert "code-demo" in p.core.engine.screens
        assert (await p.preview_screen("code-demo"))["ok"]
        assert (await p.pin_screen("code-demo"))["ok"]
        # GOOD_CODE draws 40 black pixels on row 5: wire polarity 1 = black, 81 bytes per row.
        row5 = 5 * 81
        await h.until(lambda: bytes(h.panel.glass[row5:row5 + 5]) == b"\xff" * 5)
        assert h.panel.glass[row5 + 5] == 0 and h.panel.glass[0] == 0
        assert (await p.get_status())["current_screen"] == "code-demo"
    h.run(body)


@pytest.mark.parametrize("version,sha,msg", [
    ("2.0.0", SHA, "installed version"), ("1.0.0", "0" * 64, "sha256 mismatch"), ("1.0.0", "", "sha256 mismatch"),
    ("", SHA, "installed version")])
def test_wrong_approval_is_refused(h, version, sha, msg):
    async def body(p):
        await p.import_package(h.package("c.zip", code_zip()))
        r = await p.approve_package("code-demo", version, sha)
        assert not r["ok"] and msg in r["error"]
        assert "code-demo" not in p.core.engine.screens and (await by_id(p, "code-demo"))["needs_approval"]
    h.run(body)


@pytest.mark.parametrize("args", [("nope", "1.0.0", SHA), ("telemetry", "1.0.0", SHA), (None, "1.0.0", SHA),
                                   ("code-demo", None, SHA), ("code-demo", "1.0.0", None), ("code-demo", 1, 2)])
def test_approve_rejects_bad_input(h, args):
    async def body(p):
        await p.import_package(h.package("c.zip", code_zip()))
        assert (await p.approve_package(*args))["ok"] is False
    h.run(body)


def test_editing_approved_code_revokes_the_approval(h):
    async def body(p):
        await p.import_package(h.package("c.zip", code_zip()))
        await p.approve_package("code-demo", "1.0.0", SHA)
        path = p.core.store.get("code-demo").path / "screen.py"
        path.write_text(CODE + "\n# changed\n")
        await p.pin_screen("code-demo")
        s = await by_id(p, "code-demo")
        assert s["needs_approval"] and not s["enabled"] and s["code_sha256"] != SHA
        assert not (await p.preview_screen("code-demo"))["ok"]
        await p.set_screen_enabled("telemetry", True)   # any change re-syncs the engine's list
        assert "code-demo" not in p.core.engine.screens
    h.run(body)


def test_upgrade_needs_a_newer_version_and_a_new_approval(h):
    async def body(p):
        await p.import_package(h.package("c1.zip", code_zip()))
        await p.approve_package("code-demo", "1.0.0", SHA)
        r = await p.import_package(h.package("c1b.zip", code_zip()))
        assert not r["ok"] and "not newer" in r["error"] and "code-demo" in p.core.engine.screens
        r = await p.import_package(h.package("c2.zip", code_zip(version="1.1.0")))
        assert r["ok"] and r["needs_approval"] and "code-demo" not in p.core.engine.screens
    h.run(body)


@pytest.mark.parametrize("make,msg", [
    (lambda h: str(h.tmp / "missing.zip"), "missing.zip"),
    (lambda h: h.package("junk.zip", b"not a zip"), ""),
    (lambda h: h.package("empty.zip", b""), ""),
    (lambda h: "http://example.com/x.zip", "https"),
    (lambda h: str(h.tmp), ""),
    (lambda h: "", "file path"), (lambda h: "   ", "file path"), (lambda h: "x" * 3000, "file path"),
    (lambda h: None, "file path"),
])
def test_import_rejects_bad_sources_without_touching_state(h, make, msg):
    async def body(p):
        before = await installed_state(p)
        r = await p.import_package(make(h))
        assert r["ok"] is False and msg in r["error"] and r["error"]
        assert await installed_state(p) == before
    h.run(body)


def test_import_rejects_hostile_zips(h):
    from packages_testlib import raw_zip, with_json, zinfo
    import stat as st

    async def body(p):
        evil = [with_json([(zinfo("demo-pkg/../../evil.txt"), b"x")]),
                with_json([(zinfo("demo-pkg/link", 0o777, st.S_IFLNK), b"/etc/passwd")]),
                with_json([(zinfo("demo-pkg/a.so"), b"\x7fELF")]),
                raw_zip([(zinfo("other/screen.json"), b"{}")])]
        for i, z in enumerate(evil):
            r = await p.import_package(h.package(f"e{i}.zip", z))
            assert r["ok"] is False, i
        assert [s["id"] for s in await p.list_screens() if not s["builtin"]] == []
    h.run(body)


def test_import_from_https_uses_the_downloader(h):
    async def body(p):
        calls = []

        def fetch(url, max_bytes, timeout):
            calls.append(url)
            return package_zip("web-pkg")
        p.core.store._fetch = fetch
        r = await p.import_package("https://example.com/web-pkg-1.0.0.zip")
        assert r["ok"] and calls == ["https://example.com/web-pkg-1.0.0.zip"] and "web-pkg" in p.core.engine.screens

        def broken(url, max_bytes, timeout):
            raise PackageError(f"{url}: download failed: boom")
        p.core.store._fetch = broken
        r = await p.import_package("https://example.com/other.zip")
        assert not r["ok"] and "download failed: boom" in r["error"]
    h.run(body)


def test_uninstall_removes_the_package_and_everything_that_pointed_at_it(h):
    async def body(p):
        await p.import_package(h.package("d.zip", package_zip("deco-pkg")))
        await p.set_rotation(["telemetry", "deco-pkg"])
        await p.pin_screen("deco-pkg")
        await p.set_screen_settings("deco-pkg", {})
        d = p.core.store.root / "deco-pkg"
        assert d.exists()
        r = await p.uninstall_package("deco-pkg")
        assert r["ok"] and not d.exists() and "deco-pkg" not in p.core.engine.screens
        s = await p.get_settings()
        assert s["rotation"] == ["telemetry"] and s["pinned"] is None
        assert await by_id(p, "deco-pkg") is None
        assert {"screens_changed", "settings_changed"} <= set(h.names())
    h.run(body)


def test_uninstall_cleans_idle_selection_and_per_screen_settings(h):
    from test_callables import idle_only_zip, settings_zip

    async def body(p):
        await p.import_package(h.package("i.zip", idle_only_zip()))
        await p.import_package(h.package("s.zip", settings_zip()))
        await p.set_settings({"idle_screens": ["idle-demo"]})
        await p.set_screen_settings("cfg-demo", {"n": 4})
        assert (await p.uninstall_package("idle-demo"))["ok"] and (await p.uninstall_package("cfg-demo"))["ok"]
        s = await p.get_settings()
        assert s["idle_screens"] == [] and s["screen_settings"] == {} and h.host_file()["idle_screens"] == []
    h.run(body)


@pytest.mark.parametrize("sid", ["telemetry", "nope", None, 5, ""])
def test_uninstall_refuses_builtins_unknown_and_bad_ids(h, sid):
    async def body(p):
        r = await p.uninstall_package(sid)
        assert r["ok"] is False and "telemetry" in {s["id"] for s in await p.list_screens()}
    h.run(body)


def test_packages_survive_a_restart_with_their_approval(h):
    async def first(p):
        await p.import_package(h.package("c.zip", code_zip()))
        await p.approve_package("code-demo", "1.0.0", SHA)
    h.run(first)

    async def second(p):
        s = await by_id(p, "code-demo")
        assert s["approved"] and s["enabled"] and "code-demo" in p.core.engine.screens
    h.run(second)


def test_a_screen_that_fails_to_load_is_reported_not_fatal(h):
    async def body(p):
        await p.import_package(h.package("d.zip", package_zip("deco-pkg")))
        (p.core.store.get("deco-pkg").path / "screen.json").write_text("{broken")
        await p.set_screen_enabled("telemetry", True)
        assert "deco-pkg" not in p.core.engine.screens and (await p.get_status())["connected"]
        assert await by_id(p, "deco-pkg") is None
    h.run(body)


def test_install_oserror_is_reported(h, monkeypatch):
    async def body(p):
        def boom(source):
            raise OSError(13, "Permission denied")
        monkeypatch.setattr(p.core.store, "install", boom)
        r = await p.import_package("/root/x.zip")
        assert not r["ok"] and "Permission denied" in r["error"]
    h.run(body)

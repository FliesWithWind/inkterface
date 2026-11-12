"""End to end with the REAL catalog repo: build the index from its screens, sign it with a throwaway key, serve it over
real HTTP on 127.0.0.1 only, and install through PluginCore (main.py wrappers) with that key as the test key.

The catalog repo is a separate checkout (default: a sibling directory, or INKTERFACE_CATALOG_REPO). Without it these tests skip.
The https-only rule stays in force: the plugin is pointed at the https default URL and the fetch seam rewrites that origin
to the loopback server, so the URL checks and the relative-path joins are the production ones."""
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from inkterface_core.catalog import DEFAULT_URL
from inkterface_core.ota import ed25519
from inkterface_core.packages.extract import PackageError

DECKY = Path(__file__).resolve().parents[2]
REPO = Path(os.environ.get("INKTERFACE_CATALOG_REPO") or DECKY.parents[1] / "inkterface-screens-catalog")
CORE = DECKY / "py_modules"
SEED = bytes(range(32))                      # throwaway, test-only
PUB = ed25519.publickey(SEED)

pytestmark = pytest.mark.skipif(not (REPO / "tools" / "build_catalog.py").is_file(), reason=f"catalog repo not found at {REPO}")


def tool(*args, key_seed=None):
    env = {**os.environ, "INKTERFACE_CORE": str(CORE)}
    if key_seed is not None:
        env["INKTERFACE_CATALOG_KEY"] = key_seed.hex()
    r = subprocess.run([sys.executable, str(REPO / "tools" / args[0]), *args[1:]], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"{args}: {r.stdout}\n{r.stderr}"


def build_site(out: Path, serial: int, seed: bytes = SEED) -> Path:
    tool("build_catalog.py", "--out", str(out), "--serial", str(serial))
    tool("sign_index.py", str(out / "index.json"), "--key-env", "INKTERFACE_CATALOG_KEY", key_seed=seed)
    return out


class Site:
    """Serves a directory on 127.0.0.1 (never any other interface); `fetch` is the plugin's injected downloader."""

    def __init__(self, root: Path):
        self.server = self.thread = None
        self.serve(root)

    def serve(self, root: Path):
        self.close()
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        handler.log_message = lambda *a: None
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="e2e-site")
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(5)
            self.server = None

    def fetch(self, url, max_bytes, timeout=15.0):
        assert url.startswith(DEFAULT_URL), url          # only the https catalog origin is ever requested
        try:
            with urllib.request.urlopen(self.origin + url[len(DEFAULT_URL):], timeout=timeout) as r:
                body = r.read(max_bytes + 1)
        except (urllib.error.URLError, OSError) as e:
            raise PackageError(f"{url}: download failed: {e}") from None
        if len(body) > max_bytes:
            raise PackageError(f"{url}: download exceeds {max_bytes} bytes")
        return body


@pytest.fixture(scope="module")
def site_dir(tmp_path_factory):
    return build_site(tmp_path_factory.mktemp("site"), serial=2000)


@pytest.fixture
def site(site_dir, tmp_path):
    work = tmp_path / "site"
    shutil.copytree(site_dir, work)
    s = Site(work)
    s.dir = work
    yield s
    s.close()


def test_index_built_by_the_repo_is_what_the_plugin_parses(site_dir):
    raw = (site_dir / "index.json").read_bytes()
    assert len((site_dir / "index.json.sig").read_bytes()) == 64
    from inkterface_core.catalog import verify_and_parse
    idx = verify_and_parse(raw, (site_dir / "index.json.sig").read_bytes(), PUB)
    assert [e.id for e in idx.entries] == ["gpu-focus", "month-calendar", "world-clock"]
    assert {e.id: e.has_code for e in idx.entries} == {"gpu-focus": False, "month-calendar": True, "world-clock": False}


def test_install_declarative_and_code_screen_through_plugincore(h, site):
    h.use_catalog(site.fetch, PUB)

    async def body(p):
        assert (await p.catalog_refresh())["count"] == 3
        listed = {e["id"]: e for e in (await p.catalog_list())["entries"]}
        assert set(listed) == {"gpu-focus", "month-calendar", "world-clock"} and not any(e["installed"] for e in listed.values())
        assert (await p.catalog_preview("gpu-focus"))["data_url"].startswith("data:image/png;base64,")

        r = await p.catalog_install("gpu-focus", listed["gpu-focus"]["version"])
        assert r["ok"] and not r["needs_approval"] and r["package"]["enabled"]
        assert "gpu-focus" in p.core.engine.screens
        assert (await p.preview_screen("gpu-focus"))["ok"]

        r = await p.catalog_install("month-calendar", listed["month-calendar"]["version"])
        pkg = r["package"]
        assert r["ok"] and r["needs_approval"] and pkg["has_code"] and not pkg["enabled"]
        assert "month-calendar" not in p.core.engine.screens                     # unapproved code does not run
        assert (await p.approve_package("month-calendar", pkg["version"], pkg["code_sha256"]))["ok"]
        assert "month-calendar" in p.core.engine.screens
        assert (await p.preview_screen("month-calendar"))["ok"]

        after = {e["id"]: e for e in (await p.catalog_list())["entries"]}
        assert after["gpu-focus"]["installed"] and after["month-calendar"]["installed"] and not after["world-clock"]["installed"]
        assert (await p.catalog_updates())["updates"] == []
        assert "screens_changed" in h.names()

        # a second install of the same version is refused by the store's strictly-newer rule
        assert not (await p.catalog_install("gpu-focus", listed["gpu-focus"]["version"]))["ok"]
        # developer import stays gated even though the catalog works
        p.core.host.developer_mode = False
        assert "developer feature" in (await p.import_package("/nope.zip"))["error"]
    h.run(body)


def test_tampered_zip_is_refused_and_nothing_is_installed(h, site):
    h.use_catalog(site.fetch, PUB)
    z = next((site.dir / "zips").glob("world-clock-*.zip"))
    z.write_bytes(z.read_bytes() + b"\0")

    async def body(p):
        assert (await p.catalog_refresh())["ok"]                                   # the index itself is untouched
        v = next(e for e in (await p.catalog_list())["entries"] if e["id"] == "world-clock")["version"]
        r = await p.catalog_install("world-clock", v)
        assert not r["ok"] and "catalog" in r["error"]
        assert not next(e for e in (await p.catalog_list())["entries"] if e["id"] == "world-clock")["installed"]
    h.run(body)


def test_tampered_index_and_wrong_key_are_refused(h, site):
    idx = site.dir / "index.json"
    idx.write_bytes(idx.read_bytes().replace(b'"serial": 2000', b'"serial": 2001'))
    h.use_catalog(site.fetch, PUB)

    async def body(p):
        r = await p.catalog_refresh()
        assert not r["ok"] and "signature" in r["error"].lower()
        assert (await p.catalog_list())["entries"] == []
    h.run(body)


def test_wrong_key_refuses_a_good_index(h, site):
    h.use_catalog(site.fetch, ed25519.publickey(bytes(32)))

    async def body(p):
        r = await p.catalog_refresh()
        assert not r["ok"] and "signature" in r["error"].lower()
    h.run(body)


def test_older_serial_is_refused_after_a_newer_one_was_saved(h, site, tmp_path):
    older = build_site(tmp_path / "older", serial=1500)
    h.use_catalog(site.fetch, PUB)

    async def body(p):
        assert (await p.catalog_refresh())["ok"]
        site.serve(older)                                                          # the server now replays an older, validly signed index
        r = await p.catalog_refresh()
        assert not r["ok"] and "older" in r["error"]
        assert len((await p.catalog_list())["entries"]) == 3                        # saved catalog untouched
    h.run(body)


def test_offline_uses_the_saved_catalog(h, site):
    h.use_catalog(site.fetch, PUB)

    async def body(p):
        assert (await p.catalog_refresh())["ok"]
        assert (await p.catalog_preview("gpu-focus"))["ok"]
        site.close()                                                               # network gone
        assert not (await p.catalog_refresh())["ok"]
        assert len((await p.catalog_list())["entries"]) == 3
        assert (await p.catalog_preview("gpu-focus"))["ok"]                         # from the disk cache
    h.run(body)


def test_site_files_are_what_the_contract_says(site_dir):
    doc = json.loads((site_dir / "index.json").read_text())
    assert doc["schema"] == 1 and doc["serial"] == 2000
    for e in doc["screens"]:
        assert (site_dir / e["zip"]).is_file() and (site_dir / e["preview"]).is_file()

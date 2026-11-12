"""Firmware updates through PluginCore, end to end over the emulated panel.

Real: PluginCore, Engine thread, FirmwareManager, ota/* (verify, download, stream), the emulator's
OTA/rollback model. Fake: GitHub (ota_helpers.FakeGitHub, TEST-ONLY signing key), the wall clock, `sleep`.
"""
import hashlib
import sys
import threading
import time
from pathlib import Path

import pytest

DECKY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent / "ota"))

import ota_helpers as H  # noqa: E402

from inkterface_core import firmware as FW  # noqa: E402
from inkterface_core import proto as P  # noqa: E402
from inkterface_core.emulator import EmulatedPanel  # noqa: E402
from inkterface_core.engine import EmulatedLink  # noqa: E402
from inkterface_core.ota import github  # noqa: E402
from inkterface_core.ota.errors import NetworkError, TooLarge  # noqa: E402
from inkterface_core.plugin_core import PluginCore  # noqa: E402
from inkterface_core.sources import Registry, TimeSource  # noqa: E402

IMAGE = H.make_image(40_000)
SHA = hashlib.sha256(IMAGE).hexdigest()


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class LoggedLink(EmulatedLink):
    """EmulatedLink that records who wrote what, and lets a test hook the Nth OTA_DATA write."""

    def __init__(self, rig, thread, **kw):
        super().__init__(rig.panel, **kw)
        self.rig, self.thread, self.data_frames = rig, thread, 0

    def write(self, data, timeout=None):
        for f in P.Decoder().feed(data):
            self.rig.log.append((self.thread, f.type, f.payload[:4] if f.type == P.T_OTA_DATA else b""))
            if f.type == P.T_OTA_DATA:
                self.data_frames += 1
                hook = self.rig.on_data
                if hook is not None and self.data_frames >= hook[0]:
                    self.rig.on_data = None
                    hook[1](self)
        super().write(data, timeout)


class Rig:
    def __init__(self, tmp_path, *, installed="1.0.0", key=H.PUB, version="1.0.0", panel=None, publish="1.2.0",
                 tick_s=3600.0, plugged=True, **fw_kw):
        self.panel = panel or EmulatedPanel(fw_ver=installed, ota_slot=H.SLOT)
        self.gh, self.clock, self.log, self.events = H.FakeGitHub(), Clock(), [], []
        self.links, self.plugged = [], plugged
        self.fail_updater_link_after: int | None = None   # byte budget for the NEXT link the updater opens
        self.on_data = None                               # (n, fn(link)): fn runs inside the Nth OTA_DATA write
        self.on_updater_open = None                       # fn(rig) when the updater opens a link
        self.key = key
        if publish:
            self.publish(publish)
        self.core = PluginCore(
            tmp_path / "settings", DECKY / "screens", connect=self.connect, version=version,
            registry_factory=lambda s: Registry([TimeSource()]), fw_fetch=self.gh, public_key=lambda: self.key,
            clock=self.clock, sleep=lambda s: None, handshake_timeout=0.5, confirm_timeout=3.0, tick_s=tick_s,
            on_firmware=self.events.append, **fw_kw)

    def check(self):
        """A manual check past the 60 s reuse window (the fake clock does not move by itself)."""
        self.clock.t += 61
        return self.core.check_firmware()

    def publish(self, version="1.2.0", image=IMAGE, **kw):
        self.gh.listing([self.gh.publish(version, image, **kw)])

    def connect(self):
        if not self.plugged:
            raise FileNotFoundError("no panel found (USB VID 303a)")
        who = threading.current_thread().name
        if self.panel.ota_committed:   # END was accepted: the panel resets before the next open
            self.panel.reboot()
        if who == "inkterface-fwinstall":
            if self.on_updater_open:
                self.on_updater_open(self)
        link = LoggedLink(self, who)
        if who == "inkterface-fwinstall" and self.fail_updater_link_after is not None:
            link.fail_after, self.fail_updater_link_after = self.fail_updater_link_after, None
        self.links.append(link)
        return link

    # -- helpers ------------------------------------------------------------------
    def __enter__(self):
        self.core.start()
        # start() launches the first release check on its own thread. Without this wait a test that publishes and then
        # calls check_firmware() can join that still-running check (which saw the old listing) and read its stale result.
        self.until(lambda: self.fw["check"]["at"] is not None, what="first release check")
        return self

    def __exit__(self, *exc):
        self.core.shutdown()

    @staticmethod
    def until(pred, timeout=10.0, what="condition"):
        end = time.monotonic() + timeout
        while not pred():
            assert time.monotonic() < end, f"{what} not reached in time"
            time.sleep(0.005)

    @property
    def fw(self):
        return self.core.get_firmware_status()

    def wait_check(self):
        self.until(lambda: self.fw["check"]["at"] is not None, what="first release check")
        return self.fw

    def wait_connected(self, version=None):
        self.until(lambda: self.core.engine.status()["connected"] and (version is None or self.core.engine.status()["fw_version"] == version),
                   what=f"engine connected on {version}")

    def wait_install(self, *states):
        self.until(lambda: self.fw["install"]["state"] in (states or ("done", "failed", "cancelled")), what=f"install in {states}")
        return self.fw["install"]

    def ota_types(self):
        return [t for _, t, _ in self.log if t in (P.T_OTA_BEGIN, P.T_OTA_DATA, P.T_OTA_END)]

    def states(self):
        out = []
        for e in self.events:
            s = e["install"]["state"]
            if not out or out[-1] != s:
                out.append(s)
        return out


@pytest.fixture
def rig(tmp_path):
    with Rig(tmp_path) as r:
        r.wait_check()
        r.wait_connected("1.0.0")
        yield r


# ================================================================================ happy path
def test_update_is_found_at_start_and_never_installed_by_itself(rig):
    fw = rig.fw
    assert fw["latest"] == {"version": "1.2.0", "tag": "fw-v1.2.0", "changelog": "Faster refresh.", "size": len(IMAGE),
                            "page_url": "https://github.com/FliesWithWind/inkterface/releases/tag/fw-v1.2.0"}
    assert fw["update_available"] is True and fw["note"] is None
    assert fw["installed"] == {"version": "1.0.0", "dev_build": False, "protocol_version": 1, "connected": True, "seen": True}
    assert fw["trust"] == {"state": "ok", "message": None}
    assert fw["check"]["ok"] is True and fw["check"]["error"] is None and fw["check"]["at"] == rig.clock.t
    assert fw["install"]["state"] == "idle" and not fw["protocol_mismatch"]
    time.sleep(0.2)
    assert rig.ota_types() == [] and rig.panel.installed is None   # nothing was sent: the user has not tapped Install


def test_install_end_to_end(rig):
    assert rig.core.install_firmware("1.2.0") == {"ok": True}
    assert rig.wait_install()["state"] == "done"
    p = rig.panel
    # the emulator got exactly the signed image, and the new image is confirmed (not pending rollback)
    assert hashlib.sha256(p.installed).hexdigest() == SHA and p.installed == IMAGE
    assert p.fw_ver == "1.2.0" and p.reboots == 1 and p.pending_verify is False
    fw = rig.fw
    inst = fw["install"]
    assert inst["done"] == inst["total"] == len(IMAGE) and inst["percent"] == 100 and inst["error"] is None
    assert "installed and confirmed" in inst["message"]
    # status shows the new version at once, from the confirmed HELLO, and update_available flips off
    assert fw["installed"]["version"] == "1.2.0" and fw["update_available"] is False
    # the engine got the port back, reconnected to the rebooted panel and draws again
    rig.wait_connected("1.2.0")
    rig.until(lambda: rig.core.engine.status()["frames_rendered"] > 0 and p.mode == P.MODE_HOST, what="engine drawing again")
    assert rig.fw["installed"]["version"] == "1.2.0"
    # events: states in order, progress monotonic, every event is a full status
    assert rig.states() == ["idle", "downloading", "preparing", "sending", "confirming", "done"]
    pct = [e["install"]["percent"] for e in rig.events if e["install"]["state"] == "sending"]
    assert pct == sorted(pct) and pct[-1] < 100 and len(pct) >= 3
    assert all("latest" in e and "trust" in e for e in rig.events)


def test_downloading_is_emitted_before_the_install_thread_starts(rig, monkeypatch):
    """Else a worker that wins the race reaches 'preparing' first and the UI never sees 'downloading' (flaky under load)."""
    seen = []
    start = threading.Thread.start

    def spy(self):
        if self.name == "inkterface-fwinstall":
            seen.append([e["install"]["state"] for e in rig.events][-1])
        start(self)

    monkeypatch.setattr(threading.Thread, "start", spy)
    assert rig.core.install_firmware("1.2.0") == {"ok": True}
    rig.wait_install()
    assert seen == ["downloading"]


def test_engine_is_paused_for_the_whole_transfer_and_never_talks_to_the_panel_meanwhile(rig):
    seen = []
    rig.on_updater_open = lambda r: seen.append(r.core.engine.status()["state"])
    rig.on_data = (3, lambda link: seen.append(rig.core.engine.status()["state"]))
    engine_links = list(rig.links)
    assert rig.core.install_firmware("1.2.0")["ok"]
    rig.wait_install()
    assert set(seen) == {"paused"} and len(seen) >= 2
    assert all(link.closed for link in engine_links)
    first, last = (next(i for i, e in enumerate(rig.log) if e[1] == P.T_OTA_BEGIN),
                   max(i for i, e in enumerate(rig.log) if e[0] == "inkterface-fwinstall"))
    assert not [e for e in rig.log[first:last + 1] if e[0] == "inkterface-engine"]
    rig.wait_connected("1.2.0")


def test_install_sends_only_ota_and_hello_frames(rig):
    rig.core.install_firmware("1.2.0")
    rig.wait_install()
    kinds = {t for who, t, _ in rig.log if who == "inkterface-fwinstall"}
    assert kinds <= {P.T_HELLO, P.T_OTA_BEGIN, P.T_OTA_DATA, P.T_OTA_END}
    # the HELLO ack that validates the image is the LAST thing the updater sent
    last = [e for e in rig.log if e[0] == "inkterface-fwinstall"][-1]
    assert last[1] == P.T_HELLO


# ================================================================================ trust and policy
def test_no_signing_key_bundled_is_visible_and_refuses_everything(tmp_path):
    with Rig(tmp_path, key=None) as r:
        fw = r.wait_check()
        assert fw["trust"]["state"] == "no_key" and "No release signing key" in fw["trust"]["message"]
        assert fw["check"]["ok"] is False and fw["check"]["error_kind"] == "NoTrustKey" and "signing key" in fw["check"]["error"]
        assert fw["latest"] is None and fw["update_available"] is False
        assert r.gh.calls == []   # fail closed before any network access
        res = r.core.install_firmware("1.2.0")
        assert res["ok"] is False and "signing key" in res["error"]
        assert r.core.check_firmware()["check"]["error_kind"] == "NoTrustKey" and r.gh.calls == []
        time.sleep(0.1)
        assert r.ota_types() == [] and r.panel.installed is None


def test_the_shipped_trust_module_is_still_empty_so_the_default_build_shows_no_key(tmp_path, monkeypatch):
    from inkterface_core.ota import trust
    monkeypatch.setattr(trust, "PRODUCTION_PUBLIC_KEY_HEX", "")
    r = Rig(tmp_path)
    r.core = PluginCore(tmp_path / "s2", DECKY / "screens", connect=r.connect, registry_factory=lambda s: Registry([TimeSource()]),
                        fw_fetch=r.gh, sleep=lambda s: None)   # default public_key = trust.public_key
    with r:
        r.until(lambda: r.fw["check"]["at"] is not None)
        assert r.fw["trust"]["state"] == "no_key" and r.gh.calls == []


@pytest.mark.parametrize("how", ["wrong_key", "tampered_manifest", "bad_sig_length", "unsigned"])
def test_bad_signature_is_an_error_and_nothing_is_offered(tmp_path, how):
    with Rig(tmp_path, publish=None) as r:
        doc = H.manifest_doc(IMAGE, "1.2.0")
        raw, sig = H.sign_doc(doc)
        kw = {"wrong_key": dict(seed=H.OTHER_SEED), "bad_sig_length": dict(raw=raw, sig=sig[:-1]),
              "tampered_manifest": dict(raw=raw.replace(b"Faster", b"Slower"), sig=sig),
              "unsigned": dict(raw=raw, sig=b"")}[how]
        r.publish("1.2.0", **kw)
        fw = r.core.check_firmware()
        assert fw["check"]["ok"] is False and fw["check"]["error_kind"] == "BadSignature"
        assert fw["latest"] is None and fw["update_available"] is False
        res = r.core.install_firmware("1.2.0")
        assert res["ok"] is False and "no verified firmware release" in res["error"]
        assert r.ota_types() == []


def test_a_bad_release_does_not_hide_a_known_good_one(rig):
    assert rig.fw["latest"]["version"] == "1.2.0"
    rig.publish("1.3.0", seed=H.OTHER_SEED)   # newest release is badly signed
    fw = rig.check()
    assert fw["check"]["ok"] is False and fw["check"]["error_kind"] == "BadSignature"
    assert fw["latest"]["version"] == "1.2.0" and fw["update_available"] is True   # last good result stays visible


def test_downgrade_is_refused(tmp_path):
    with Rig(tmp_path, installed="2.0.0") as r:
        r.wait_check()
        r.wait_connected("2.0.0")
        fw = r.fw
        assert fw["update_available"] is False and "newer than the latest release 1.2.0" in fw["note"]
        res = r.core.install_firmware("1.2.0")
        assert res["ok"] is False and "older than the installed 2.0.0" in res["error"]
        assert r.ota_types() == [] and r.fw["install"]["state"] == "idle"


def test_same_version_is_not_reinstalled(tmp_path):
    with Rig(tmp_path, installed="1.2.0") as r:
        r.wait_check()
        r.wait_connected("1.2.0")
        assert r.fw["update_available"] is False and r.fw["note"] is None
        res = r.core.install_firmware("1.2.0")
        assert res["ok"] is False and "already runs 1.2.0" in res["error"]


@pytest.mark.parametrize("dev", ["gabc123-DIRTY", "0.0.0-dev+308c457", "0.0.0-dev+308c457.dirty", "0.0.0-dev+unknown"])
def test_dev_build_is_offered_the_release(tmp_path, dev):
    with Rig(tmp_path, installed=dev) as r:
        r.wait_check()
        r.wait_connected()
        fw = r.fw
        assert fw["installed"]["dev_build"] is True and fw["update_available"] is True
        assert r.core.install_firmware("1.2.0")["ok"]
        assert r.wait_install()["state"] == "done" and r.panel.fw_ver == "1.2.0"


@pytest.mark.parametrize("version", [None, 120, "1.2", "9.9.9", "fw-v1.2.0", ["1.2.0"]])
def test_install_only_accepts_the_verified_latest_version(rig, version):
    res = rig.core.install_firmware(version)
    assert res["ok"] is False and "not the latest verified release (1.2.0)" in res["error"]
    assert rig.ota_types() == []


def test_install_needs_a_release_a_seen_panel_and_no_other_install(tmp_path):
    with Rig(tmp_path, publish=None) as r:
        r.gh.listing([])
        r.core.check_firmware()   # the start-up check ran against an empty fake and failed; this one sees the empty listing
        assert r.fw["latest"] is None and r.fw["update_available"] is False and r.fw["check"]["ok"] is True
        assert "no verified firmware release" in r.core.install_firmware("1.2.0")["error"]
    with Rig(tmp_path / "b", plugged=False) as r:
        r.wait_check()
        assert r.fw["installed"] == {"version": None, "dev_build": False, "protocol_version": None, "connected": False, "seen": False}
        assert "has not been seen yet" in r.core.install_firmware("1.2.0")["error"]
        assert r.fw["update_available"] is True   # the release is real; only the panel is missing


def test_second_install_while_one_runs_is_refused(rig):
    results = []
    rig.on_data = (2, lambda link: results.append(rig.core.install_firmware("1.2.0")))
    rig.core.install_firmware("1.2.0")
    assert rig.wait_install()["state"] == "done"
    assert results == [{"ok": False, "error": "a firmware update is already running"}]


@pytest.mark.parametrize("plugin,offered", [("1.0.0-beta.1", True), ("1.0.0", False)])
def test_only_a_prerelease_plugin_is_offered_prerelease_firmware(tmp_path, plugin, offered):
    with Rig(tmp_path, publish=None, version=plugin, installed="1.0.0-beta.1") as r:
        r.publish("1.0.0-beta.2", prerelease=True, doc=H.manifest_doc(IMAGE, "1.0.0-beta.2", min_plugin_version="0.0.1"))
        fw = r.core.check_firmware()
        assert fw["check"]["ok"] is True, fw["check"]
        assert (fw["latest"] or {}).get("version") == ("1.0.0-beta.2" if offered else None)
        assert fw["update_available"] is offered
        if offered:
            r.wait_connected("1.0.0-beta.1")
            assert r.core.install_firmware("1.0.0-beta.2") == {"ok": True}
            assert r.wait_install()["state"] == "done" and r.panel.fw_ver == "1.0.0-beta.2"


# ================================================================================ failures during install
def test_link_drop_mid_stream_resumes_from_the_panels_offset(rig):
    rig.fail_updater_link_after = 12_000   # first updater link dies after ~12 KB
    rig.core.install_firmware("1.2.0")
    assert rig.wait_install()["state"] == "done"
    assert rig.panel.installed == IMAGE and rig.panel.fw_ver == "1.2.0" and rig.panel.pending_verify is False
    updater = [l for l in rig.links if l.thread == "inkterface-fwinstall"]
    assert len(updater) >= 3   # dropped link, resumed link, confirm link
    begins = [i for i, e in enumerate(rig.log) if e[1] == P.T_OTA_BEGIN]
    assert len(begins) == 2
    second = [e for e in rig.log[begins[1]:] if e[1] == P.T_OTA_DATA][0]
    assert int.from_bytes(second[2], "little") > 0   # resumed, not restarted from 0
    assert sum(1 for e in rig.log if e[1] == P.T_OTA_DATA) < 2 * (len(IMAGE) // 4080 + 1)
    rig.wait_connected("1.2.0")


def test_panel_unplugged_for_good_fails_cleanly_and_the_engine_comes_back(rig):
    def unplug(link):
        rig.plugged = False
        link.dead = True

    rig.on_data = (2, unplug)
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert inst["state"] == "failed" and "cannot open the panel" in inst["error"] and inst["rollback_pending"] is False
    assert "previous firmware" in inst["message"]
    assert rig.panel.installed is None and rig.panel.fw_ver == "1.0.0"
    assert rig.core.engine.status()["state"] != "paused"   # engine got the port back
    rig.plugged = True
    rig.wait_connected("1.0.0")
    # the user retries: the panel still holds the partial session, so this resumes
    rig.core.install_firmware("1.2.0")
    assert rig.wait_install("done")["state"] == "done" and rig.panel.installed == IMAGE
    assert rig.fw["install"]["error"] is None


def test_panel_rejects_the_flash_write(rig):
    rig.panel.ota_fail_write_at = 10_000
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert inst["state"] == "failed" and "OTA_FLASH_FAIL" in inst["error"]
    assert rig.panel.installed is None and rig.panel.fw_ver == "1.0.0"
    assert rig.core.engine.status()["state"] != "paused"
    rig.wait_connected("1.0.0")


def test_firmware_without_ota_support_says_so(tmp_path):
    with Rig(tmp_path, panel=EmulatedPanel(fw_ver="0.9.0")) as r:   # no ota_slot => no CAP_OTA
        r.wait_check()
        r.wait_connected("0.9.0")
        r.core.install_firmware("1.2.0")
        inst = r.wait_install()
        assert inst["state"] == "failed" and "no OTA support" in inst["error"]
        r.wait_connected("0.9.0")


def test_download_hash_mismatch_fails_before_the_engine_is_touched(rig):
    states = []
    rig.gh.routes[H.dl_url("fw-v1.2.0", "inkterface-ee05-1.2.0.bin")] = IMAGE[:-1] + b"X"   # same size, other bytes
    rig.on_updater_open = lambda r: states.append("opened")
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert inst["state"] == "failed" and "sha256" in inst["error"]
    assert states == [] and rig.ota_types() == [] and rig.core.engine.status()["state"] == "ready"
    assert "paused" not in {e["install"]["state"] for e in rig.events} and rig.panel.reboots == 0


@pytest.mark.parametrize("failure", [NetworkError("cannot reach GitHub: down"), TooLarge("too big"), github.Response(404), github.Response(500)])
def test_download_errors_are_reported(rig, failure):
    rig.gh.routes[H.dl_url("fw-v1.2.0", "inkterface-ee05-1.2.0.bin")] = failure
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert inst["state"] == "failed" and inst["error"] and rig.core.engine.status()["state"] == "ready"


def test_panel_that_does_not_confirm_is_reported_as_pending_rollback(rig):
    orig = rig.connect

    def connect():
        link = orig()
        if rig.panel.reboots == 1 and rig.panel.pending_verify:   # the new image was bad: watchdog rolled it back
            rig.panel.watchdog_expire()
            link.close()
            return orig()
        return link

    rig.connect = connect
    rig.core.firmware._connect = connect
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert inst["state"] == "failed" and inst["rollback_pending"] is True
    assert "rolls back to the previous version by itself" in inst["message"] and "expected '1.2.0'" in inst["error"]
    assert rig.panel.fw_ver == "1.0.0" and rig.panel.pending_verify is False
    rig.core.engine._connect = connect
    assert rig.core.engine.status()["state"] != "paused"


def test_unexpected_exception_does_not_leave_the_ui_stuck(rig, monkeypatch):
    monkeypatch.setattr(FW.stream, "install", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert inst["state"] == "failed" and "unexpected error: KeyError" in inst["error"]
    assert rig.core.engine.status()["state"] != "paused"


# ================================================================================ cancel
def test_cancel_during_transfer(rig):
    res = []
    rig.on_data = (4, lambda link: res.append(rig.core.cancel_firmware_install()))
    rig.core.install_firmware("1.2.0")
    inst = rig.wait_install()
    assert res == [{"ok": True}]
    assert inst["state"] == "cancelled" and inst["error"] is None and "cancelled" in inst["message"]
    assert rig.panel.installed is None and rig.panel.ota_committed is None and rig.panel.fw_ver == "1.0.0" and rig.panel.reboots == 0
    assert rig.core.engine.status()["state"] != "paused"
    rig.wait_connected("1.0.0")
    assert rig.states()[-1] == "cancelled"
    # and the same image can be installed afterwards
    rig.core.install_firmware("1.2.0")
    assert rig.wait_install("done")["state"] == "done" and rig.panel.installed == IMAGE


def test_cancel_with_nothing_running_and_too_late_to_cancel(rig):
    assert rig.core.cancel_firmware_install() == {"ok": False, "error": "no firmware update is running"}
    late = []
    rig.on_updater_open = lambda r: late.append(r.core.cancel_firmware_install()) if r.panel.ota_committed is None and r.panel.reboots == 1 else None
    rig.core.install_firmware("1.2.0")
    assert rig.wait_install()["state"] == "done"
    assert late and late[0]["ok"] is False and "too late to cancel" in late[0]["error"]


def test_cancel_before_the_download_finishes(rig):
    gate = threading.Event()
    real = rig.gh

    def slow(url, max_bytes):
        if url.endswith(".bin"):
            gate.wait(5)
        return real(url, max_bytes)

    rig.core.firmware._fetch = slow
    rig.core.install_firmware("1.2.0")
    rig.until(lambda: rig.fw["install"]["state"] == "downloading")
    assert rig.core.cancel_firmware_install() == {"ok": True}
    gate.set()
    assert rig.wait_install()["state"] == "cancelled"
    assert rig.core.engine.status()["state"] == "ready" and rig.ota_types() == []


def test_shutdown_mid_install_cancels_it_and_leaks_nothing(tmp_path):
    before = {t.name for t in threading.enumerate()}
    r = Rig(tmp_path)
    r.core.start()
    r.wait_check()
    r.wait_connected("1.0.0")
    gate = threading.Event()
    r.on_data = (2, lambda link: gate.wait(5))
    r.core.install_firmware("1.2.0")
    r.until(lambda: r.fw["install"]["state"] == "sending" or gate.is_set() or r.on_data is None)
    threading.Timer(0.3, gate.set).start()
    r.core.shutdown()
    assert r.panel.installed is None and r.panel.reboots == 0
    time.sleep(0.1)
    assert [t.name for t in threading.enumerate() if t.name.startswith("inkterface-") and t.name not in before] == []
    r.core.shutdown()   # twice is fine


# ================================================================================ schedule + GitHub errors
def test_check_runs_at_start_then_every_24_hours(tmp_path):
    with Rig(tmp_path) as r:
        r.wait_check()
        listing = lambda: sum(1 for u, _ in r.gh.calls if u.endswith("releases?per_page=20"))   # noqa: E731
        assert listing() == 1
        t0 = r.clock.t
        assert r.core.firmware.tick() is False   # not due
        r.clock.t = t0 + 23 * 3600 + 3599
        assert r.core.firmware.tick() is False and listing() == 1
        r.publish("1.3.0")   # a newer release appears during the day
        r.clock.t = t0 + 24 * 3600
        assert r.core.firmware.tick() is True and listing() == 2
        assert r.fw["latest"]["version"] == "1.3.0" and r.fw["check"]["at"] == t0 + 24 * 3600
        assert r.fw["check"]["next_at"] == t0 + 48 * 3600
        assert r.core.firmware.tick() is False and listing() == 2


def test_the_scheduler_thread_does_the_daily_check_by_itself(tmp_path):
    with Rig(tmp_path, tick_s=0.01) as r:
        r.wait_check()
        n = lambda: sum(1 for u, _ in r.gh.calls if u.endswith("releases?per_page=20"))   # noqa: E731
        assert n() == 1
        time.sleep(0.1)
        assert n() == 1   # not due yet
        r.clock.t += 24 * 3600 + 1
        r.until(lambda: n() == 2, what="daily check by the scheduler thread")


def test_failed_check_retries_after_an_hour_not_a_day(tmp_path):
    with Rig(tmp_path) as r:
        r.wait_check()
        r.gh.routes[f"{github.API}/repos/{github.REPO}/releases?per_page=20"] = github.Response(500)
        t0 = r.clock.t = r.clock.t + 24 * 3600
        assert r.core.firmware.tick() is True
        fw = r.fw
        assert fw["check"]["ok"] is False and fw["check"]["error_kind"] == "NetworkError" and "unavailable" in fw["check"]["error"]
        assert fw["latest"]["version"] == "1.2.0" and fw["update_available"] is True   # outage keeps the known update
        assert fw["check"]["next_at"] == t0 + 3600
        r.clock.t = t0 + 3599
        assert r.core.firmware.tick() is False
        r.publish("1.2.0")   # GitHub is back
        r.clock.t = t0 + 3600
        assert r.core.firmware.tick() is True and r.fw["check"]["ok"] is True and r.fw["check"]["error"] is None


def test_rate_limit_is_respected(tmp_path):
    with Rig(tmp_path) as r:
        r.wait_check()
        reset = r.clock.t + 5 * 3600
        r.gh.routes[f"{github.API}/repos/{github.REPO}/releases?per_page=20"] = github.Response(
            403, b"", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)})
        r.clock.t += 24 * 3600
        r.core.firmware.tick()
        c = r.fw["check"]
        assert c["error_kind"] == "RateLimited" and "rate limit" in c["error"]
        assert c["next_at"] == max(reset, r.clock.t + 3600)


LIST = f"{github.API}/repos/{github.REPO}/releases?per_page=20"
LIMITED = lambda **h: github.Response(403, b"", {"x-ratelimit-remaining": "0", **h})   # noqa: E731


def n_listing(r):
    return sum(1 for u, _ in r.gh.calls if u == LIST)


def etag_rig(tmp_path, **kw):
    r = Rig(tmp_path, publish=None, **kw)
    r.gh.listing([r.gh.publish("1.2.0", IMAGE)], etag='"v1"')
    return r


def test_cadence_is_hours_and_a_manual_check_within_a_minute_reuses_the_result(rig):
    assert FW.CHECK_EVERY_S == 24 * 3600 and FW.RETRY_AFTER_ERROR_S >= 3600 and FW.MIN_RECHECK_S == 60
    assert n_listing(rig) == 1
    assert rig.core.check_firmware()["latest"]["version"] == "1.2.0" and n_listing(rig) == 1   # same instant
    rig.clock.t += 59
    rig.core.check_firmware()
    assert n_listing(rig) == 1
    rig.clock.t += 2
    rig.core.check_firmware()
    assert n_listing(rig) == 2


def test_a_failed_check_can_be_retried_at_once(rig):
    rig.gh.routes[LIST] = github.Response(502)
    assert rig.check()["check"]["ok"] is False
    rig.publish("1.2.0")   # Wi-Fi is back
    assert rig.core.check_firmware()["check"]["ok"] is True and n_listing(rig) == 3   # no 60 s wait after a failure


def listing_headers(r):
    return [h for (u, _), h in zip(r.gh.calls, r.gh.sent) if u == LIST]


def test_etag_is_cached_in_the_settings_dir_and_a_restart_makes_no_api_request(tmp_path):
    with etag_rig(tmp_path) as r:
        r.wait_check()
        t0 = r.clock.t
        assert listing_headers(r) == [{}]
        assert (tmp_path / "settings" / "cache" / "fw-releases.json").is_file()
    r2 = etag_rig(tmp_path)   # plugin restart (Decky reload, reboot) an hour later: same disk, new process
    r2.clock.t = t0 + 3600
    with r2:
        fw = r2.wait_check()
        assert listing_headers(r2) == []   # the list is < 24 h old: not one API request (a 304 would still cost quota)
        assert fw["check"]["ok"] is True and fw["latest"]["version"] == "1.2.0" and fw["update_available"] is True
        assert fw["check"]["next_at"] == t0 + 24 * 3600   # the schedule is the original one, not restarted
        r2.clock.t += 61
        r2.core.check_firmware()   # the user asks: now it does ask, conditionally
        assert listing_headers(r2) == [{"If-None-Match": '"v1"'}]
        assert r2.fw["check"]["next_at"] == r2.clock.t + 24 * 3600


def test_restart_after_a_day_asks_again_conditionally(tmp_path):
    with etag_rig(tmp_path) as r:
        r.wait_check()
        t0 = r.clock.t
    r2 = etag_rig(tmp_path)
    r2.clock.t = t0 + 24 * 3600
    with r2:
        r2.wait_check()
        assert listing_headers(r2) == [{"If-None-Match": '"v1"'}]
        assert r2.fw["latest"]["version"] == "1.2.0"


def test_idle_request_rate_is_far_below_the_quota(tmp_path):
    """A restart every 4 h for 40 h: the API sees the first start and the first restart a day later, nothing else."""
    n = 0
    with etag_rig(tmp_path) as r:
        r.wait_check()
        t0 = r.clock.t
        n += len(listing_headers(r))
    for k in range(1, 11):
        r = etag_rig(tmp_path)
        r.clock.t = t0 + k * 4 * 3600
        with r:
            r.wait_check()
            n += len(listing_headers(r))
    assert n == 2


def test_corrupt_cache_file_is_ignored_by_the_plugin(tmp_path):
    cache = tmp_path / "settings" / "cache" / "fw-releases.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{ truncated")
    with etag_rig(tmp_path) as r:
        fw = r.wait_check()
        assert fw["check"]["ok"] is True and r.gh.sent[0] == {} and "v1" in cache.read_text()


def test_rate_limit_retry_after_is_shown_and_honoured(rig):
    t0 = rig.clock.t = rig.clock.t + 24 * 3600
    rig.gh.routes[LIST] = LIMITED(**{"retry-after": "1800"})
    rig.core.firmware.tick()
    c = rig.fw["check"]
    assert c["ok"] is False and c["error_kind"] == "RateLimited" and "rate limit" in c["error"]
    assert c["retry_at"] == t0 + 1800 and "60 anonymous requests per hour" in c["limit_message"]
    assert c["next_at"] == t0 + 3600
    assert rig.fw["latest"]["version"] == "1.2.0"   # the known update stays visible
    n = n_listing(rig)
    rig.clock.t = t0 + 1799
    assert rig.core.check_firmware()["check"]["retry_at"] == t0 + 1800 and n_listing(rig) == n   # too early: no request
    rig.publish("1.2.0")
    rig.clock.t = t0 + 1800
    c = rig.core.check_firmware()["check"]
    assert n_listing(rig) == n + 1 and c["ok"] is True and c["retry_at"] is None and c["limit_message"] is None


def test_rate_limit_without_any_reset_time_waits_an_hour_and_a_silly_reset_is_capped(rig):
    t0 = rig.clock.t = rig.clock.t + 24 * 3600
    rig.gh.routes[LIST] = github.Response(429)
    rig.core.firmware.tick()
    assert rig.fw["check"]["retry_at"] is None and rig.fw["check"]["limit_message"]
    n = n_listing(rig)
    rig.clock.t = t0 + 3599
    rig.core.check_firmware()
    assert n_listing(rig) == n
    rig.clock.t = t0 + 3600
    rig.gh.routes[LIST] = LIMITED(**{"x-ratelimit-reset": str(rig.clock.t + 10 * 24 * 3600)})
    rig.core.check_firmware()
    assert rig.fw["check"]["retry_at"] == t0 + 3600 + FW.MAX_LIMIT_WAIT_S


def test_rate_limited_with_a_cache_keeps_working_from_the_cache_and_does_not_hit_the_api(tmp_path):
    with etag_rig(tmp_path) as r:
        r.wait_check()
        t0 = r.clock.t = r.clock.t + 24 * 3600
        r.gh.routes[LIST] = LIMITED(**{"retry-after": "600"})
        r.core.firmware.tick()
        c = r.fw["check"]
        assert c["ok"] is True and c["error"] is None and c["retry_at"] == t0 + 600
        assert "last known release" in c["limit_message"]
        assert r.fw["latest"]["version"] == "1.2.0" and r.fw["update_available"] is True
        assert c["next_at"] == t0 + 24 * 3600   # the daily cadence is unchanged
        n = n_listing(r)
        r.clock.t = t0 + 300
        r.core.check_firmware()
        assert n_listing(r) == n


def test_tampered_manifest_is_still_rejected_while_rate_limited(tmp_path):
    with etag_rig(tmp_path) as r:
        r.wait_check()
        r.clock.t += 24 * 3600
        r.gh.routes[LIST] = LIMITED(**{"retry-after": "600"})
        url = H.dl_url("fw-v1.2.0", "manifest.json")
        r.gh.routes[url] = r.gh.routes[url].replace(b"Faster", b"Slower")
        r.core.firmware.tick()
        assert r.fw["check"]["ok"] is False and r.fw["check"]["error_kind"] == "BadSignature"


def test_rate_limited_without_a_cache_uses_the_github_com_download_url(rig):
    # a stable plugin, no cache on disk yet (the rig's listing carried no ETag): releases/latest/download needs no API
    latest = f"https://github.com/{github.REPO}/releases/latest/download/"
    for n in ("manifest.json", "manifest.sig"):
        rig.gh.routes[latest + n] = rig.gh.routes[H.dl_url("fw-v1.2.0", n)]
    rig.gh.routes[LIST] = LIMITED(**{"retry-after": "600"})
    c = rig.check()["check"]
    assert c["ok"] is True and c["retry_at"] is not None and rig.fw["latest"]["version"] == "1.2.0"
    rig.clock.t += 24 * 3600
    rig.gh.routes[latest + "manifest.json"] = rig.gh.routes[latest + "manifest.json"].replace(b"Faster", b"Slower")
    assert rig.check()["check"]["error_kind"] == "BadSignature"


@pytest.mark.parametrize("route,kind", [
    (github.Response(404), "BadResponse"),
    (github.Response(502), "NetworkError"),
    (NetworkError("cannot reach GitHub: timed out"), "NetworkError"),
    (TooLarge("listing too big"), "TooLarge"),
])
def test_github_api_errors_become_a_readable_status(tmp_path, route, kind):
    with Rig(tmp_path) as r:
        r.wait_check()
        r.gh.routes[f"{github.API}/repos/{github.REPO}/releases?per_page=20"] = route
        fw = r.check()
        assert fw["check"]["ok"] is False and fw["check"]["error_kind"] == kind and fw["check"]["error"]
        assert r.core.install_firmware("9.9.9")["ok"] is False


def test_release_assets_missing_or_foreign_urls(tmp_path):
    with Rig(tmp_path, publish=None) as r:
        r.publish("1.2.0", drop=("manifest.sig",))
        fw = r.core.check_firmware()
        assert fw["check"]["error_kind"] == "BadResponse" and "missing manifest.sig" in fw["check"]["error"]
        r.publish("1.2.0", urls={"manifest.json": "https://evil.example/manifest.json"})
        assert "unexpected download URL" in r.core.check_firmware()["check"]["error"]


def test_no_releases_published_is_not_an_error(tmp_path):
    with Rig(tmp_path, publish=None) as r:
        r.gh.listing([])
        fw = r.core.check_firmware()
        assert fw["check"]["ok"] is True and fw["latest"] is None and fw["update_available"] is False


def test_plugin_that_cannot_tell_its_own_version_reports_it(tmp_path):
    with Rig(tmp_path, version="unknown") as r:
        fw = r.wait_check()
        assert fw["check"]["ok"] is False and "cannot compare versions" in fw["check"]["error"]


def test_release_that_needs_a_newer_plugin_or_protocol(tmp_path):
    with Rig(tmp_path, version="0.0.1", publish=None) as r:
        r.publish("1.2.0", doc=H.manifest_doc(IMAGE, "1.2.0", min_plugin_version="9.0.0"))
        c = r.core.check_firmware()["check"]
        assert c["error_kind"] == "PluginTooOld" and "needs plugin >= 9.0.0" in c["error"]
        r.publish("1.2.0", doc=H.manifest_doc(IMAGE, "1.2.0", protocol_version=2))
        c = r.core.check_firmware()["check"]
        assert c["error_kind"] == "ProtocolMismatch"


def test_manual_check_while_one_runs_and_checks_are_skipped_during_an_install(rig):
    gate = threading.Event()
    real = rig.gh

    def slow(url, max_bytes, headers=None):
        gate.wait(5)
        return real(url, max_bytes, headers)

    rig.core.firmware._fetch = slow
    rig.clock.t += 61
    t = threading.Thread(target=rig.core.check_firmware)
    t.start()
    rig.until(lambda: rig.fw["check"]["checking"])
    assert rig.core.check_firmware()["check"]["checking"] is True   # joined the running check, no second request
    gate.set()
    t.join(5)
    assert not t.is_alive()
    rig.core.firmware._fetch = real
    rig.on_data = (2, lambda link: results.append(rig.core.firmware.tick()))
    results = []
    rig.clock.t += 48 * 3600
    rig.core.install_firmware("1.2.0")
    rig.wait_install()
    assert results == [False]


# ================================================================================ HELLO protocol mismatch
class OldProtoPanel(EmulatedPanel):
    """Firmware from before protocol v1: HELLO says v0 until an OTA image replaces it."""

    proto = P.PROTO_VER + 7

    def _hello(self):
        h = P.Hello(self.proto, P.MAX_PAYLOAD, self.rx_window, P.WIDTH, P.HEIGHT, self.caps, self.idle_free, self.fw_ver)
        return self._send(P.T_HELLO, P.hello_payload(h))

    def reboot(self):
        if self.ota_committed:
            self.proto = P.PROTO_VER
        super().reboot()


def test_protocol_mismatch_points_to_the_firmware_update_and_the_update_fixes_it(tmp_path):
    panel = OldProtoPanel(fw_ver="0.1.0", ota_slot=H.SLOT)
    with Rig(tmp_path, panel=panel) as r:
        r.wait_check()
        r.until(lambda: r.core.get_status()["ui_state"] == "firmware_too_old", what="engine refusing the old firmware")
        fw = r.fw
        assert fw["protocol_mismatch"] is True and f"protocol v{P.PROTO_VER + 7}" in fw["mismatch_message"]
        assert "Install the latest firmware" in fw["mismatch_message"]
        assert fw["installed"]["version"] == "0.1.0" and fw["installed"]["connected"] is False and fw["update_available"] is True
        assert r.core.install_firmware("1.2.0")["ok"]
        assert r.wait_install()["state"] == "done" and panel.fw_ver == "1.2.0"
        r.until(lambda: r.core.get_status()["ui_state"] == "connected", what="engine on the new firmware")
        assert r.fw["protocol_mismatch"] is False


def test_status_before_the_core_runs_raises_like_every_other_read(tmp_path):
    r = Rig(tmp_path)
    for call in (r.core.get_firmware_status, r.core.check_firmware, r.core.cancel_firmware_install):
        with pytest.raises(RuntimeError, match="not running"):
            call()
    with pytest.raises(RuntimeError, match="not running"):
        r.core.install_firmware("1.2.0")


def test_event_callback_errors_never_break_an_install(tmp_path):
    def boom(status):
        raise RuntimeError("ui gone")

    with Rig(tmp_path) as r:
        r.core.firmware._emit = boom
        r.wait_check()
        r.wait_connected("1.0.0")
        r.core.install_firmware("1.2.0")
        assert r.wait_install()["state"] == "done"


def test_start_failure_stops_the_updater(tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise ValueError("cannot build")

    r = Rig(tmp_path)
    r.core._fw_args = (Boom,) + r.core._fw_args[1:]
    with pytest.raises(ValueError):
        r.core.start()
    assert r.core.engine is None and r.core.firmware is None


def test_start_failure_after_the_updater_exists_stops_it(tmp_path):
    stopped = []

    class Bad:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise ValueError("thread limit")

        def stop(self):
            stopped.append(1)

    r = Rig(tmp_path)
    r.core._fw_args = (Bad,) + r.core._fw_args[1:]
    with pytest.raises(ValueError, match="thread limit"):
        r.core.start()
    assert stopped == [1] and r.core.engine is None and r.core.firmware is None


def test_status_note_when_the_cached_release_no_longer_fits_the_plugin(rig):
    rig.core.firmware.plugin_version = "0.0.1"   # e.g. a downgraded plugin: min_plugin_version 1.0.0 is now too new
    fw = rig.fw
    assert fw["update_available"] is False and "needs plugin >= 1.0.0" in fw["note"]

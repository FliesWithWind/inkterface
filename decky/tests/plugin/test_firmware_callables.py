"""main.py: the four firmware callables, the `firmware` event, and the Decky-side wiring (emulated OTA panel)."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ota"))
import ota_helpers as H  # noqa: E402

from inkterface_core import proto as P  # noqa: E402
from inkterface_core.emulator import EmulatedPanel  # noqa: E402
from inkterface_core.engine import EmulatedLink  # noqa: E402

IMAGE = H.make_image(30_000)


@pytest.fixture
def fwh(h):
    """The plugin harness with an OTA-capable panel, a fake GitHub signed with the TEST-ONLY key and fast timers."""
    h.panel = EmulatedPanel(fw_ver="1.0.0", ota_slot=H.SLOT)
    h.gh = H.FakeGitHub()
    h.gh.listing([h.gh.publish("1.2.0", IMAGE)])

    def connect():
        if h.panel.ota_committed:
            h.panel.reboot()
        return h._connect()

    h.mod.connect = connect
    h.mod.firmware_kw = dict(fw_fetch=h.gh, public_key=lambda: H.PUB, sleep=lambda s: None, handshake_timeout=0.5,
                             confirm_timeout=3.0, tick_s=3600.0)
    h.mod._version = lambda: "1.0.0"
    return h


def fw_events(h):
    return [a[0] for e, a in h.events if e == "firmware"]


def test_callables_roundtrip_and_emit_firmware_events(fwh):
    async def body(p):
        await fwh.until(lambda: p.core.firmware.status()["check"]["at"] is not None)
        await fwh.until(lambda: p.core.get_firmware_status()["installed"]["version"] == "1.0.0")
        st = await p.get_firmware_status()
        json.dumps(st)
        assert st["update_available"] and st["latest"]["version"] == "1.2.0" and st["trust"]["state"] == "ok"
        assert (await p.check_firmware())["check"]["ok"] is True
        assert await p.install_firmware("1.2.0") == {"ok": True}
        await fwh.until(lambda: p.core.get_firmware_status()["install"]["state"] == "done")
        await fwh.until(lambda: fw_events(fwh) and fw_events(fwh)[-1]["install"]["state"] == "done")
        states = []
        for e in fw_events(fwh):
            if not states or states[-1] != e["install"]["state"]:
                states.append(e["install"]["state"])
        assert states[-2:] == ["confirming", "done"] and "sending" in states
        assert fwh.panel.installed == IMAGE and hashlib.sha256(IMAGE).hexdigest() == hashlib.sha256(fwh.panel.installed).hexdigest()
        # the engine is back: `status` reports the new firmware again
        await fwh.until(lambda: p.core.get_status()["fw_version"] == "1.2.0" and p.core.get_status()["connected"])
    fwh.run(body)


def test_install_refusal_and_cancel_callables_return_plain_dicts(fwh):
    async def body(p):
        assert (await p.install_firmware("0.0.1"))["ok"] is False
        assert await p.cancel_firmware_install() == {"ok": False, "error": "no firmware update is running"}
        st = await p.get_status()
        assert st["ui_state"] in ("connected", "connecting")
    fwh.run(body)


def test_status_shows_updating_while_the_engine_is_paused(fwh):
    async def body(p):
        await fwh.until(lambda: p.core.get_firmware_status()["latest"] is not None)
        p.core.engine.pause()
        st = await p.get_status()
        assert st["ui_state"] == "updating" and st["state"] == "paused"
        p.core.engine.resume()
    fwh.run(body)


def test_callables_raise_when_the_plugin_is_not_running(fwh):
    async def body():
        p = fwh.mod.Plugin()
        for call in (p.get_firmware_status(), p.check_firmware(), p.install_firmware("1.2.0"), p.cancel_firmware_install()):
            with pytest.raises(RuntimeError, match="not running"):
                await call
    asyncio.run(body())


def test_event_bridge_survives_a_closed_loop(fwh):
    """The updater's thread may report after the loop is gone (unload): that must not raise."""
    async def body(p):
        return p.core.firmware._emit
    emit = fwh.run(body)
    emit({"install": {"state": "idle"}})   # loop closed by now

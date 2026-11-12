"""Shared rig for the engine tests: fake clock, controllable data, tiny screens, emulated panel."""
import json
from pathlib import Path

from inkterface_core import proto as P
from inkterface_core.emulator import EmulatedPanel
from inkterface_core.engine import Engine, EmulatedLink, Settings, load_screens
from inkterface_core.screens import render_frame

INVERT = bytes(b ^ 0xFF for b in range(256))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Data:
    """Registry stand-in: the test mutates `.d`, the engine reads snapshot()."""

    def __init__(self, **d):
        self.d = {"system.cpu.load": 10.0, "system.gpu.load": 20.0, **d}
        self.np = []
        self.fail = None
        self.listeners = []
        self.watched = []   # every watch() call, in order

    def subscribe(self, cb):
        self.listeners.append(cb)

    def unsubscribe(self, cb):
        self.listeners.remove(cb)

    def watch(self, keys):
        self.watched.append(keys)

    def change(self, **kv):
        """A source reported new values: update the data and tell the subscribers, like Registry does."""
        self.d.update({k.replace("__", "."): v for k, v in kv.items()})
        for cb in list(self.listeners):
            cb()

    def snapshot(self):
        if self.fail:
            raise self.fail
        return dict(self.d)

    def set_now_playing(self, appid):
        if appid == "bad":
            raise ValueError("bad appid")
        self.np.append(appid)


def write_screen(root: Path, sid: str, text: str, refresh_s=1.0, settings=None, active=True):
    """Two small text boxes far apart: a data change in one yields one small rect."""
    doc = {"schema": 1, "id": sid, "name": sid, "version": "1.0.0", "author": "t"}
    sec = {"refresh_s": refresh_s, "widgets": [
        {"type": "text", "x": 8, "y": 8, "w": 200, "h": 40, "text": text, "font": "bold-32"},
        {"type": "text", "x": 400, "y": 400, "w": 200, "h": 40, "text": "{system.gpu.load:.0f}", "font": "bold-32"}]}
    doc["active" if active else "idle"] = sec if active else {"render_mode": "mono", "widgets": []}
    if settings:
        doc["settings"] = settings
    d = root / sid
    d.mkdir(parents=True)
    (d / "screen.json").write_text(json.dumps(doc))


def make_screens(root: Path):
    write_screen(root, "a", "A {system.cpu.load:.0f}")
    write_screen(root, "b", "B {system.cpu.load:.0f}")
    write_screen(root, "now-playing", "NP {system.cpu.load:.0f}", refresh_s=5.0)
    screens, errs = load_screens(root)
    assert not errs, errs
    return screens


class Rig:
    """Engine + fake clock + emulated panel. `links` collects every link the engine opened."""

    def __init__(self, tmp_path, settings=None, partial_s=0.0, full_s=0.0, panel=None, **engine_kw):
        self.clock = Clock()
        self.data = Data()
        self.screens = make_screens(tmp_path / "screens")
        self.settings = settings or Settings(rotation=["a", "b"], rotation_interval_s=30)
        self.panel = panel or EmulatedPanel()
        self.partial_s, self.full_s = partial_s, full_s
        self.links = []
        self.plugged = True
        self.connect_times = []
        self.engine = Engine(self.data, self.screens, self.settings, connect=self._connect,
                             clock=self.clock, wall=lambda: 5000.0 + self.clock.t, **engine_kw)

    def _connect(self):
        self.connect_times.append(self.clock.t)
        if not self.plugged:
            raise FileNotFoundError("no panel found (USB VID 303a)")
        link = EmulatedLink(self.panel, self.clock, self.partial_s, self.full_s)
        self.links.append(link)
        return link

    @property
    def link(self):
        return self.links[-1]

    def run(self, seconds, dt=0.05):
        for _ in range(round(seconds / dt)):
            self.engine.step()
            self.clock.t += dt

    def expected(self, sid, settings=None):
        """The wire-polarity frame the panel must show, rendered independently of the engine."""
        return render_frame(self.screens[sid], self.data.snapshot(), settings).translate(INVERT)

    def frames(self, *types):
        return [f for f in self.link.written if f.type in types]

    def wire_bytes(self, *types):
        return sum(len(P.encode(f.type, f.seq, f.payload)) for f in self.frames(*types))

    @property
    def st(self):
        return self.engine.status()

"""Which active screen is on the panel right now. Pure logic, clock passed in. See docs/engine.md.

Two profiles: "normal" (rotation / rotation_interval_s / pinned) and, while `ingame_enabled` is on and a game runs,
"ingame" (settings.ingame). Within the active profile: pinned screen > now-playing (game running, auto-switch on, and
only without the in-game profile) > rotation.
While something outranks the rotation its clock is held, so when the game exits or the pin is
removed the rotation resumes on the same screen with the same time left. A profile switch starts the new rotation at
its first screen, and at most one switch happens per SWITCH_DEBOUNCE_S, so a game that flaps does not flicker the panel.
"""
from __future__ import annotations

from .settings import INGAME, NORMAL, Settings

SWITCH_DEBOUNCE_S = 3.0


class Scheduler:
    def __init__(self, settings: Settings, available=()):
        self.settings = settings
        self.available: set = set(available)
        self.now_playing = None  # appid, or None
        self.profile = NORMAL
        self._switched_at: float | None = None   # clock of the last profile switch
        self._reset()

    def _reset(self) -> None:
        self._idx = 0
        self._since: float | None = None  # when the current rotation slot began
        self._held_at: float | None = None  # set while pin/now-playing outranks the rotation
        self._rot_id: str | None = None

    def set_available(self, ids) -> None:
        self.available = set(ids)

    def update(self, settings: Settings, now: float) -> None:
        """New settings: keep the current rotation screen if it is still in the list, else restart."""
        self.settings = settings
        rot = self._rotation()
        self._idx = rot.index(self._rot_id) if self._rot_id in rot else 0
        self._since = now if self._held_at is None else None
        if self._held_at is not None:
            self._held_at = now

    def set_now_playing(self, appid) -> None:
        """appid of the running game, or None/0 when it exits."""
        self.now_playing = appid or None

    def _wanted(self) -> str:
        return INGAME if self.settings.ingame_enabled and self.now_playing is not None else NORMAL

    def switch_pending(self, now: float) -> bool:
        """A profile switch is wanted and allowed now (the debounce is over): the caller should render."""
        if self._wanted() == self.profile:
            return False
        t = self._switched_at
        return t is None or now - t >= SWITCH_DEBOUNCE_S or now < t   # a clock that went backwards does not block it

    def select(self, now: float) -> str:
        """Switch the active profile if one is pending; returns it."""
        if self.switch_pending(now):
            self.profile, self._switched_at = self._wanted(), now
            self._reset()
        return self.profile

    def _active(self) -> dict:
        return self.settings.profile(self.profile)

    def _rotation(self) -> list:
        rot = [s for s in self._active()["rotation"] if s in self.available]
        return rot or sorted(self.available)[:1]  # nothing usable listed: show any screen rather than none

    def current(self, now: float) -> str | None:
        self.select(now)
        s, p = self.settings, self._active()
        if p["pinned"] in self.available:
            return self._hold(now, p["pinned"])
        if (self.now_playing is not None and not s.ingame_enabled and s.auto_now_playing
                and s.now_playing_screen in self.available):
            return self._hold(now, s.now_playing_screen)
        rot = self._rotation()
        if not rot:
            return self._hold(now, None)
        if self._held_at is not None:  # resume: the time spent held does not count
            if self._since is not None:
                self._since += now - self._held_at
            self._held_at = None
        if self._since is None:
            self._since = now
        interval = p["rotation_interval_s"]
        n = int((now - self._since) // interval)
        if n > 0:
            self._idx += n
            self._since += n * interval
        self._idx %= len(rot)
        self._rot_id = rot[self._idx]
        return self._rot_id

    def _hold(self, now: float, screen: str | None) -> str | None:
        if self._held_at is None:
            self._held_at = now
        return screen

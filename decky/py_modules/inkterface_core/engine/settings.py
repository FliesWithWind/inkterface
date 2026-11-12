"""Engine settings: one JSON file, schema-validated, atomic writes. Stdlib only. See docs/engine.md."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field

SCHEMA = 1
ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")

# key -> (min, max) for the numeric keys. bool is rejected for all of them.
LIMITS = {
    "rotation_interval_s": (5, 86400),
    "idle_timeout_s": (15, 3600),  # MODE idle_timeout_s range; the panel NAKs anything else
    "ghost_clear_minutes": (0, 120),  # minutes between full refreshes that clear ghosting; 0 = never
}


def _is_id(v) -> bool:
    return isinstance(v, str) and 0 < len(v) <= 64 and set(v) <= ID_CHARS


PROFILE_KEYS = ("rotation", "rotation_interval_s", "pinned")   # what the "In a game" profile has of its own
NORMAL, INGAME = "normal", "ingame"


def _profile_errors(d: dict, where: str = "") -> list[str]:
    """Problems in the rotation / interval / pin of one profile (the top level, or the `ingame` object)."""
    errs = []
    r = d.get("rotation", [])
    if not isinstance(r, list) or not all(_is_id(i) for i in r):
        errs.append(f"{where}rotation: must be a list of screen ids ([a-z0-9-])")
    elif len(set(r)) != len(r):
        errs.append(f"{where}rotation: duplicate screen id")
    lo, hi = LIMITS["rotation_interval_s"]
    v = d.get("rotation_interval_s", lo)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        errs.append(f"{where}rotation_interval_s: must be an integer from {lo} to {hi}")
    p = d.get("pinned")
    if p is not None and not _is_id(p):
        errs.append(f"{where}pinned: must be null or a screen id")
    return errs


@dataclass
class Settings:
    rotation: list = field(default_factory=lambda: ["telemetry", "thermals-power", "big-clock"])
    rotation_interval_s: int = 30
    pinned: str | None = None  # a pinned screen beats rotation and now-playing
    auto_now_playing: bool = True
    now_playing_screen: str = "now-playing"
    idle_timeout_s: int = 60  # silence before the panel shows its idle layout; sent in MODE (15..3600)
    ghost_clear_minutes: int = 10  # at least this long since the last full refresh before ghosting is cleared (0 = never)
    screen_settings: dict = field(default_factory=dict)  # screen id -> {setting key: value}
    # The "In a game" profile: used instead of rotation / rotation_interval_s / pinned while a game runs and ingame_enabled
    # is on. None until first enabled (then a copy of the normal profile). Kept when switched off, so on again restores it.
    ingame_enabled: bool = False
    ingame: dict | None = None   # {"rotation": [...], "rotation_interval_s": int, "pinned": id | None}

    def profile(self, name: str) -> dict:
        """rotation / rotation_interval_s / pinned of one profile. The in-game one falls back to the normal one until it exists."""
        if name == INGAME and self.ingame is not None:
            return self.ingame
        return {"rotation": self.rotation, "rotation_interval_s": self.rotation_interval_s, "pinned": self.pinned}

    @staticmethod
    def validate(data) -> list[str]:
        """Every problem in a settings dict (as parsed from JSON); empty list = valid."""
        if not isinstance(data, dict):
            return [f"top level must be an object, got {type(data).__name__}"]
        errs = []
        allowed = set(Settings.__dataclass_fields__) | {"schema"}
        for k in sorted(set(data) - allowed):
            errs.append(f"{k}: unknown setting")
        if "schema" in data and data["schema"] != SCHEMA:
            errs.append(f"schema: must be {SCHEMA}")
        errs += _profile_errors(data)
        for k, (lo, hi) in LIMITS.items():
            if k != "rotation_interval_s" and k in data and (not isinstance(data[k], int) or isinstance(data[k], bool) or not lo <= data[k] <= hi):
                errs.append(f"{k}: must be an integer from {lo} to {hi}")
        if not isinstance(data.get("ingame_enabled", False), bool):
            errs.append("ingame_enabled: must be true or false")
        ig = data.get("ingame")
        if ig is not None:
            if not isinstance(ig, dict) or set(ig) != set(PROFILE_KEYS):
                errs.append(f"ingame: must be null or an object with exactly {', '.join(PROFILE_KEYS)}")
            else:
                errs += _profile_errors(ig, "ingame.")
        if not isinstance(data.get("auto_now_playing", True), bool):
            errs.append("auto_now_playing: must be true or false")
        if "now_playing_screen" in data and not _is_id(data["now_playing_screen"]):
            errs.append("now_playing_screen: must be a screen id")
        ss = data.get("screen_settings", {})
        if not isinstance(ss, dict):
            errs.append("screen_settings: must be an object")
        else:
            for sid, vals in ss.items():
                if not _is_id(sid) or not isinstance(vals, dict):
                    errs.append(f"screen_settings.{sid}: must be an object keyed by a screen id")
        return errs

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        errs = cls.validate(data)
        if errs:
            raise ValueError("; ".join(errs))
        return cls(**{k: v for k, v in data.items() if k != "schema"})

    def to_dict(self) -> dict:
        d = {"schema": SCHEMA, **json.loads(json.dumps(asdict(self)))}
        if not self.ingame_enabled and self.ingame is None:   # never used: the file stays as it was (and an older plugin still reads it)
            del d["ingame_enabled"], d["ingame"]
        return d

    @classmethod
    def load(cls, path: str | os.PathLike) -> tuple["Settings", str | None]:
        """(settings, error). Missing file = defaults, no error. Unreadable/invalid file = defaults + error,
        and the bad file is moved to `<path>.corrupt` so the next save cannot destroy a hand-edited file."""
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return cls(), None
        except OSError as e:
            return cls(), f"cannot read {path}: {e}"
        try:
            data = json.loads(raw.decode("utf-8"))
            return cls.from_dict(data), None
        except (ValueError, UnicodeDecodeError) as e:  # JSONDecodeError and our validation error are ValueErrors
            err = f"{path}: {e}; using defaults"
            try:
                os.replace(path, str(path) + ".corrupt")
            except OSError:
                pass
            return cls(), err

    def save(self, path: str | os.PathLike) -> None:
        """Atomic: write a temp file in the same directory, fsync, rename over the target."""
        path = os.fspath(path)
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

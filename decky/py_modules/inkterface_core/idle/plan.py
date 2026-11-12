"""What the plugin wants on the panel's idle store: user's idle screens -> one ISTR blob (docs/idle-mode.md).

`build_plan` never raises: a screen that does not compile, an oversize store or a bad config value becomes
`plan.error` (text without any secret) and `plan.blob` is None, so the engine refuses before sending a byte.
The blob carries the Wi-Fi password and Steam key, so it stays out of repr() and is never put in a status.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .compile import LayoutError, compile_layout
from .store import MAX_ROTATE_S, MIN_ROTATE_S, StoreError, pack_store

MODES = ("mono", "gray4")


@dataclass(frozen=True)
class IdlePlan:
    blob: bytes | None = field(default=None, repr=False)
    ids: tuple = ()                 # screen ids in store order (store layout i = ids[i])
    modes: tuple = ()               # render mode of each, parallel to ids
    error: str | None = None        # why there is no blob (nothing is uploaded)
    needs_gray4: bool = False

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.blob).hexdigest()[:12] if self.blob else ""

    @property
    def size(self) -> int:
        return len(self.blob) if self.blob else 0


def supported_modes(idle: dict, assets_dir) -> list[str]:
    """Render modes this screen's idle section compiles in (the panel can show any compilable layout either way)."""
    out = []
    for m in MODES:
        try:
            compile_layout({**idle, "render_mode": m}, assets_dir=str(assets_dir))
            out.append(m)
        except LayoutError:
            pass
    return out


def _coord(v) -> str:
    return f"{v:.5f}".rstrip("0").rstrip(".")


def build_plan(screens: list, *, modes: dict, rotate_s: int, tz: str, lat=None, lon=None,
               units: str = "metric", steam_key: str = "", steam_id: str = "", wifi_ssid: str = "",
               wifi_pass: str = "") -> IdlePlan:
    """screens: [(id, Screen)] in rotation order (first = shown first). modes: {id: 'mono'|'gray4'} overrides.
    rotate_s 0 = no rotation (the first layout stays), else MIN_ROTATE_S..MAX_ROTATE_S seconds per layout."""
    ids = tuple(i for i, _ in screens)
    if not screens:
        return IdlePlan()
    blobs, used = [], []
    problems = []
    for sid, scr in screens:
        mode = modes.get(sid) or scr.idle.get("render_mode", "mono")
        try:
            blobs.append(compile_layout({**scr.idle, "render_mode": mode}, assets_dir=str(scr.assets_dir)))
            used.append(mode)
        except LayoutError as e:
            problems.append(f"{sid}: {e.problems[0]}" + (f" (+{len(e.problems) - 1} more)" if len(e.problems) > 1 else ""))
    if problems:
        return IdlePlan(ids=ids, error="; ".join(problems))
    cfg = {"tz": tz, "units": 1 if units == "imperial" else 0}
    if lat is not None and lon is not None:
        cfg["lat"], cfg["lon"] = _coord(lat), _coord(lon)
    for k, v in (("steam_key", steam_key), ("steam_id", steam_id), ("wifi_ssid", wifi_ssid), ("wifi_pass", wifi_pass)):
        if v:
            cfg[k] = v
    rotate = rotate_s > 0 and len(blobs) > 1
    try:
        blob = pack_store(blobs, active=0, rotate=rotate, rotate_s=max(rotate_s, MIN_ROTATE_S) if rotate else 60,
                          config=cfg)
    except StoreError as e:
        return IdlePlan(ids=ids, error=f"idle store: {e}")
    return IdlePlan(blob=blob, ids=ids, modes=tuple(used), needs_gray4="gray4" in used)


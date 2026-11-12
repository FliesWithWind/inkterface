"""Shared builders for the idle layout tests."""
from __future__ import annotations

from inkterface_core.idle.compile import compile_layout


def layout(widgets, *, mode="mono", sources=None, **kw):
    idle = {"render_mode": mode, "widgets": widgets, "sources": sources or {}}
    idle.update(kw)
    return idle


def compile_ok(widgets, *, mode="mono", sources=None, assets_dir=None, **kw):
    return compile_layout(layout(widgets, mode=mode, sources=sources, **kw),
                          assets_dir=assets_dir)


WEATHER_SRC = {"wx": {"type": "weather", "units": "metric", "location": "52.1,4.3"}}
CLOCK_SRC = {"time": {"type": "clock"}}
STEAM_SRC = {"st": {"type": "steam", "appid": "570"}}
HTTP_SRC = {"api": {"type": "http_json", "url": "https://example.com/d.json", "pointer": "/a/b"}}

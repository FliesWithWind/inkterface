"""screen.json loader and validator. Collects every problem, each prefixed with its JSON path."""
from __future__ import annotations

import difflib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from . import data as D
from .fonts import parse_font
from .widgets import COMMON, GEOMETRY, MAX_ASSET_BYTES, MAX_IMAGE_PIXELS, WIDGETS, Opt

WIDTH, HEIGHT = 648, 480
SCHEMA_VERSION = 1
MAX_JSON_BYTES = 256 * 1024
MAX_WIDGETS = 200
MAX_CODE_BYTES = 256 * 1024
ID_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?")
KEY_RE = re.compile(r"[a-z][a-z0-9_]*")

TOP_KEYS = {"schema", "id", "name", "version", "author", "description", "active", "idle", "settings"}
ACTIVE_KEYS = {"size", "refresh_s", "code", "widgets", "triggers"}
IDLE_KEYS = {"render_mode", "refresh_interval_s", "full_refresh_every", "sources", "widgets"}
IDLE_WIDGETS = {"text", "number", "bar", "icon", "image", "clock"}
IDLE_SOURCE_TYPES = {"clock", "weather", "steam", "http_json"}
SETTING_KEYS = {"key", "type", "label", "default", "min", "max", "values"}


class ScreenError(Exception):
    """Invalid package. `.errors` holds one message per problem."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("\n".join(self.errors))


@dataclass
class Screen:
    root: Path
    id: str
    name: str
    version: str
    author: str
    description: str = ""
    active: dict | None = None
    idle: dict | None = None
    settings: list[dict] = field(default_factory=list)

    @property
    def refresh_s(self) -> float:
        return float((self.active or {}).get("refresh_s", 1.0))

    def bound_paths(self) -> frozenset | None:
        """Snapshot keys the active widgets read (`bind`, `{template}` refs, a clock's time.now/time.tz, the now-playing widget).

        None for a code screen: what its script reads is unknown, so every change counts."""
        if self.code_path is not None:
            return None
        out: set[str] = set()
        for w in (self.active or {}).get("widgets") or ():
            if w.get("type") == "clock" and not w.get("bind"):
                out |= {"time.now", "time.tz"}
            elif w.get("type") == "now-playing":   # reads steam.now_playing.* without a bind
                out.add("steam.now_playing")
            for v in w.values():
                if isinstance(v, str) and "{" in v:
                    try:
                        out |= {p[0] for p in D.parse_template(v) if not isinstance(p, str)}
                    except ValueError:
                        pass
            if isinstance(w.get("bind"), str):
                out.add(w["bind"])
        return frozenset(p for p in out if not p.startswith("settings."))

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def code_path(self) -> Path | None:
        """screen.py of the code tier. Declared and validated only; this engine never runs it."""
        code = (self.active or {}).get("code")
        return self.root / code if code else None

    def resolve_settings(self, overrides: Mapping | None = None) -> dict:
        return resolve_settings(self.settings, overrides)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _type_name(v) -> str:
    return "null" if v is None else type(v).__name__


def _check_setting_value(s: Mapping, v: Any) -> str | None:
    """Error text if `v` is not valid for setting schema `s`."""
    t = s["type"]
    if t == "bool":
        return None if isinstance(v, bool) else f"must be bool, got {_type_name(v)}"
    if t == "string":
        return None if isinstance(v, str) else f"must be string, got {_type_name(v)}"
    if t == "enum":
        return None if v in s["values"] and isinstance(v, str) else f"must be one of {s['values']}"
    if not _is_num(v):
        return f"must be a number, got {_type_name(v)}"
    if "min" in s and v < s["min"]:
        return f"must be >= {s['min']}"
    if "max" in s and v > s["max"]:
        return f"must be <= {s['max']}"
    return None


def resolve_settings(schema: list[dict], overrides: Mapping | None = None) -> dict:
    """Defaults overlaid with user values. ScreenError for unknown keys or invalid values."""
    out = {s["key"]: s["default"] for s in schema}
    errs = []
    for k, v in (overrides or {}).items():
        s = next((s for s in schema if s["key"] == k), None)
        if s is None:
            errs.append(f"settings.{k}: unknown setting")
        elif (msg := _check_setting_value(s, v)):
            errs.append(f"settings.{k}: {msg}")
        else:
            out[k] = v
    if errs:
        raise ScreenError(errs)
    return out


class _Validator:
    def __init__(self, root: Path | None):
        self.root = root
        self.errors: list[str] = []
        self.setting_kinds: dict[str, str] = {}

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    # -- generic -------------------------------------------------------------------------
    def obj(self, v, path, allowed: set[str], required: set[str] = frozenset()) -> bool:
        if not isinstance(v, dict):
            self.err(path, f"must be an object, got {_type_name(v)}")
            return False
        for k in v:
            if k not in allowed:
                hint = difflib.get_close_matches(k, allowed, n=1)
                self.err(f"{path}.{k}" if path else k, "unknown key" + (f" (did you mean '{hint[0]}'?)" if hint else ""))
        for k in sorted(required - v.keys()):
            self.err(f"{path}.{k}" if path else k, "required")
        return True

    def string(self, v, path, maxlen=200, pattern: re.Pattern | None = None, what="") -> bool:
        if not isinstance(v, str) or not v.strip():
            self.err(path, "must be a non-empty string")
        elif len(v) > maxlen:
            self.err(path, f"too long ({len(v)} > {maxlen})")
        elif pattern and not pattern.fullmatch(v):
            self.err(path, f"invalid {what or 'value'} '{v}'")
        else:
            return True
        return False

    # -- bindings --------------------------------------------------------------------------
    def source(self, ref: str, path: str, kinds: frozenset[str] | set[str], what: str) -> None:
        kind = D.source_kind(ref, self.setting_kinds)
        if kind is None:
            pool = list(D.SOURCES) + [f"settings.{k}" for k in self.setting_kinds]
            hint = difflib.get_close_matches(ref, pool, n=1)
            self.err(path, f"unknown source '{ref}'" + (f" (did you mean '{hint[0]}'?)" if hint else ""))
        elif kind not in kinds:
            self.err(path, f"source '{ref}' is {kind}; {what} needs {' or '.join(sorted(kinds))}")

    def template(self, s: str, path: str) -> None:
        try:
            parts = D.parse_template(s)
        except ValueError as e:
            return self.err(path, f"bad template: {e}")
        for p in parts:
            if isinstance(p, str):
                continue
            ref, spec = p
            self.source(ref, path, {D.NUMBER, D.STRING, D.BOOL, D.TIME}, "a template")
            if spec and not _spec_ok(spec):
                self.err(path, f"bad format spec ':{spec}' for '{ref}'")

    # -- assets ----------------------------------------------------------------------------
    def asset(self, v, path: str) -> None:
        if not self.string(v, path, 200):
            return
        if "\x00" in v or "\\" in v or os.path.isabs(v) or ".." in Path(v).parts:
            return self.err(path, f"asset path '{v}' must be relative to assets/ without '..'")
        if self.root is None:
            return
        base = (self.root / "assets").resolve()
        target = (base / v).resolve()
        if base != target and base not in target.parents:
            return self.err(path, f"asset path '{v}' escapes the package")
        if not target.is_file():
            return self.err(path, f"asset '{v}' not found in assets/")
        size = target.stat().st_size
        if size > MAX_ASSET_BYTES:
            return self.err(path, f"asset '{v}' too large ({size} bytes, max {MAX_ASSET_BYTES})")
        try:
            with Image.open(target) as im:
                if im.width * im.height > MAX_IMAGE_PIXELS:
                    self.err(path, f"asset '{v}' has too many pixels ({im.width}x{im.height})")
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            self.err(path, f"asset '{v}' is not a readable image")

    # -- options -----------------------------------------------------------------------------
    def option(self, v, o: Opt, path: str) -> None:
        k = o.kind
        if k == "bool":
            if not isinstance(v, bool):
                self.err(path, f"must be bool, got {_type_name(v)}")
        elif k in ("int", "num"):
            if not (_is_int(v) if k == "int" else _is_num(v)):
                return self.err(path, f"must be {'an integer' if k == 'int' else 'a number'}, got {_type_name(v)}")
            if o.min is not None and v < o.min or o.max is not None and v > o.max:
                self.err(path, f"must be between {o.min} and {o.max}")
        elif k == "enum":
            if v not in o.values or not isinstance(v, str):
                self.err(path, f"must be one of {list(o.values)}")
        elif k == "font":
            try:
                parse_font(v)
            except ValueError as e:
                self.err(path, str(e))
        elif k == "range":
            if not (isinstance(v, list) and len(v) == 2 and all(_is_num(n) for n in v)):
                self.err(path, "must be [min, max] numbers")
            elif v[0] >= v[1]:
                self.err(path, "min must be less than max")
        elif k == "asset":
            self.asset(v, path)
        elif k == "str":
            if not isinstance(v, str):
                self.err(path, f"must be string, got {_type_name(v)}")
            elif len(v) > 500:
                self.err(path, "too long (max 500)")
            elif o.templated:
                self.template(v, path)

    # -- widgets ---------------------------------------------------------------------------
    def geometry(self, w: dict, path: str, bounds: tuple[int, int]) -> None:
        vals = {}
        for k in GEOMETRY:
            if k not in w:
                continue
            if not _is_int(w[k]):
                self.err(f"{path}.{k}", f"must be an integer, got {_type_name(w[k])}")
            else:
                vals[k] = w[k]
        for k in ("x", "y"):
            if k in vals and vals[k] < 0:
                self.err(f"{path}.{k}", "must be >= 0")
        for k in ("w", "h"):
            if k in vals and vals[k] < 1:
                self.err(f"{path}.{k}", "must be >= 1")
        for pos, size, limit in (("x", "w", bounds[0]), ("y", "h", bounds[1])):
            if pos in vals and size in vals and vals[pos] >= 0 and vals[pos] + vals[size] > limit:
                self.err(f"{path}.{pos}", f"widget extends past the {limit}px edge ({pos}+{size} = {vals[pos] + vals[size]})")

    def widget(self, w, path: str) -> None:
        if not isinstance(w, dict):
            return self.err(path, f"must be an object, got {_type_name(w)}")
        wtype = w.get("type")
        spec = WIDGETS.get(wtype) if isinstance(wtype, str) else None
        if spec is None:
            hint = difflib.get_close_matches(str(wtype), list(WIDGETS), n=1)
            return self.err(f"{path}.type", f"unknown widget type '{wtype}'" + (f" (did you mean '{hint[0]}'?)" if hint else "")
                            if wtype is not None else "required")
        allowed = {"type", *GEOMETRY, *COMMON, *spec.options} | ({"bind"} if spec.bind_kinds is not None else set())
        required = {"type", *GEOMETRY} | {k for k, o in spec.options.items() if o.required}
        if spec.bind_kinds is not None and spec.bind_required:
            required.add("bind")
        self.obj(w, path, allowed, required)
        self.geometry(w, path, (WIDTH, HEIGHT))
        for k, o in {**COMMON, **spec.options}.items():
            if k in w:
                self.option(w[k], o, f"{path}.{k}")
        if "bind" in w and spec.bind_kinds is not None:
            if not isinstance(w["bind"], str):
                self.err(f"{path}.bind", f"must be string, got {_type_name(w['bind'])}")
            else:
                self.source(w["bind"], f"{path}.bind", spec.bind_kinds, f"'{wtype}'")
        spec.check(w, path, self)


def _spec_ok(spec: str) -> bool:
    return any(_try_format(x, spec) for x in (1, 1.5, "x"))


def _try_format(v, spec) -> bool:
    try:
        format(v, spec)
        return True
    except (ValueError, TypeError):
        return False


def _validate_settings(v: _Validator, items) -> None:
    if not isinstance(items, list):
        return v.err("settings", f"must be an array, got {_type_name(items)}")
    seen: set[str] = set()
    for i, s in enumerate(items):
        p = f"settings[{i}]"
        if not v.obj(s, p, SETTING_KEYS, {"key", "type", "default"}):
            continue
        extra = {"number": {"min", "max"}, "enum": {"values"}}.get(s.get("type"), set())
        for k in ("min", "max", "values"):
            if k in s and k not in extra:
                v.err(f"{p}.{k}", f"not allowed for type '{s.get('type')}'")
        if "key" in s and v.string(s["key"], f"{p}.key", 40, KEY_RE, "key"):
            if s["key"] in seen:
                v.err(f"{p}.key", f"duplicate setting '{s['key']}'")
            seen.add(s["key"])
        if "label" in s:
            v.string(s["label"], f"{p}.label", 80)
        t = s.get("type")
        if "type" in s and t not in D.SETTING_KINDS:
            v.err(f"{p}.type", f"must be one of {sorted(D.SETTING_KINDS)}")
            continue
        ok = True
        if t == "enum":
            vals = s.get("values")
            if not (isinstance(vals, list) and vals and all(isinstance(x, str) for x in vals) and len(set(vals)) == len(vals)):
                v.err(f"{p}.values", "required for enum: non-empty list of unique strings")
                ok = False
        if t == "number":
            for k in ("min", "max"):
                if k in s and not _is_num(s[k]):
                    v.err(f"{p}.{k}", "must be a number")
                    ok = False
            if ok and "min" in s and "max" in s and s["min"] > s["max"]:
                v.err(p, "min must not exceed max")
                ok = False
        if ok and "default" in s and "type" in s:
            if (msg := _check_setting_value(s, s["default"])):
                v.err(f"{p}.default", msg)
        if isinstance(s.get("key"), str) and t in D.SETTING_KINDS:
            v.setting_kinds[s["key"]] = D.SETTING_KINDS[t]


def _validate_active(v: _Validator, a) -> None:
    if not v.obj(a, "active", ACTIVE_KEYS, {"widgets"}):
        return
    if "size" in a and a["size"] != [WIDTH, HEIGHT]:
        v.err("active.size", f"must be [{WIDTH}, {HEIGHT}]")
    if "refresh_s" in a and not (_is_num(a["refresh_s"]) and 0.1 <= a["refresh_s"] <= 3600):
        v.err("active.refresh_s", "must be a number between 0.1 and 3600")
    if "triggers" in a and v.obj(a["triggers"], "active.triggers", {"on_game_launch"}):
        if "on_game_launch" in a["triggers"] and not isinstance(a["triggers"]["on_game_launch"], bool):
            v.err("active.triggers.on_game_launch", "must be bool")
    if "code" in a:
        _validate_code(v, a["code"])
    ws = a.get("widgets")
    if ws is None:
        return
    if not isinstance(ws, list):
        return v.err("active.widgets", f"must be an array, got {_type_name(ws)}")
    if len(ws) > MAX_WIDGETS:
        v.err("active.widgets", f"too many widgets ({len(ws)} > {MAX_WIDGETS})")
    ids: set[str] = set()
    for i, w in enumerate(ws[:MAX_WIDGETS]):
        v.widget(w, f"active.widgets[{i}]")
        if isinstance(w, dict) and isinstance(w.get("id"), str):
            if w["id"] in ids:
                v.err(f"active.widgets[{i}].id", f"duplicate id '{w['id']}'")
            ids.add(w["id"])


def _validate_code(v: _Validator, code) -> None:
    if not v.string(code, "active.code", 100):
        return
    if "\x00" in code or "\\" in code or os.path.isabs(code) or ".." in Path(code).parts or not code.endswith(".py"):
        return v.err("active.code", f"'{code}' must be a relative .py path inside the package")
    if v.root is None:
        return
    target = (v.root / code).resolve()
    if v.root.resolve() not in target.parents:
        v.err("active.code", f"'{code}' escapes the package")
    elif not target.is_file():
        v.err("active.code", f"'{code}' not found")
    elif target.stat().st_size > MAX_CODE_BYTES:
        v.err("active.code", f"'{code}' too large (max {MAX_CODE_BYTES} bytes)")


def _validate_idle(v: _Validator, idle) -> None:
    """Structure only: idle widgets are rendered by firmware, whose binding grammar is still open."""
    if not v.obj(idle, "idle", IDLE_KEYS, {"render_mode", "widgets"}):
        return
    if idle.get("render_mode") not in ("mono", "gray4") and "render_mode" in idle:
        v.err("idle.render_mode", "must be 'mono' or 'gray4'")
    for k in ("refresh_interval_s", "full_refresh_every"):
        if k in idle and not (_is_int(idle[k]) and idle[k] >= 1):
            v.err(f"idle.{k}", "must be an integer >= 1")
    if "sources" in idle:
        if isinstance(idle["sources"], dict):
            for name, src in idle["sources"].items():
                p = f"idle.sources.{name}"
                if not isinstance(src, dict) or src.get("type") not in IDLE_SOURCE_TYPES:
                    v.err(p, f"must be an object with type in {sorted(IDLE_SOURCE_TYPES)}")
        else:
            v.err("idle.sources", f"must be an object, got {_type_name(idle['sources'])}")
    ws = idle.get("widgets")
    if ws is None:
        return
    if not isinstance(ws, list):
        return v.err("idle.widgets", f"must be an array, got {_type_name(ws)}")
    for i, w in enumerate(ws[:MAX_WIDGETS]):
        p = f"idle.widgets[{i}]"
        if not isinstance(w, dict):
            v.err(p, f"must be an object, got {_type_name(w)}")
            continue
        if w.get("type") not in IDLE_WIDGETS:
            v.err(f"{p}.type", f"must be one of {sorted(IDLE_WIDGETS)}")
        v.geometry(w, p, (WIDTH, HEIGHT))
        for k in GEOMETRY:
            if k not in w:
                v.err(f"{p}.{k}", "required")


def validate_screen(data: Any, root: Path | None = None, folder_name: str | None = None) -> list[str]:
    """All problems in a parsed screen.json (empty list = valid). `root` enables asset/code file checks."""
    v = _Validator(root)
    if not v.obj(data, "", TOP_KEYS, {"schema", "id", "name", "version", "author"}):
        return v.errors
    if "schema" in data and data["schema"] != SCHEMA_VERSION:
        v.err("schema", f"must be {SCHEMA_VERSION}")
    if "id" in data and v.string(data["id"], "id", 64, ID_RE, "id (lowercase a-z, 0-9, '-')"):
        if folder_name is not None and data["id"] != folder_name:
            v.err("id", f"'{data['id']}' must match the folder name '{folder_name}'")
    for k, n in (("name", 80), ("author", 80), ("description", 500)):
        if k in data:
            v.string(data[k], k, n)
    if "version" in data:
        v.string(data["version"], "version", 40, SEMVER_RE, "version (expected x.y.z)")
    if not ("active" in data or "idle" in data):
        v.err("", "needs an 'active' or an 'idle' section")
    if "settings" in data:
        _validate_settings(v, data["settings"])
    if "active" in data:
        _validate_active(v, data["active"])
    if "idle" in data:
        _validate_idle(v, data["idle"])
    return v.errors


def _no_dupes(pairs):
    d = {}
    for k, val in pairs:
        if k in d:
            raise ValueError(f"duplicate key '{k}'")
        d[k] = val
    return d


def _no_const(name):
    raise ValueError(f"invalid number {name}")


def load_screen(path: str | Path, folder_name: str | None = None) -> Screen:
    """Load and fully validate a package folder. Raises ScreenError listing every problem.

    `folder_name` replaces the folder-name check (default: the folder's own name); the package store
    keeps versions in `<id>/<version>/` and passes the id.
    """
    root = Path(path)
    f = root / "screen.json"
    if not f.is_file():
        raise ScreenError(f"{f}: not found (a package is a folder containing screen.json)")
    if f.stat().st_size > MAX_JSON_BYTES:
        raise ScreenError(f"{f}: too large (max {MAX_JSON_BYTES} bytes)")
    try:
        data = json.loads(f.read_text("utf-8"), object_pairs_hook=_no_dupes, parse_constant=_no_const)
    except (ValueError, UnicodeDecodeError) as e:
        raise ScreenError(f"screen.json: invalid JSON: {e}") from None
    errs = validate_screen(data, root, folder_name or root.resolve().name)
    if errs:
        raise ScreenError([f"screen.json: {e}" for e in errs])
    return Screen(root=root, id=data["id"], name=data["name"], version=data["version"], author=data["author"],
                  description=data.get("description", ""), active=data.get("active"), idle=data.get("idle"),
                  settings=data.get("settings", []))

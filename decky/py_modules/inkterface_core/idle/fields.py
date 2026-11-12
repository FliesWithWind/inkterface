"""Source / field / font tables for the idle layout wire format.

Mirrors the tables in docs/idle-mode.md and firmware/src/ee05/idle_layout.h exactly.
No Pillow / Decky imports; pure stdlib so the decoder oracle stays dependency-free.
"""
from __future__ import annotations

# --- wire constants (keep in step with idle_layout.h) ----------------------
LAYOUT_MAGIC = b"ILY1"
LAYOUT_VER = 1
STORE_MAGIC = b"ISTR"
STORE_VER = 1

MAX_LAYOUT_BYTES = 8192
MAX_STORE_BYTES = 49152
MAX_SOURCES = 8
MAX_WIDGETS = 64
HEADER_LEN = 16
SOURCE_LEN = 8
WIDGET_LEN = 28

NO_STR = 0xFFFF        # pool offset meaning "absent"
NO_SRC = 0xFF
SCREEN_W = 648
SCREEN_H = 480

# render modes
RM_MONO = 0
RM_GRAY4 = 1

# layout flags
LF_DARK = 1

# widget flags
WF_INVERT = 1    # fill box with ink
WF_CLIP = 2      # text: clip instead of "..."
WF_WRAP = 4      # text: word wrap
WF_IMG_2BPP = 8  # image: 2 bits/pixel

# source flags (weather units)
SF_UNITS_MASK = 3  # 0 global, 1 metric, 2 imperial

# source types
SRC_CLOCK = 1
SRC_WEATHER = 2
SRC_STEAM = 3
SRC_HTTP_JSON = 4

# widget types
W_TEXT = 1
W_NUMBER = 2
W_BAR = 3
W_ICON = 4
W_IMAGE = 5
W_CLOCK = 6

# align
AL_LEFT, AL_CENTER, AL_RIGHT = 0, 1, 2
VA_TOP, VA_MIDDLE, VA_BOTTOM = 0, 1, 2

DECIMALS_DEFAULT = 0x7F  # number.p0: default formatting

# icon sets
ICONS_WEATHER = 0
ICONS_NAMED = 1
# named icon ids, in order
ICON_NAMES = ["sun", "cloud", "rain", "snow", "storm", "fog", "clock", "gamepad"]

# default poll intervals (seconds), poll_s == 0
POLL_WEATHER = 900
POLL_STEAM = 900
POLL_HTTP_JSON = 300
MIN_POLL = 30

# template token byte, degree byte
TOK = 0x01
CH_DEGREE = 0x7F  # renders the degree sign; screen.json spells it U+00B0

# http_json url limits
HTTP_URL_MIN = 9     # "https://" + at least 1 char
HTTP_URL_MAX = 255
HTTP_PTR_MAX = 128


# --- fields ----------------------------------------------------------------
# (src type, field id, screen.json path (no source name, no "[n]"), kind, idx count)
# kind: N number, S string, T time (epoch), A number-or-string. idx count 0 = not indexed.
class Field:
    __slots__ = ("src", "id", "path", "kind", "count")

    def __init__(self, src: int, fid: int, path: str, kind: str, count: int) -> None:
        self.src = src
        self.id = fid
        self.path = path
        self.kind = kind
        self.count = count


FIELDS: list[Field] = [
    Field(SRC_CLOCK, 0, "now", "T", 0),
    Field(SRC_WEATHER, 0, "current.temp", "N", 0),
    Field(SRC_WEATHER, 1, "current.code", "N", 0),
    Field(SRC_WEATHER, 2, "current.condition", "S", 0),
    Field(SRC_WEATHER, 3, "today.max", "N", 0),
    Field(SRC_WEATHER, 4, "today.min", "N", 0),
    Field(SRC_WEATHER, 5, "today.sunrise", "S", 0),
    Field(SRC_WEATHER, 6, "today.sunset", "S", 0),
    Field(SRC_WEATHER, 7, "today.rain", "N", 0),
    Field(SRC_WEATHER, 8, "forecast.day", "S", 3),
    Field(SRC_WEATHER, 9, "forecast.max", "N", 3),
    Field(SRC_WEATHER, 10, "forecast.min", "N", 3),
    Field(SRC_WEATHER, 11, "forecast.code", "N", 3),
    Field(SRC_WEATHER, 12, "forecast.condition", "S", 3),
    Field(SRC_WEATHER, 13, "forecast.rain", "N", 3),
    Field(SRC_STEAM, 0, "players", "N", 0),
    Field(SRC_STEAM, 1, "online", "N", 0),
    Field(SRC_STEAM, 2, "persona", "S", 0),
    Field(SRC_STEAM, 3, "level", "N", 0),
    Field(SRC_STEAM, 4, "games_owned", "N", 0),
    Field(SRC_STEAM, 5, "hours_total", "N", 0),
    Field(SRC_STEAM, 6, "friends_online", "N", 0),
    Field(SRC_STEAM, 7, "friends_ingame", "N", 0),
    Field(SRC_STEAM, 8, "recent.name", "S", 3),
    Field(SRC_STEAM, 9, "recent.hours2w", "N", 3),
    Field(SRC_STEAM, 10, "achievement.name", "S", 0),
    Field(SRC_STEAM, 11, "achievement.unlocked", "N", 0),
    Field(SRC_STEAM, 12, "achievement.total", "N", 0),
    Field(SRC_HTTP_JSON, 0, "value", "A", 0),
]

_BY_SRC_ID: dict[tuple[int, int], Field] = {(f.src, f.id): f for f in FIELDS}
_BY_SRC_PATH: dict[tuple[int, str], Field] = {(f.src, f.path): f for f in FIELDS}

SRC_TYPE_BY_NAME = {
    "clock": SRC_CLOCK,
    "weather": SRC_WEATHER,
    "steam": SRC_STEAM,
    "http_json": SRC_HTTP_JSON,
}
SRC_NAME_BY_TYPE = {v: k for k, v in SRC_TYPE_BY_NAME.items()}


def find_field(src_type: int, fid: int) -> Field | None:
    return _BY_SRC_ID.get((src_type, fid))


def find_path(src_type: int, path: str) -> Field | None:
    return _BY_SRC_PATH.get((src_type, path))


def field_table_text() -> str:
    """Lines `<src> <id> <path> <kind> <count>\\n`, sorted by (src, id).

    The firmware `idle_layout_host --dump-fields` prints the same text; a pytest diffs them.
    """
    lines = []
    for f in sorted(FIELDS, key=lambda f: (f.src, f.id)):
        lines.append(f"{f.src} {f.id} {f.path} {f.kind} {f.count}\n")
    return "".join(lines)


# --- fonts -----------------------------------------------------------------
# IBM Plex Mono. id -> (style, pixel size, charset).  screen.json spells them
# "regular-28" / "bold-160"; width = 0.6 * size.
_ASCII = "".join(chr(c) for c in range(0x20, 0x7F))          # 32..126
_FULL_CHARSET = _ASCII + "\u00b0"                            # + degree
_DIGIT_CHARSET = "0123456789:.-/% \u00b0"

# full ASCII fonts: regular ids 0..5, bold ids 8..13, sizes 14 18 22 28 36 48
_FULL_SIZES = [14, 18, 22, 28, 36, 48]
# digits-only bold fonts: ids 16..18, sizes 80 120 160
_DIGIT_SIZES = [80, 120, 160]


class FontInfo:
    __slots__ = ("id", "style", "size", "charset")

    def __init__(self, fid: int, style: str, size: int, charset: str) -> None:
        self.id = fid
        self.style = style
        self.size = size
        self.charset = charset


FONTS: dict[int, FontInfo] = {}
_FONT_BY_NAME: dict[str, FontInfo] = {}

for _i, _sz in enumerate(_FULL_SIZES):
    FONTS[_i] = FontInfo(_i, "regular", _sz, _FULL_CHARSET)
    FONTS[8 + _i] = FontInfo(8 + _i, "bold", _sz, _FULL_CHARSET)
for _i, _sz in enumerate(_DIGIT_SIZES):
    FONTS[16 + _i] = FontInfo(16 + _i, "bold", _sz, _DIGIT_CHARSET)
for _f in FONTS.values():
    _FONT_BY_NAME[f"{_f.style}-{_f.size}"] = _f


def font_valid(fid: int) -> bool:
    return fid in FONTS


def font_by_name(name: str) -> FontInfo | None:
    return _FONT_BY_NAME.get(name)


def font_has_char(fid: int, ch: str) -> bool:
    """Charset membership test for a single character. U+00B0 counts as degree."""
    f = FONTS.get(fid)
    if f is None:
        return False
    return ch in f.charset

# stricter source parameter limits (the firmware decoder enforces the same)
import re as _re  # noqa: E402

CLOCK_TZ_MAX = 63
STEAM_APPID_MAX = 10
LATLON_RE = _re.compile(r"-?[0-9]+(?:\.[0-9]+)?,-?[0-9]+(?:\.[0-9]+)?")

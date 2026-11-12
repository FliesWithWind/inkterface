// Idle layout wire format "ILY1" (panel side). See docs/idle-mode.md, "Layout wire format".
//
// Pure C++11, no Arduino: shared by the firmware and the host tests
// (firmware/host_test/idle_*_host.cpp). The host compiler is
// decky/py_modules/inkterface_core/idle/compile.py; both must agree byte for byte.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace idle {

constexpr uint8_t LAYOUT_MAGIC[4] = {'I', 'L', 'Y', '1'};
constexpr uint8_t LAYOUT_VER = 1;
constexpr size_t MAX_LAYOUT_BYTES = 8192;
constexpr size_t MAX_SOURCES = 8;
constexpr size_t MAX_WIDGETS = 64;
constexpr size_t HEADER_LEN = 16;
constexpr size_t SOURCE_LEN = 8;
constexpr size_t WIDGET_LEN = 28;
constexpr uint16_t NO_STR = 0xFFFF;  // pool offset meaning "absent"
constexpr uint8_t NO_SRC = 0xFF;
constexpr uint16_t SCREEN_W = 648, SCREEN_H = 480;

enum RenderMode : uint8_t { RM_MONO = 0, RM_GRAY4 = 1 };
enum SrcType : uint8_t { SRC_CLOCK = 1, SRC_WEATHER = 2, SRC_STEAM = 3, SRC_HTTP_JSON = 4 };
enum WidgetType : uint8_t { W_TEXT = 1, W_NUMBER = 2, W_BAR = 3, W_ICON = 4, W_IMAGE = 5, W_CLOCK = 6 };

constexpr uint8_t LF_DARK = 1;  // layout flag: black background, white default ink

// widget flags
constexpr uint8_t WF_INVERT = 1;    // fill box with ink, draw content in background
constexpr uint8_t WF_CLIP = 2;      // text: clip instead of "..." when too wide
constexpr uint8_t WF_WRAP = 4;      // text: word wrap
constexpr uint8_t WF_IMG_2BPP = 8;  // image: 2 bits/pixel (gray4) instead of 1
// source flags (weather): units override
constexpr uint8_t SF_UNITS_MASK = 3;  // 0 = global config, 1 = metric, 2 = imperial

enum Align : uint8_t { AL_LEFT = 0, AL_CENTER = 1, AL_RIGHT = 2 };
enum VAlign : uint8_t { VA_TOP = 0, VA_MIDDLE = 1, VA_BOTTOM = 2 };
constexpr uint8_t DECIMALS_DEFAULT = 0x7F;  // number.p0: default formatting
enum IconSet : uint8_t { ICONS_WEATHER = 0, ICONS_NAMED = 1 };
enum IconName : uint8_t {
    IC_SUN = 0, IC_CLOUD, IC_RAIN, IC_SNOW, IC_STORM, IC_FOG, IC_CLOCK, IC_GAMEPAD, IC_COUNT
};

// default poll intervals (seconds) when Source.pollS == 0
constexpr uint16_t POLL_WEATHER = 900, POLL_STEAM = 900, POLL_HTTP_JSON = 300;
constexpr uint16_t MIN_POLL = 30;

// text template token: TOK, src+1, field+1, idx+1, spec+1   (spec = decimals 0..6 or DECIMALS_DEFAULT)
constexpr uint8_t TOK = 0x01;
constexpr uint8_t CH_DEGREE = 0x7F;  // renders the degree sign

#pragma pack(push, 1)
struct Source {  // 8 bytes on the wire
    uint8_t type;
    uint8_t flags;
    uint16_t pollS;
    uint16_t s1, s2;  // pool offsets
};
struct Widget {  // 28 bytes on the wire
    uint8_t type;
    uint8_t flags;
    uint16_t x, y, w, h;
    uint8_t font;
    uint8_t align;  // bits 0-1 Align, bits 2-3 VAlign
    uint8_t color;  // 0 black .. 3 white
    uint8_t border;
    uint16_t text;  // pool offset: text template / clock format
    uint16_t aux;   // pool offset: number unit / image bitmap
    uint8_t src, field, idx;
    uint8_t p0, p1, maxLines;
    int16_t lo, hi;  // bar range
};
#pragma pack(pop)
static_assert(sizeof(Source) == SOURCE_LEN, "wire layout");
static_assert(sizeof(Widget) == WIDGET_LEN, "wire layout");

struct Layout {
    uint8_t mode = RM_MONO;
    uint8_t flags = 0;
    uint16_t refreshS = 60;
    uint16_t fullEvery = 60;
    uint8_t nSources = 0, nWidgets = 0;
    uint16_t poolLen = 0;
    Source src[MAX_SOURCES];
    Widget w[MAX_WIDGETS];
    const uint8_t *pool = nullptr;  // points into the buffer passed to decodeLayout (must outlive)

    const char *str(uint16_t off) const { return off < poolLen ? (const char *)pool + off : ""; }
    uint16_t effectivePollS(const Source &s) const
    {
        if (s.pollS) return s.pollS;
        return s.type == SRC_WEATHER ? POLL_WEATHER : s.type == SRC_STEAM ? POLL_STEAM : POLL_HTTP_JSON;
    }
};

enum DecodeError : uint8_t {
    DEC_OK = 0,
    DEC_TOO_SHORT,     // shorter than header + crc, or sections run past the end
    DEC_TOO_BIG,       // > MAX_LAYOUT_BYTES
    DEC_BAD_MAGIC,
    DEC_BAD_VERSION,
    DEC_BAD_CRC,
    DEC_BAD_HEADER,    // mode, counts, reserved byte, refresh values, length mismatch
    DEC_BAD_SOURCE,    // unknown type/flags, bad pool ref, bad url/pointer/poll, unknown field
    DEC_BAD_WIDGET,    // unknown type, bad geometry/font/color/border/align/params, bad pool ref
    DEC_BAD_STRING,    // pool ref without NUL terminator, control chars, bad template token
    DEC_BAD_REF,       // widget -> source/field/idx that does not exist or has the wrong kind
};
const char *decodeErrorName(DecodeError e);

// Validates every byte it will later trust (see docs/idle-mode.md for the full rule list).
// On DEC_OK `out` is complete; `out.pool` aliases `buf`.
DecodeError decodeLayout(const uint8_t *buf, size_t len, Layout &out);

// ---------------------------------------------------------------------------
// Fonts: ids are part of the wire format. Sizes are pixel sizes of IBM Plex Mono.
constexpr uint8_t FONT_REGULAR_BASE = 0, FONT_BOLD_BASE = 8, FONT_DIGITS_BASE = 16;
// full ASCII (32..126, 0x7F = degree): regular ids 0..5, bold ids 8..13
constexpr uint8_t FULL_SIZES[6] = {14, 18, 22, 28, 36, 48};
// digits subset ("0123456789:.-/% " and 0x7F), bold only: ids 16..18
constexpr uint8_t DIGIT_SIZES_N = 3;
constexpr uint16_t DIGIT_SIZES[3] = {80, 120, 160};
bool fontValid(uint8_t id);
bool fontHasChar(uint8_t id, uint8_t ch);  // charset test only (needs no glyph data)

// ---------------------------------------------------------------------------
// Binding fields. Every (source type, field id) a widget or template token may reference.
struct FieldInfo {
    uint8_t src;      // SrcType
    uint8_t id;
    const char *path; // screen.json path without the source name and without "[n]"
    char kind;        // 'N' number, 'S' string, 'T' time (epoch seconds), 'A' number or string
    uint8_t count;    // 0 = not indexed, else valid idx 0..count-1
};
extern const FieldInfo kFields[];
extern const size_t kFieldCount;
const FieldInfo *findField(uint8_t srcType, uint8_t id);
const FieldInfo *findPath(uint8_t srcType, const char *path);

}  // namespace idle

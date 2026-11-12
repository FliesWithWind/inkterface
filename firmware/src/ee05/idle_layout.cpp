#include "idle_layout.h"

#include <string.h>

#include "usbproto.h"

namespace idle {

const char *decodeErrorName(DecodeError e)
{
    static const char *n[] = {"OK",       "TOO_SHORT", "TOO_BIG",   "BAD_MAGIC",  "BAD_VERSION", "BAD_CRC",
                              "BAD_HEADER", "BAD_SOURCE", "BAD_WIDGET", "BAD_STRING", "BAD_REF"};
    return e <= DEC_BAD_REF ? n[e] : "?";
}

// ---------------------------------------------------------------------------
// Fields
const FieldInfo kFields[] = {
    {SRC_CLOCK, 0, "now", 'T', 0},
    {SRC_WEATHER, 0, "current.temp", 'N', 0},
    {SRC_WEATHER, 1, "current.code", 'N', 0},
    {SRC_WEATHER, 2, "current.condition", 'S', 0},
    {SRC_WEATHER, 3, "today.max", 'N', 0},
    {SRC_WEATHER, 4, "today.min", 'N', 0},
    {SRC_WEATHER, 5, "today.sunrise", 'S', 0},
    {SRC_WEATHER, 6, "today.sunset", 'S', 0},
    {SRC_WEATHER, 7, "today.rain", 'N', 0},
    {SRC_WEATHER, 8, "forecast.day", 'S', 3},
    {SRC_WEATHER, 9, "forecast.max", 'N', 3},
    {SRC_WEATHER, 10, "forecast.min", 'N', 3},
    {SRC_WEATHER, 11, "forecast.code", 'N', 3},
    {SRC_WEATHER, 12, "forecast.condition", 'S', 3},
    {SRC_WEATHER, 13, "forecast.rain", 'N', 3},
    {SRC_STEAM, 0, "players", 'N', 0},
    {SRC_STEAM, 1, "online", 'N', 0},
    {SRC_STEAM, 2, "persona", 'S', 0},
    {SRC_STEAM, 3, "level", 'N', 0},
    {SRC_STEAM, 4, "games_owned", 'N', 0},
    {SRC_STEAM, 5, "hours_total", 'N', 0},
    {SRC_STEAM, 6, "friends_online", 'N', 0},
    {SRC_STEAM, 7, "friends_ingame", 'N', 0},
    {SRC_STEAM, 8, "recent.name", 'S', 3},
    {SRC_STEAM, 9, "recent.hours2w", 'N', 3},
    {SRC_STEAM, 10, "achievement.name", 'S', 0},
    {SRC_STEAM, 11, "achievement.unlocked", 'N', 0},
    {SRC_STEAM, 12, "achievement.total", 'N', 0},
    {SRC_HTTP_JSON, 0, "value", 'A', 0},
};
const size_t kFieldCount = sizeof(kFields) / sizeof(kFields[0]);

const FieldInfo *findField(uint8_t srcType, uint8_t id)
{
    for (size_t i = 0; i < kFieldCount; i++)
        if (kFields[i].src == srcType && kFields[i].id == id) return &kFields[i];
    return nullptr;
}

const FieldInfo *findPath(uint8_t srcType, const char *path)
{
    for (size_t i = 0; i < kFieldCount; i++)
        if (kFields[i].src == srcType && !strcmp(kFields[i].path, path)) return &kFields[i];
    return nullptr;
}

// ---------------------------------------------------------------------------
// Fonts
bool fontValid(uint8_t id)
{
    return id < 6 || (id >= FONT_BOLD_BASE && id < FONT_BOLD_BASE + 6) ||
           (id >= FONT_DIGITS_BASE && id < FONT_DIGITS_BASE + DIGIT_SIZES_N);
}

bool fontHasChar(uint8_t id, uint8_t ch)
{
    if (!fontValid(id)) return false;
    if (id >= FONT_DIGITS_BASE) return (ch >= '0' && ch <= '9') || ch == ':' || ch == '.' || ch == '-' ||
                                       ch == '/' || ch == '%' || ch == ' ' || ch == CH_DEGREE;
    return (ch >= 0x20 && ch <= 0x7E) || ch == CH_DEGREE;
}

// ---------------------------------------------------------------------------
namespace {

inline uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
inline uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// Mirrors decky/py_modules/inkterface_core/idle/decode.py check for check and in the same order
// (decky/tests/idle/test_cpp_decode.py fuzzes the two against each other).

// pool string at `off`: offset inside the pool, NUL inside the pool, bytes 0x20..0x7F
// (tokens allowed only when asked, then 01 + 4 exempt bytes). Returns the length in `len`.
DecodeError poolString(const Layout &l, uint16_t off, bool tokens, size_t &len)
{
    if (off >= l.poolLen) return DEC_BAD_STRING;
    size_t end = off;
    while (end < l.poolLen && l.pool[end]) end++;
    if (end >= l.poolLen) return DEC_BAD_STRING;
    for (size_t i = off; i < end;) {
        uint8_t c = l.pool[i];
        if (c == TOK) {
            if (!tokens || i + 4 >= end) return DEC_BAD_STRING;
            i += 5;
        } else if (c >= 0x20 && c <= 0x7F) {
            i++;
        } else {
            return DEC_BAD_STRING;
        }
    }
    len = end - off;
    return DEC_OK;
}

bool allDigits(const char *s, size_t n)
{
    if (!n) return false;
    for (size_t i = 0; i < n; i++)
        if (s[i] < '0' || s[i] > '9') return false;
    return true;
}

// "lat,lon": [-]digits[.digits] , [-]digits[.digits]
bool latLon(const char *s, size_t n)
{
    size_t i = 0;
    for (int part = 0; part < 2; part++) {
        if (i < n && s[i] == '-') i++;
        size_t d = 0;
        while (i < n && s[i] >= '0' && s[i] <= '9') i++, d++;
        if (!d) return false;
        if (i < n && s[i] == '.') {
            i++;
            size_t f = 0;
            while (i < n && s[i] >= '0' && s[i] <= '9') i++, f++;
            if (!f) return false;
        }
        if (part == 0) {
            if (i >= n || s[i] != ',') return false;
            i++;
        }
    }
    return i == n;
}

DecodeError checkSource(const Layout &l, const Source &s)
{
    size_t n = 0;
    DecodeError e;
    const char *p1 = s.s1 < l.poolLen ? (const char *)l.pool + s.s1 : "";
    switch (s.type) {
    case SRC_CLOCK:
        if (s.flags || s.s2 != NO_STR) return DEC_BAD_SOURCE;
        if (s.s1 != NO_STR) {
            if ((e = poolString(l, s.s1, false, n))) return e;
            if (n > 63) return DEC_BAD_SOURCE;
        }
        break;
    case SRC_WEATHER:
        if ((s.flags & ~SF_UNITS_MASK) || (s.flags & SF_UNITS_MASK) == 3 || s.s2 != NO_STR) return DEC_BAD_SOURCE;
        if (s.s1 != NO_STR) {
            if ((e = poolString(l, s.s1, false, n))) return e;
            if (!latLon(p1, n)) return DEC_BAD_SOURCE;
        }
        break;
    case SRC_STEAM:
        if (s.flags || s.s2 != NO_STR) return DEC_BAD_SOURCE;
        if (s.s1 != NO_STR) {
            if ((e = poolString(l, s.s1, false, n))) return e;
            if (n > 10 || !allDigits(p1, n)) return DEC_BAD_SOURCE;
        }
        break;
    case SRC_HTTP_JSON:
        if (s.flags || s.s1 == NO_STR) return DEC_BAD_SOURCE;
        if ((e = poolString(l, s.s1, false, n))) return e;
        if (n < 9 || n > 255 || strncmp(p1, "https://", 8) != 0) return DEC_BAD_SOURCE;
        if (s.s2 != NO_STR) {
            if ((e = poolString(l, s.s2, false, n))) return e;
            if (n > 128 || (n && l.pool[s.s2] != '/')) return DEC_BAD_SOURCE;
        }
        break;
    default: return DEC_BAD_SOURCE;
    }
    if (s.type == SRC_CLOCK) return s.pollS ? DEC_BAD_SOURCE : DEC_OK;
    return (s.pollS && s.pollS < MIN_POLL) ? DEC_BAD_SOURCE : DEC_OK;
}

// source/field/idx reference of a bound widget; `kinds` = accepted field kinds
DecodeError checkRef(const Layout &l, const Widget &w, const char *kinds)
{
    if (w.src == NO_SRC || w.src >= l.nSources) return DEC_BAD_REF;
    const FieldInfo *fi = findField(l.src[w.src].type, w.field);
    if (!fi || !strchr(kinds, fi->kind)) return DEC_BAD_REF;
    if (fi->count == 0 ? w.idx != 0 : w.idx >= fi->count) return DEC_BAD_REF;
    return DEC_OK;
}

// literal bytes of a text template must be in the font; tokens are skipped
bool templateCharsOk(const Layout &l, uint8_t font, size_t off, size_t len)
{
    for (size_t i = off; i < off + len;) {
        if (l.pool[i] == TOK) { i += 5; continue; }
        if (!fontHasChar(font, l.pool[i])) return false;
        i++;
    }
    return true;
}

DecodeError checkTemplateRefs(const Layout &l, size_t off, size_t len)
{
    // pass 1: spec charset (BAD_STRING) like the Python _check_template_tokens
    for (size_t i = off; i < off + len;) {
        if (l.pool[i] != TOK) { i++; continue; }
        uint8_t s = l.pool[i + 1], f = l.pool[i + 2], x = l.pool[i + 3], spec = l.pool[i + 4];
        i += 5;
        if (!s || !f || !x) return DEC_BAD_STRING;
        if (!(spec >= '0' && spec <= '6') && spec != 'D') return DEC_BAD_STRING;
    }
    return DEC_OK;
}

DecodeError resolveTokens(const Layout &l, size_t off, size_t len)
{
    for (size_t i = off; i < off + len;) {
        if (l.pool[i] != TOK) { i++; continue; }
        unsigned s = l.pool[i + 1] - 1u, f = l.pool[i + 2] - 1u, x = l.pool[i + 3] - 1u;
        uint8_t spec = l.pool[i + 4];
        i += 5;
        if (s >= l.nSources) return DEC_BAD_REF;
        const FieldInfo *fi = findField(l.src[s].type, (uint8_t)f);
        if (!fi || fi->kind == 'T') return DEC_BAD_REF;
        if (fi->count == 0 ? x != 0 : x >= fi->count) return DEC_BAD_REF;
        if (fi->kind == 'S' && spec != 'D') return DEC_BAD_STRING;
    }
    return DEC_OK;
}

bool clockCharsOk(uint8_t font, const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n;) {
        if (p[i] == '%') { i += 2; continue; }
        if (!fontHasChar(font, p[i])) return false;
        i++;
    }
    return true;
}

DecodeError checkWidget(const Layout &l, const Widget &w)
{
    if (w.type < W_TEXT || w.type > W_CLOCK) return DEC_BAD_WIDGET;
    if (w.flags & ~(WF_INVERT | WF_CLIP | WF_WRAP | WF_IMG_2BPP)) return DEC_BAD_WIDGET;
    if (w.w < 1 || w.h < 1) return DEC_BAD_WIDGET;
    if ((uint32_t)w.x + w.w > SCREEN_W || (uint32_t)w.y + w.h > SCREEN_H) return DEC_BAD_WIDGET;
    if ((w.align & 3) > 2 || ((w.align >> 2) & 3) > 2 || (w.align & ~0x0F)) return DEC_BAD_WIDGET;
    if (w.color > 3 || w.border > 4) return DEC_BAD_WIDGET;
    if (l.mode == RM_MONO && (w.type == W_TEXT || w.type == W_NUMBER || w.type == W_CLOCK) &&
        (w.color == 1 || w.color == 2))
        return DEC_BAD_WIDGET;

    size_t n = 0;
    DecodeError e;
    switch (w.type) {
    case W_TEXT: {
        if (w.flags & WF_IMG_2BPP) return DEC_BAD_WIDGET;
        if (w.text == NO_STR || !fontValid(w.font)) return DEC_BAD_WIDGET;
        if (w.src != NO_SRC || w.field || w.idx || w.p0 || w.p1 || w.lo || w.hi) return DEC_BAD_WIDGET;
        if (w.aux != NO_STR) return DEC_BAD_WIDGET;
        if ((e = poolString(l, w.text, true, n))) return e;
        if ((e = checkTemplateRefs(l, w.text, n))) return e;
        if (!templateCharsOk(l, w.font, w.text, n)) return DEC_BAD_STRING;
        return resolveTokens(l, w.text, n);
    }
    case W_NUMBER: {
        if (w.flags & (WF_CLIP | WF_WRAP | WF_IMG_2BPP)) return DEC_BAD_WIDGET;
        if (!fontValid(w.font) || w.text != NO_STR) return DEC_BAD_WIDGET;
        if (w.lo || w.hi || w.p1) return DEC_BAD_WIDGET;
        if (!(w.p0 <= 6 || w.p0 == DECIMALS_DEFAULT)) return DEC_BAD_WIDGET;
        if ((e = checkRef(l, w, "NA"))) return e;
        if (w.aux != NO_STR) {
            if ((e = poolString(l, w.aux, false, n))) return e;
            for (size_t i = 0; i < n; i++)
                if (!fontHasChar(w.font, l.pool[w.aux + i])) return DEC_BAD_STRING;
        }
        return DEC_OK;
    }
    case W_BAR:
        if (w.flags & (WF_CLIP | WF_WRAP | WF_IMG_2BPP)) return DEC_BAD_WIDGET;
        if (w.text != NO_STR || w.aux != NO_STR) return DEC_BAD_WIDGET;
        if (w.font || w.p1) return DEC_BAD_WIDGET;
        if (w.p0 > 1 || w.lo >= w.hi) return DEC_BAD_WIDGET;
        return checkRef(l, w, "NA");
    case W_ICON:
        if (w.flags & (WF_CLIP | WF_WRAP | WF_IMG_2BPP)) return DEC_BAD_WIDGET;
        if (w.text != NO_STR || w.aux != NO_STR || w.font) return DEC_BAD_WIDGET;
        if (w.p0 == ICONS_WEATHER) {
            if (w.p1) return DEC_BAD_WIDGET;
            return checkRef(l, w, "N");
        }
        if (w.p0 != ICONS_NAMED) return DEC_BAD_WIDGET;
        if (w.p1 >= IC_COUNT) return DEC_BAD_WIDGET;
        if (w.src != NO_SRC || w.field || w.idx) return DEC_BAD_WIDGET;
        return DEC_OK;
    case W_IMAGE: {
        if (w.flags & (WF_CLIP | WF_WRAP)) return DEC_BAD_WIDGET;
        bool two = w.flags & WF_IMG_2BPP;
        if (two && l.mode != RM_GRAY4) return DEC_BAD_WIDGET;
        if (w.text != NO_STR || w.font || w.src != NO_SRC || w.field || w.idx) return DEC_BAD_WIDGET;
        if (w.p0 || w.p1 || w.lo || w.hi) return DEC_BAD_WIDGET;
        if (w.aux == NO_STR) return DEC_BAD_WIDGET;
        size_t need = (two ? (size_t)((w.w + 3) / 4) : (size_t)((w.w + 7) / 8)) * w.h;
        return (size_t)w.aux + need > l.poolLen ? DEC_BAD_WIDGET : DEC_OK;
    }
    case W_CLOCK: {
        if (w.flags & (WF_CLIP | WF_WRAP | WF_IMG_2BPP)) return DEC_BAD_WIDGET;
        if (!fontValid(w.font) || w.text == NO_STR) return DEC_BAD_WIDGET;
        if (w.aux != NO_STR || w.p0 || w.p1 || w.lo || w.hi) return DEC_BAD_WIDGET;
        if ((e = checkRef(l, w, "T"))) return e;
        if ((e = poolString(l, w.text, false, n))) return e;
        const uint8_t *f = l.pool + w.text;
        for (size_t i = 0; i < n; i++) {
            if (f[i] != '%') continue;
            if (i + 1 >= n || !strchr("HMSIpAadeBbmyYj%", f[i + 1])) return DEC_BAD_STRING;
            i++;
        }
        return clockCharsOk(w.font, f, n) ? DEC_OK : DEC_BAD_STRING;
    }
    default: return DEC_BAD_WIDGET;
    }
}

}  // namespace

DecodeError decodeLayout(const uint8_t *buf, size_t len, Layout &out)
{
    if (len < HEADER_LEN + 4) return DEC_TOO_SHORT;
    if (len > MAX_LAYOUT_BYTES) return DEC_TOO_BIG;
    if (memcmp(buf, LAYOUT_MAGIC, 4)) return DEC_BAD_MAGIC;
    if (buf[4] != LAYOUT_VER) return DEC_BAD_VERSION;
    if (usbproto::crc32(buf, len - 4) != rd32(buf + len - 4)) return DEC_BAD_CRC;

    Layout l;
    l.mode = buf[5];
    l.refreshS = rd16(buf + 6);
    l.fullEvery = rd16(buf + 8);
    l.flags = buf[10];
    l.nSources = buf[11];
    l.nWidgets = buf[12];
    l.poolLen = rd16(buf + 14);
    if (l.mode > RM_GRAY4 || (l.flags & ~LF_DARK) || buf[13] != 0 || !l.refreshS || !l.fullEvery ||
        l.nSources > MAX_SOURCES || l.nWidgets > MAX_WIDGETS)
        return DEC_BAD_HEADER;
    size_t want = HEADER_LEN + (size_t)SOURCE_LEN * l.nSources + (size_t)WIDGET_LEN * l.nWidgets + l.poolLen + 4;
    if (want != len) return DEC_BAD_HEADER;
    const uint8_t *p = buf + HEADER_LEN;
    for (int i = 0; i < l.nSources; i++, p += SOURCE_LEN) memcpy(&l.src[i], p, SOURCE_LEN);
    for (int i = 0; i < l.nWidgets; i++, p += WIDGET_LEN) memcpy(&l.w[i], p, WIDGET_LEN);  // little endian host
    l.pool = p;
    if (!l.poolLen || l.pool[0] != 0) return DEC_BAD_STRING;

    for (int i = 0; i < l.nSources; i++) {
        DecodeError e = checkSource(l, l.src[i]);
        if (e) return e;
    }
    for (int i = 0; i < l.nWidgets; i++) {
        DecodeError e = checkWidget(l, l.w[i]);
        if (e) return e;
    }
    out = l;
    out.pool = l.pool;
    return DEC_OK;
}

}  // namespace idle

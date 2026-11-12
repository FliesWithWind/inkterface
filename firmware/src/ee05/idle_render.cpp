#include "idle_render.h"

#include <math.h>
#include <stdio.h>
#include <string.h>
#include <vector>

namespace idle {

// ---------------------------------------------------------------------------
// formatting
std::string formatNumber(double v, uint8_t decimals)
{
    if (!isfinite(v)) return "--";
    char b[48];
    if (decimals == DECIMALS_DEFAULT) {
        snprintf(b, sizeof b, "%.1f", v);
        size_t n = strlen(b);
        if (n > 2 && b[n - 1] == '0' && b[n - 2] == '.') b[n - 2] = 0;
    } else {
        snprintf(b, sizeof b, "%.*f", decimals > 6 ? 6 : decimals, v);
    }
    // "-0", "-0.0": a rounded-away negative
    const char *p = b + (b[0] == '-');
    bool zero = true;
    for (; *p; p++)
        if (*p != '0' && *p != '.') zero = false;
    return (zero && b[0] == '-') ? std::string(b + 1) : std::string(b);
}

static const char *const kDays[7] = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
static const char *const kMonths[12] = {"January", "February", "March",     "April",   "May",      "June",
                                        "July",    "August",   "September", "October", "November", "December"};

std::string formatTime(const char *fmt, const struct tm &tm)
{
    std::string o;
    char b[24];
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') { o += *p; continue; }
        char c = *++p;
        if (!c) break;
        int wd = tm.tm_wday < 0 || tm.tm_wday > 6 ? 0 : tm.tm_wday;
        int mo = tm.tm_mon < 0 || tm.tm_mon > 11 ? 0 : tm.tm_mon;
        switch (c) {
        case 'H': snprintf(b, sizeof b, "%02d", tm.tm_hour); o += b; break;
        case 'M': snprintf(b, sizeof b, "%02d", tm.tm_min); o += b; break;
        case 'S': snprintf(b, sizeof b, "%02d", tm.tm_sec); o += b; break;
        case 'I': snprintf(b, sizeof b, "%02d", tm.tm_hour % 12 == 0 ? 12 : tm.tm_hour % 12); o += b; break;
        case 'p': o += tm.tm_hour >= 12 ? "PM" : "AM"; break;
        case 'A': o += kDays[wd]; break;
        case 'a': o += std::string(kDays[wd], 3); break;
        case 'd': snprintf(b, sizeof b, "%02d", tm.tm_mday); o += b; break;
        case 'e': snprintf(b, sizeof b, "%2d", tm.tm_mday); o += b; break;
        case 'B': o += kMonths[mo]; break;
        case 'b': o += std::string(kMonths[mo], 3); break;
        case 'm': snprintf(b, sizeof b, "%02d", tm.tm_mon + 1); o += b; break;
        case 'y': snprintf(b, sizeof b, "%02d", (tm.tm_year + 1900) % 100); o += b; break;
        case 'Y': snprintf(b, sizeof b, "%d", tm.tm_year + 1900); o += b; break;
        case 'j': snprintf(b, sizeof b, "%03d", tm.tm_yday + 1); o += b; break;
        case '%': o += '%'; break;
        default: break;  // the compiler/decoder rejects anything else
        }
    }
    return o;
}

std::string formatTimePlaceholder(const char *fmt)
{
    std::string o;
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') { o += *p; continue; }
        if (!*++p) break;
        o += *p == '%' ? "%" : "--";
    }
    return o;
}

static std::string valueText(const Value &v, uint8_t spec)
{
    if (v.kind == Value::NUM) return formatNumber(v.num, spec);
    if (v.kind == Value::STR) return v.str;
    return "--";
}

std::string expandTemplate(const Layout &l, const char *tpl, const Data &data)
{
    std::string o;
    for (const uint8_t *p = (const uint8_t *)tpl; *p; p++) {
        if (*p != TOK) { o += (char)*p; continue; }
        if (!p[1] || !p[2] || !p[3] || !p[4]) break;  // truncated token (decoder rejects)
        uint8_t spec = p[4] == 'D' ? DECIMALS_DEFAULT : (uint8_t)(p[4] - '0');
        (void)l;
        o += valueText(data.get((uint8_t)(p[1] - 1), (uint8_t)(p[2] - 1), (uint8_t)(p[3] - 1)), spec);
        p += 4;
    }
    return o;
}

uint8_t weatherIconFor(int c)
{
    if (c == 0 || c == 1) return IC_SUN;
    if (c == 2) return IC_PARTLY;
    if (c == 45 || c == 48) return IC_FOG;
    if ((c >= 51 && c <= 67) || (c >= 80 && c <= 82)) return IC_RAIN;
    if ((c >= 71 && c <= 77) || c == 85 || c == 86) return IC_SNOW;
    if (c >= 95 && c <= 99) return IC_STORM;
    return IC_CLOUD;
}

uint8_t unitFontFor(uint8_t id)
{
    if (id >= FONT_DIGITS_BASE) return (uint8_t)(FONT_BOLD_BASE + 3 + (id - FONT_DIGITS_BASE));  // bold 28/36/48
    uint8_t base = id >= FONT_BOLD_BASE ? FONT_BOLD_BASE : FONT_REGULAR_BASE;
    uint8_t i = id - base;
    return (uint8_t)(base + (i >= 2 ? i - 2 : 0));
}

// ---------------------------------------------------------------------------
// text
namespace {

inline uint8_t contrast(uint8_t c) { return c >= 2 ? 0 : 3; }

int glyphAdv(const Font &f, uint8_t ch)
{
    const Glyph *g = f.find(ch);
    if (g) return g->adv;
    const Glyph *sp = f.find(' ');
    return sp ? sp->adv : f.px * 6 / 10;
}

int textW(const Font &f, const std::string &s)
{
    int w = 0;
    for (unsigned char c : s) w += glyphAdv(f, c);
    return w;
}

void drawGlyphs(Canvas &cv, const Font &f, const std::string &s, int x, int baseline, uint8_t lv)
{
    for (unsigned char c : s) {
        const Glyph *g = f.find(c);
        if (g && g->w) cv.blit1(f.bitmap + g->off, (g->w + 7) / 8, x + g->xo, baseline + g->yo, g->w, g->h, lv);
        x += glyphAdv(f, c);
    }
}

std::string ellipsize(const Font &f, const std::string &s, int maxW)
{
    if (textW(f, s) <= maxW) return s;
    const std::string dots = f.find('.') ? "..." : "";
    std::string o = s;
    while (!o.empty() && textW(f, o + dots) > maxW) o.pop_back();
    while (!o.empty() && o.back() == ' ') o.pop_back();
    return o + dots;
}

struct Box { int x, y, w, h; };

void splitWords(const Font &f, const std::string &s, int maxW, std::vector<std::string> &lines)
{
    std::string cur, word;
    auto pushWord = [&]() {
        if (word.empty()) return;
        std::string cand = cur.empty() ? word : cur + " " + word;
        if (textW(f, cand) <= maxW) { cur = cand; word.clear(); return; }
        if (!cur.empty()) { lines.push_back(cur); cur.clear(); }
        // word longer than the line: split by characters
        while (!word.empty() && textW(f, word) > maxW) {
            size_t k = 1;
            while (k < word.size() && textW(f, word.substr(0, k + 1)) <= maxW) k++;
            lines.push_back(word.substr(0, k));
            word.erase(0, k);
        }
        cur = word;
        word.clear();
    };
    for (char c : s) {
        if (c == ' ') pushWord();
        else word += c;
    }
    pushWord();
    if (!cur.empty()) lines.push_back(cur);
}

void drawTextBlock(Canvas &cv, const Font &f, const std::string &text, const Box &b, uint8_t halign, uint8_t valign,
                   bool clip, bool wrap, int maxLines, uint8_t lv)
{
    std::vector<std::string> lines;
    if (wrap) {
        splitWords(f, text, b.w, lines);
        int cap = maxLines > 0 ? maxLines : (b.h / f.lineH > 0 ? b.h / f.lineH : 1);
        if ((int)lines.size() > cap) {
            lines.resize(cap);
            if (!clip) lines.back() = ellipsize(f, lines.back() + "...", b.w);
        }
    } else {
        lines.push_back(clip ? text : ellipsize(f, text, b.w));
    }
    int total = (int)lines.size() * f.lineH;
    int y = b.y + (valign == VA_MIDDLE ? (b.h - total) / 2 : valign == VA_BOTTOM ? b.h - total : 0);
    for (const std::string &ln : lines) {
        int w = textW(f, ln);
        int x = b.x + (halign == AL_CENTER ? (b.w - w) / 2 : halign == AL_RIGHT ? b.w - w : 0);
        if (w > b.w) x = b.x;  // clipped overflow starts at the left edge
        drawGlyphs(cv, f, ln, x, y + f.ascent, lv);
        y += f.lineH;
    }
}

// ---------------------------------------------------------------------------
// icons: drawn on a 64-unit grid scaled to the box
struct IconCtx {
    Canvas &cv;
    int cx, cy, s;
    uint8_t lv, bg;
    int S(int v) const { return v * s / 64; }
    int T(int v) const { int t = S(v); return t < 1 ? 1 : t; }
};

void cloud(const IconCtx &c, int ox, int oy, bool halo)
{
    // pieces relative to (cx+ox, cy+oy): three lobes on a slab
    struct P { int x, y, r; };
    const P lobes[3] = {{-14, 4, 11}, {1, -3, 15}, {16, 4, 11}};
    int x0 = c.cx + c.S(ox), y0 = c.cy + c.S(oy);
    for (int pass = halo ? 0 : 1; pass < 2; pass++) {
        uint8_t lv = pass == 0 ? c.bg : c.lv;
        int g = pass == 0 ? c.T(3) : 0;
        for (const P &p : lobes) c.cv.fillCircle(x0 + c.S(p.x), y0 + c.S(p.y), c.S(p.r) + g, lv);
        c.cv.fillRect(x0 + c.S(-14) - g, y0 + c.S(4) - g, c.S(30) + 2 * g, c.S(12) + 2 * g, lv);
    }
}

void sun(const IconCtx &c, int ox, int oy, int r, int inner, int outer)
{
    int x = c.cx + c.S(ox), y = c.cy + c.S(oy);
    c.cv.fillCircle(x, y, c.S(r), c.lv);
    static const int dx[8] = {1, 1, 0, -1, -1, -1, 0, 1}, dy[8] = {0, 1, 1, 1, 0, -1, -1, -1};
    for (int i = 0; i < 8; i++) {
        double k = (i & 1) ? 0.7071 : 1.0;
        c.cv.line(x + (int)lround(dx[i] * c.S(inner) * k), y + (int)lround(dy[i] * c.S(inner) * k),
                  x + (int)lround(dx[i] * c.S(outer) * k), y + (int)lround(dy[i] * c.S(outer) * k), c.T(3), c.lv);
    }
}

void drawIcon(Canvas &cv, uint8_t icon, int x, int y, int w, int h, uint8_t lv)
{
    int s = w < h ? w : h;
    IconCtx c{cv, x + w / 2, y + h / 2, s, lv, contrast(lv)};
    switch (icon) {
    case IC_SUN: sun(c, 0, 0, 13, 20, 29); break;
    case IC_PARTLY:
        sun(c, -9, -10, 10, 15, 22);
        cloud(c, 6, 6, true);
        break;
    case IC_CLOUD: cloud(c, 0, -2, false); break;
    case IC_RAIN:
        cloud(c, 0, -12, false);
        for (int i = 0; i < 3; i++)
            cv.line(c.cx + c.S(-14 + i * 14), c.cy + c.S(10), c.cx + c.S(-18 + i * 14), c.cy + c.S(24), c.T(3), lv);
        break;
    case IC_SNOW:
        cloud(c, 0, -12, false);
        for (int i = 0; i < 3; i++) {
            cv.fillCircle(c.cx + c.S(-14 + i * 14), c.cy + c.S(14 + (i & 1) * 8), c.T(3), lv);
        }
        break;
    case IC_STORM:
        cloud(c, 0, -12, false);
        cv.line(c.cx + c.S(3), c.cy + c.S(6), c.cx + c.S(-5), c.cy + c.S(18), c.T(4), lv);
        cv.line(c.cx + c.S(-5), c.cy + c.S(18), c.cx + c.S(5), c.cy + c.S(18), c.T(4), lv);
        cv.line(c.cx + c.S(5), c.cy + c.S(18), c.cx + c.S(-3), c.cy + c.S(30), c.T(4), lv);
        break;
    case IC_FOG:
        for (int i = 0; i < 4; i++) {
            int len = i & 1 ? 40 : 52;
            cv.fillRect(c.cx - c.S(len / 2) + (i & 1 ? c.S(6) : 0), c.cy + c.S(-21 + i * 14), c.S(len), c.T(5), lv);
        }
        break;
    case IC_CLOCK:
        cv.ring(c.cx, c.cy, c.S(28), c.T(4), lv);
        cv.line(c.cx, c.cy, c.cx, c.cy - c.S(17), c.T(4), lv);
        cv.line(c.cx, c.cy, c.cx + c.S(12), c.cy + c.S(6), c.T(4), lv);
        break;
    case IC_GAMEPAD:
        cv.fillCircle(c.cx - c.S(18), c.cy + c.S(2), c.S(12), lv);
        cv.fillCircle(c.cx + c.S(18), c.cy + c.S(2), c.S(12), lv);
        cv.fillRect(c.cx - c.S(18), c.cy - c.S(10), c.S(36), c.S(24), lv);
        cv.fillRect(c.cx - c.S(21), c.cy - c.S(3), c.S(10), c.T(3), c.bg);
        cv.fillRect(c.cx - c.S(17), c.cy - c.S(7), c.T(3), c.S(10), c.bg);
        cv.fillCircle(c.cx + c.S(14), c.cy - c.S(2), c.T(2), c.bg);
        cv.fillCircle(c.cx + c.S(21), c.cy + c.S(3), c.T(2), c.bg);
        break;
    default: break;
    }
}

void drawPlaceholderIcon(Canvas &cv, int x, int y, int w, int h, uint8_t lv)
{
    int s = (w < h ? w : h) / 4;
    int cx = x + w / 2, cy = y + h / 2;
    cv.fillRect(cx - s - s / 2, cy - 1, s, 2, lv);
    cv.fillRect(cx + s / 2, cy - 1, s, 2, lv);
}

// ---------------------------------------------------------------------------
double numOrNan(const Value &v) { return v.kind == Value::NUM ? v.num : NAN; }

struct Local {
    const Layout &l;
    const Data &d;
    const Env &env;
    Canvas &cv;
    uint8_t ink() const { return l.flags & LF_DARK ? 3 : 0; }
};

void drawWidget(Local &L, const Widget &w)
{
    Canvas &cv = L.cv;
    const Box b{w.x, w.y, w.w, w.h};
    cv.setClip(w.x, w.y, w.w, w.h);
    const bool inv = w.flags & WF_INVERT;
    uint8_t fg = w.color;
    if (inv) {
        cv.fillRect(b.x, b.y, b.w, b.h, w.color);
        fg = contrast(w.color);
    }
    const uint8_t ha = w.align & 3, va = (w.align >> 2) & 3;
    const Font *f = fontById(w.font);

    switch (w.type) {
    case W_TEXT:
        if (f) drawTextBlock(cv, *f, expandTemplate(L.l, L.l.str(w.text), L.d), b, ha, va, w.flags & WF_CLIP,
                             w.flags & WF_WRAP, w.maxLines, fg);
        break;
    case W_CLOCK:
        if (f) {
            Value v = L.d.get(w.src, w.field, w.idx);
            const char *fmt = L.l.str(w.text);
            std::string s;
            if (v.kind == Value::TIME) {
                struct tm tm;
                memset(&tm, 0, sizeof tm);
                const Source &src = L.l.src[w.src];
                L.env.localTime((int64_t)v.num, src.s1 == NO_STR ? nullptr : L.l.str(src.s1), tm);
                s = formatTime(fmt, tm);
            } else {
                s = formatTimePlaceholder(fmt);
            }
            drawTextBlock(cv, *f, s, b, ha, va, true, false, 0, fg);
        }
        break;
    case W_NUMBER:
        if (f) {
            Value v = L.d.get(w.src, w.field, w.idx);
            bool ok = v.kind == Value::NUM && isfinite(v.num);
            std::string num = ok ? formatNumber(v.num, w.p0) : "--";
            std::string unit = ok && w.aux != NO_STR ? L.l.str(w.aux) : "";
            const Font *uf = fontById(unitFontFor(w.font));
            int nw = textW(*f, num), uw = (uf && !unit.empty()) ? textW(*uf, unit) + 2 : 0;
            int total = nw + uw;
            int x = b.x + (ha == AL_CENTER ? (b.w - total) / 2 : ha == AL_RIGHT ? b.w - total : 0);
            if (total > b.w) x = b.x;
            int y = b.y + (va == VA_MIDDLE ? (b.h - f->lineH) / 2 : va == VA_BOTTOM ? b.h - f->lineH : 0);
            drawGlyphs(cv, *f, num, x, y + f->ascent, fg);
            if (uw) drawGlyphs(cv, *uf, unit, x + nw + 2, y + f->ascent, fg);
        }
        break;
    case W_BAR: {
        Value v = L.d.get(w.src, w.field, w.idx);
        cv.frame(b.x, b.y, b.w, b.h, 1, fg);
        double val = numOrNan(v);
        if (isfinite(val) && b.w > 4 && b.h > 4) {
            double fr = (val - w.lo) / (double)(w.hi - w.lo);
            fr = fr < 0 ? 0 : fr > 1 ? 1 : fr;
            int iw = b.w - 4, ih = b.h - 4;
            if (w.p0 == 0) cv.fillRect(b.x + 2, b.y + 2, (int)lround(iw * fr), ih, fg);
            else {
                int fh = (int)lround(ih * fr);
                cv.fillRect(b.x + 2, b.y + 2 + ih - fh, iw, fh, fg);
            }
        }
        break;
    }
    case W_ICON:
        if (w.p0 == ICONS_NAMED) {
            drawIcon(cv, w.p1, b.x, b.y, b.w, b.h, fg);
        } else {
            Value v = L.d.get(w.src, w.field, w.idx);
            if (v.kind == Value::NUM && isfinite(v.num))
                drawIcon(cv, weatherIconFor(v.num < -1 ? -1 : v.num > 1000 ? 1000 : (int)v.num), b.x, b.y, b.w, b.h, fg);
            else drawPlaceholderIcon(cv, b.x, b.y, b.w, b.h, fg);
        }
        break;
    case W_IMAGE: {
        const uint8_t *bits = L.l.pool + w.aux;
        if (w.flags & WF_IMG_2BPP) {
            cv.blit2(bits, (w.w + 3) / 4, b.x, b.y, w.w, w.h);
        } else {
            cv.blit1(bits, (w.w + 7) / 8, b.x, b.y, w.w, w.h, 0 + (L.l.flags & LF_DARK ? 3 : 0));
        }
        break;
    }
    default: break;
    }
    if (w.border) cv.frame(b.x, b.y, b.w, b.h, w.border, inv ? contrast(w.color) : w.color);
    cv.resetClip();
}

}  // namespace

void renderLayout(const Layout &l, const Data &data, const Env &env, Canvas &cv)
{
    cv.resetClip();
    cv.clear(l.flags & LF_DARK ? 0 : 3);
    Local L{l, data, env, cv};
    for (int i = 0; i < l.nWidgets; i++) drawWidget(L, l.w[i]);
}

}  // namespace idle

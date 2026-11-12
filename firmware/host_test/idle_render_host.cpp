// Host harness for the idle renderer.
//
//   idle_render_host <layout.bin> <sample.txt> <out.png>
//       decode the layout, render it with the sample data and write the picture the panel
//       would show: mono layouts as a 1-bit PNG (dithered exactly like Canvas::toMono),
//       gray4 layouts as a 2-bit grayscale PNG.
//   idle_render_host --selftest
//       formatting / text / icon / clipping unit checks
//
// sample.txt (one record per line, '#' comments):
//   epoch <seconds>          frozen wall clock (0 = clock not synced)
//   tz <POSIX TZ>            global zone
//   <src> <path> <idx> N <number>
//   <src> <path> <idx> S <text to end of line>
//   <src> <path> <idx> T <epoch>
// <src> is the layout source index, <path> a docs/idle-mode.md field path ("current.temp").
//
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 idle_render_host.cpp ../src/ee05/idle_{render,canvas,
//        layout,fonts_data}.cpp ../src/ee05/usbproto.cpp
#include <assert.h>
#include <math.h>
#include <map>
#include <stdio.h>
#include <string.h>
#include <string>
#include <tuple>
#include <vector>

#include "idle_env.h"
#include "idle_render.h"
#include "usbproto.h"

using namespace idle;

struct MapData : Data {
    std::map<std::tuple<int, int, int>, Value> m;
    Value get(uint8_t s, uint8_t f, uint8_t i) const override
    {
        auto it = m.find(std::make_tuple((int)s, (int)f, (int)i));
        return it == m.end() ? Value() : it->second;
    }
};

// --- minimal PNG writer (stored deflate), bit depth 1 or 2, grayscale ---------------------------
static void put32be(std::vector<uint8_t> &v, uint32_t x)
{
    for (int s = 24; s >= 0; s -= 8) v.push_back((uint8_t)(x >> s));
}
static void chunk(std::vector<uint8_t> &png, const char *type, const std::vector<uint8_t> &data)
{
    put32be(png, (uint32_t)data.size());
    std::vector<uint8_t> td(type, type + 4);
    td.insert(td.end(), data.begin(), data.end());
    png.insert(png.end(), td.begin(), td.end());
    put32be(png, usbproto::crc32(td.data(), td.size()));
}
static uint32_t adler32(const uint8_t *p, size_t n)
{
    uint32_t a = 1, b = 0;
    for (size_t i = 0; i < n; i++) { a = (a + p[i]) % 65521; b = (b + a) % 65521; }
    return (b << 16) | a;
}
static bool writePng(const char *path, int w, int h, int depth, const std::vector<uint8_t> &rows /* h * stride */)
{
    size_t stride = ((size_t)w * depth + 7) / 8;
    std::vector<uint8_t> raw;
    for (int y = 0; y < h; y++) {
        raw.push_back(0);
        raw.insert(raw.end(), rows.begin() + y * stride, rows.begin() + (y + 1) * stride);
    }
    std::vector<uint8_t> z = {0x78, 0x01};
    for (size_t o = 0; o < raw.size() || o == 0; o += 65535) {
        size_t n = raw.size() - o < 65535 ? raw.size() - o : 65535;
        z.push_back(o + n >= raw.size() ? 1 : 0);
        z.push_back(n & 0xFF); z.push_back(n >> 8); z.push_back(~n & 0xFF); z.push_back((~n >> 8) & 0xFF);
        z.insert(z.end(), raw.begin() + o, raw.begin() + o + n);
        if (o + n >= raw.size()) break;
    }
    put32be(z, adler32(raw.data(), raw.size()));
    std::vector<uint8_t> png = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A}, ih;
    put32be(ih, w); put32be(ih, h);
    ih.push_back((uint8_t)depth); ih.push_back(0); ih.push_back(0); ih.push_back(0); ih.push_back(0);
    chunk(png, "IHDR", ih);
    chunk(png, "IDAT", z);
    chunk(png, "IEND", {});
    FILE *f = fopen(path, "wb");
    if (!f) return false;
    fwrite(png.data(), 1, png.size(), f);
    fclose(f);
    return true;
}

static std::vector<uint8_t> readFile(const char *p)
{
    std::vector<uint8_t> v;
    FILE *f = fopen(p, "rb");
    if (!f) return v;
    uint8_t b[4096];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f)) > 0) v.insert(v.end(), b, b + n);
    fclose(f);
    return v;
}

static bool loadSample(const char *path, const Layout &l, PosixEnv &env, MapData &d)
{
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (!n || line[0] == '#') continue;
        if (!strncmp(line, "epoch ", 6)) { env.fixedEpoch = atoll(line + 6); continue; }
        if (!strncmp(line, "tz ", 3)) { static char tz[512]; snprintf(tz, sizeof tz, "%s", line + 3); env.globalTz = tz; continue; }
        int src, idx, off = 0;
        char path_[64], kind;
        if (sscanf(line, "%d %63s %d %c %n", &src, path_, &idx, &kind, &off) < 4) { fprintf(stderr, "bad line: %s\n", line); return false; }
        if (src < 0 || src >= l.nSources) { fprintf(stderr, "bad source %d\n", src); return false; }
        const FieldInfo *fi = findPath(l.src[src].type, path_);
        if (!fi) { fprintf(stderr, "unknown path %s\n", path_); return false; }
        const char *rest = line + off;
        Value v = kind == 'N' ? Value::number(atof(rest)) : kind == 'T' ? Value::time(atoll(rest)) : Value::text(rest);
        d.m[std::make_tuple(src, (int)fi->id, idx)] = v;
    }
    fclose(f);
    return true;
}

static int renderCmd(const char *layoutPath, const char *samplePath, const char *outPath)
{
    std::vector<uint8_t> bin = readFile(layoutPath);
    Layout l;
    DecodeError e = decodeLayout(bin.data(), bin.size(), l);
    if (e != DEC_OK) { fprintf(stderr, "decode failed: %s\n", decodeErrorName(e)); return 1; }
    PosixEnv env;
    MapData d;
    if (!loadSample(samplePath, l, env, d)) return 1;
    // clock source: epoch from the sample's "epoch" line, exposed as field 0 of every clock source
    for (int s = 0; s < l.nSources; s++)
        if (l.src[s].type == SRC_CLOCK && env.fixedEpoch) d.m[std::make_tuple(s, 0, 0)] = Value::time(env.fixedEpoch);
    Canvas cv;
    renderLayout(l, d, env, cv);
    std::vector<uint8_t> rows;
    if (l.mode == RM_MONO) {
        rows.resize(MONO_BYTES);
        cv.toMono(rows.data());
        for (auto &b : rows) b = (uint8_t)~b;  // PNG gray: 1 = white
        return writePng(outPath, SCREEN_W, SCREEN_H, 1, rows) ? 0 : 1;
    }
    rows.assign(cv.gray(), cv.gray() + GRAY_BYTES);  // same packing: 4 px/byte MSB first, 0 black .. 3 white
    return writePng(outPath, SCREEN_W, SCREEN_H, 2, rows) ? 0 : 1;
}

// ------------------------------------------------------------------------------------------------
static int selftest()
{
    // number formatting
    assert(formatNumber(11.2, DECIMALS_DEFAULT) == "11.2");
    assert(formatNumber(12.0, DECIMALS_DEFAULT) == "12");
    assert(formatNumber(-0.04, DECIMALS_DEFAULT) == "0");   // never "-0"
    assert(formatNumber(-0.4, 0) == "0");
    assert(formatNumber(-3.5, 0) == "-4" || formatNumber(-3.5, 0) == "-3");  // libc rounding, sign kept
    assert(formatNumber(3.14159, 2) == "3.14");
    assert(formatNumber(1234567.0, 0) == "1234567");
    assert(formatNumber(NAN, 0) == "--" && formatNumber(INFINITY, 0) == "--");

    // time formatting
    struct tm tm;
    memset(&tm, 0, sizeof tm);
    tm.tm_year = 2026 - 1900; tm.tm_mon = 10; tm.tm_mday = 3; tm.tm_hour = 0; tm.tm_min = 7; tm.tm_sec = 9;
    tm.tm_wday = 2; tm.tm_yday = 306;
    assert(formatTime("%H:%M:%S", tm) == "00:07:09");
    assert(formatTime("%I:%M %p", tm) == "12:07 AM");
    tm.tm_hour = 13;
    assert(formatTime("%I %p", tm) == "01 PM");
    assert(formatTime("%A, %e %B %Y", tm) == "Tuesday,  3 November 2026");
    assert(formatTime("%a %b %d %m %y %j %%", tm) == "Tue Nov 03 11 26 307 %");
    assert(formatTimePlaceholder("%H:%M") == "--:--" && formatTimePlaceholder("100%%") == "100%");
    tm.tm_wday = 99; tm.tm_mon = 77;  // garbage must not index out of range
    (void)formatTime("%A %B", tm);

    // weather mapping
    assert(weatherIconFor(0) == IC_SUN && weatherIconFor(2) == IC_PARTLY && weatherIconFor(3) == IC_CLOUD);
    assert(weatherIconFor(45) == IC_FOG && weatherIconFor(61) == IC_RAIN && weatherIconFor(81) == IC_RAIN);
    assert(weatherIconFor(73) == IC_SNOW && weatherIconFor(86) == IC_SNOW && weatherIconFor(95) == IC_STORM);
    assert(weatherIconFor(-1) == IC_CLOUD && weatherIconFor(1000) == IC_CLOUD);

    // unit fonts exist for every font id
    for (uint8_t id = 0; id < FONT_SLOTS; id++)
        if (fontById(id)) assert(fontById(unitFontFor(id)));

    // fonts: every id in the wire-format table has data; charset table matches the data
    for (uint8_t id = 0; id < FONT_SLOTS; id++) {
        assert(fontValid(id) == (fontById(id) != nullptr));
        if (!fontById(id)) continue;
        for (int c = 0; c < 256; c++) assert(fontHasChar(id, (uint8_t)c) == (fontById(id)->find((uint8_t)c) != nullptr));
    }

    // canvas primitives
    {
        Canvas cv;
        cv.clear(3);
        cv.fillRect(10, 10, 5, 5, 0);
        assert(cv.get(10, 10) == 0 && cv.get(14, 14) == 0 && cv.get(15, 15) == 3 && cv.get(9, 9) == 3);
        cv.setClip(0, 0, 12, 12);
        cv.fillRect(0, 0, 100, 100, 1);  // clipped
        assert(cv.get(11, 11) == 1 && cv.get(12, 12) == 0 && cv.get(50, 50) == 3);  // outside the clip: untouched
        cv.resetClip();
        cv.fillRect(-5, -5, 10, 10, 2);   // off-screen parts ignored
        assert(cv.get(0, 0) == 2 && cv.get(4, 4) == 2 && cv.get(5, 5) != 2);
        cv.fillRect(SCREEN_W - 2, SCREEN_H - 2, 10, 10, 0);
        assert(cv.get(SCREEN_W - 1, SCREEN_H - 1) == 0);
        cv.put(-1, -1, 0); cv.put(SCREEN_W, 0, 0);  // no crash
        cv.clear(3);
        cv.line(0, 0, 9, 9, 1, 0);
        assert(cv.get(5, 5) == 0 && cv.get(0, 9) == 3);
        cv.fillCircle(100, 100, 5, 0);
        assert(cv.get(100, 100) == 0 && cv.get(100, 105) == 0 && cv.get(100, 107) == 3);
        // mono packing: black = 1, dither for grays
        cv.clear(3);
        cv.fillRect(0, 0, 8, 1, 0);
        std::vector<uint8_t> m(MONO_BYTES);
        cv.toMono(m.data());
        assert(m[0] == 0xFF && m[1] == 0x00 && m[SCREEN_W / 8] == 0x00);
        cv.clear(1);
        cv.toMono(m.data());
        int black = 0;
        for (int x = 0; x < 4; x++) for (int y = 0; y < 4; y++) black += (m[y * (SCREEN_W / 8)] >> (7 - x)) & 1;
        assert(black == 11);  // dark gray: 11/16 of a Bayer cell
    }
    puts("idle_render selftest OK");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--selftest")) return selftest();
    if (argc == 4) return renderCmd(argv[1], argv[2], argv[3]);
    fprintf(stderr, "usage: idle_render_host <layout.bin> <sample.txt> <out.png> | --selftest\n");
    return 2;
}

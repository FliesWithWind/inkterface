// Fast single-phase partial refresh for the UC8179 5.83" 648x480 panel (EE05).
//
// Pure C++11, no Arduino: the waveform/window math lives here so it is unit tested on
// the host (firmware/host_test/fastpartial_host.cpp). The SPI driver is uc8179_fast.h.
// Background and measurements: docs/partial-refresh.md.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace fastpartial {

constexpr uint16_t PANEL_W = 648;
constexpr uint16_t PANEL_H = 480;
constexpr size_t STRIDE = PANEL_W / 8;
constexpr size_t LUT_LEN = 42;  // 7 groups x (pattern + 4 phase frame counts + repeat): KW mode, p.22-24

// UC8179 source levels for one LUT phase (2 bits each, 4 phases per group byte): GND/VDH/VDL/VDHR, p.22-23.
enum Level : uint8_t { GND = 0, VDH = 1, VDL = 2, VDHR = 3 };

// Waveform for the two transitions that change the picture. Black->white is driven
// with VDL, white->black with VDH. The datasheet fixes the LUT format (p.22-24) but not which
// way a voltage moves the pigment: this polarity is the one validated on the panel.
// Our own waveform, not from the datasheet: docs/uc8179-tables.md section 4.
// Unchanged pixels (WW, KK) and the border get an all-zero LUT: no drive at all.
struct Params {
    uint8_t pre;    // frames driven toward the OLD colour first (0 = none)
    uint8_t drive;  // frames driven toward the NEW colour
    constexpr Params(uint8_t pre_ = 0, uint8_t drive_ = 20) : pre(pre_), drive(drive_) {}
};

// Reference for the "before" numbers: the stock partial waveform of the library we replaced
// is 70 frames (T1=30, T2=5, T3=30, T4=5), i.e. ~1.5 s of drive. Not used by the firmware.
constexpr Params STOCK = Params(35, 35);

// Shipped default: 20 frames x 20.35 ms = 407 ms of drive, no pre phase (0.67 s per
// refresh). Build with -D INK_PARTIAL_DRIVE=<frames> to trade contrast against time;
// docs/partial-refresh.md has the sweep.
#ifndef INK_PARTIAL_DRIVE
#define INK_PARTIAL_DRIVE 20
#endif
constexpr Params FAST = Params(0, INK_PARTIAL_DRIVE);

// VCOM_DC (command 0x82, VDCS[6:0], 0.05 V steps, 0x00 = -0.1 V, 0x26 = -2.0 V; datasheet p.32) for the register-LUT partial
// refresh. Our waveform leaves unchanged pixels (WW, KK) at source GND for the whole refresh while VCOM sits at VCOM_DC, so
// the pixels the window only happens to cover see (0 V - VCOM_DC) the entire time. At -2.0 V that is a steady +2 V toward
// black on every white pixel inside a window, and it makes the two drive directions unequal (W->K 17 V, K->W 13 V). Over
// hundreds of partials this is the gray block / haze around updating values. -0.1 V is the closest to neutral the register
// reaches. Build with -D INK_PARTIAL_VCOM=0x26 for the old value (A/B on the glass). Numbers: docs/partial-refresh.md.
#ifndef INK_PARTIAL_VCOM
#define INK_PARTIAL_VCOM 0x00
#endif
constexpr uint8_t VCOM_DC_REG = INK_PARTIAL_VCOM;

inline uint8_t phasePattern(uint8_t a, uint8_t b, uint8_t c, uint8_t d)
{
    return (uint8_t)((a & 3) << 6 | (b & 3) << 4 | (c & 3) << 2 | (d & 3));
}

// One transition LUT: optional pre phase toward `from`, then the drive toward `to`.
// Zero-length phases are dropped (compacted) rather than relied on to be skipped.
inline void buildTransitionLut(Level from, Level to, const Params &p, uint8_t out[LUT_LEN])
{
    memset(out, 0, LUT_LEN);
    uint8_t lv[4] = {0, 0, 0, 0}, fr[4] = {0, 0, 0, 0};
    int n = 0;
    if (p.pre) { lv[n] = from; fr[n++] = p.pre; }
    if (p.drive) { lv[n] = to; fr[n++] = p.drive; }
    if (!n) return;
    out[0] = phasePattern(lv[0], lv[1], lv[2], lv[3]);  // p.22: level select 0..3, phase 0 in bits 7:6
    for (int i = 0; i < 4; i++) out[1 + i] = fr[i];     // frames per phase
    out[5] = 1;  // times to repeat; measured as one pass (docs/uc8179-tables.md section 3)
}

// lut[0]=0x20 VCOM, [1]=0x21 WW, [2]=0x22 KW (black->white), [3]=0x23 WK, [4]=0x24 KK,
// [5]=0x25 border. VCOM/WW/KK/border stay zero.
inline void buildLuts(const Params &p, uint8_t lut[6][LUT_LEN])
{
    memset(lut, 0, 6 * LUT_LEN);
    buildTransitionLut(VDH, VDL, p, lut[2]);  // K->W: pre toward black (VDH), drive to white (VDL)
    buildTransitionLut(VDL, VDH, p, lut[3]);  // W->K
}

// Controller timing measured on the EE05 (docs/partial-refresh.md): one frame at the
// 0x30 power-on default is 20.35 ms and every refresh runs 3 extra frames.
constexpr uint32_t FRAME_US = 20350;
constexpr uint32_t OVERHEAD_FRAMES = 3;
inline uint32_t refreshUs(const Params &p) { return ((uint32_t)p.pre + p.drive + OVERHEAD_FRAMES) * FRAME_US; }

// Byte-aligned window as the controller wants it (0x90): x snapped down to 8, the right
// edge snapped up to the next byte boundary, inclusive end coordinates.
struct Window {
    uint16_t x8, w8, y, h;  // w8 = byte-aligned width in pixels
    uint16_t xe, ye;        // inclusive end (xe = x8 + w8 - 1)
    size_t rowBytes() const { return w8 / 8; }
};

inline bool alignWindow(uint16_t x, uint16_t y, uint16_t w, uint16_t h, Window &o)
{
    if (!w || !h || (uint32_t)x + w > PANEL_W || (uint32_t)y + h > PANEL_H) return false;
    o.x8 = x & 0xFFF8;
    o.w8 = (uint16_t)(((x + w + 7) & 0xFFF8) - o.x8);
    o.y = y;
    o.h = h;
    o.xe = (uint16_t)(o.x8 + o.w8 - 1);
    o.ye = (uint16_t)(y + h - 1);
    return true;
}

// Running median / p90 of up to N samples (bench + tests). Nearest-rank percentile.
template <size_t N> struct Stats {
    uint32_t v[N];
    size_t n = 0;
    void add(uint32_t x) { if (n < N) v[n++] = x; }
    uint32_t percentile(unsigned pct) const
    {
        if (!n) return 0;
        uint32_t s[N];
        memcpy(s, v, n * sizeof(uint32_t));
        for (size_t i = 1; i < n; i++)  // insertion sort, N is tiny
            for (size_t j = i; j > 0 && s[j - 1] > s[j]; j--) { uint32_t t = s[j]; s[j] = s[j - 1]; s[j - 1] = t; }
        size_t rank = (pct * n + 99) / 100;  // ceil(pct/100 * n)
        return s[(rank ? rank : 1) - 1];
    }
};

}  // namespace fastpartial

// Model of the UC8179 on the EE05 glass, driven by the real firmware code: usbproto.cpp (host stream -> REFRESH) and
// uc8179_fast.h (REFRESH -> SPI bytes) run unmodified; only SPI/GPIO are fakes, and the fake SPI is this model.
//
//   panel_model_host [--eff 0..100] < host_bytes
//
// The model keeps what the controller keeps: the OLD (0x10) and NEW (0x13) RAM planes, the 0x91/0x90/0x92 window, N2OCP, and for
// every pixel a reflectance 0 (black) .. 255 (white). A refresh moves it like the waveform does:
//   partial (PSR REG=1): pixels with OLD != NEW are moved by `eff` percent of the remaining distance to the new colour. Pixels
//                        with OLD == NEW get LUT WW / KK. "Not driven" is not "no field": for every pixel the model also adds up
//                        the dose = sum over the frames of (source level - VCOM), in V*frames, from the LUT bytes, the PWR and
//                        VDCS registers the driver really wrote (positive = toward black). A pixel left alone should see ~0.
//   full (PSR REG=0, OTP): every pixel of the panel is driven to the new colour (the OTP set also re-drives WW/KK).
// Driving levels: source 00 GND, 01 VDH, 10 VDL (11 VDHR, 3 V, not used in KW mode); VDH/VDL = 2.4 V + 0.2 V * level (0x3A = 14.0 V,
// 0x3F = 15.0 V); VCOM_DC = -0.1 V - 0.05 V * VDCS (0x26 = -2.0 V) (UC8179 datasheet p.18, p.32); the VCOM LUT is all zero in our
// waveform, so VCOM stays at VCOM_DC for the whole refresh. Every refresh lasts max(LUT frames) + 3 frames (docs/partial-refresh.md).
// Last line: DOSE ww= kw= wk= kk= (V*frames per refresh for a pixel left white, black->white, white->black, left black).
// A full swing (wk) is ~300; a pixel that is left alone should see ~0. The dose is a field integral, not an optical model: how much
// of it the pigment answers to (threshold, saturation) is a property of the glass, which nothing here can read back.
// At every REFRESH it checks the invariants the driver relies on, and prints one line per refresh and a SUMMARY line:
//   stale_old   pixels in the refresh window whose OLD RAM differs from what the glass shows (the controller would pick the wrong
//               transition for them: this is how an undrawn leftover is born)
//   fb_diff     pixels whose glass colour differs from the firmware framebuffer after the refresh
//   gray        pixels whose reflectance is more than 12 % away from pure black/white after the refresh
// Build: see decky/tests/test_panel_model.py
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

#include <Arduino.h>
#include <SPI.h>

#include "uc8179_fast.h"
#include "usbproto.h"

using fastpartial::PANEL_H;
using fastpartial::PANEL_W;
using fastpartial::STRIDE;

static int g_dc = HIGH, g_cs = HIGH;
void digitalWrite(int pin, int level)
{
    if (pin == EPD_DC) g_dc = level;
    if (pin == EPD_CS) g_cs = level;
}

struct Model {
    uint8_t ramOld[PANEL_H][PANEL_W], ramNew[PANEL_H][PANEL_W];  // 1 = black (RAM polarity undone)
    uint8_t refl[PANEL_H][PANEL_W];                              // 0 black .. 255 white
    int eff = 100;                                               // percent of the remaining distance a drive covers
    bool inWindow = false;
    int hs = 0, he = PANEL_W - 1, vs = 0, ve = PANEL_H - 1;
    int cmd = -1;
    uint8_t lut[6][42] = {};
    uint8_t pwr[4] = {0x07, 0x07, 0x3a, 0x3a};  // power-on defaults (p.17-18)
    uint8_t vdcs = 0x00;
    double doseGroup[4] = {0, 0, 0, 0};          // last partial: WW, KW, WK, KK
    double doseMaxWW = 0, doseMaxKK = 0;
    std::vector<uint8_t> data;
    uint8_t psr = 0, cdi = 0;
    // RAM write cursor
    int cx = 0, cy = 0;
    // results
    long refreshes = 0, fulls = 0, staleTotal = 0, fbDiffTotal = 0, grayTotal = 0, staleMax = 0, fbDiffMax = 0, grayMax = 0;
    const uint8_t *fb = nullptr;  // firmware framebuffer (1 = black), set by the panel adapter before each refresh

    Model() { memset(ramOld, 0, sizeof ramOld); memset(ramNew, 0, sizeof ramNew); memset(refl, 255, sizeof refl); }

    void startCmd(uint8_t c) { endCmd(); cmd = c; data.clear(); if (c == 0x10 || c == 0x13) { cx = hs; cy = vs; } }

    void ramByte(uint8_t b, uint8_t (*ram)[PANEL_W])
    {
        for (int k = 7; k >= 0; k--) {
            if (cy <= ve && cx <= he && cx < PANEL_W) ram[cy][cx] = ((b >> k) & 1) ? 0 : 1;  // RAM 1 = white
            cx++;
        }
        if (cx > he) { cx = hs; cy++; }
    }

    void dataByte(uint8_t b)
    {
        if (cmd == 0x10) { ramByte(b, ramOld); return; }
        if (cmd == 0x13) { ramByte(b, ramNew); return; }
        data.push_back(b);
    }

    void endCmd()
    {
        if (cmd == 0x90 && data.size() == 9) {
            hs = ((data[0] << 8) | data[1]) & ~7;
            he = ((data[2] << 8) | data[3]) | 7;
            vs = (data[4] << 8) | data[5];
            ve = (data[6] << 8) | data[7];
        } else if (cmd == 0x91) { inWindow = true; }
        else if (cmd == 0x92) { inWindow = false; hs = 0; he = PANEL_W - 1; vs = 0; ve = PANEL_H - 1; }
        else if (cmd == 0x00 && !data.empty()) psr = data[0];
        else if (cmd >= 0x20 && cmd <= 0x25 && data.size() == 42) memcpy(lut[cmd - 0x20], data.data(), 42);
        else if (cmd == 0x01 && data.size() == 4) memcpy(pwr, data.data(), 4);
        else if (cmd == 0x82 && !data.empty()) vdcs = data[0];
        else if (cmd == 0x50 && !data.empty()) cdi = data[0];
        else if (cmd == 0x12) refresh();
        cmd = -1;
        data.clear();
    }

    void refresh()
    {
        int x0 = inWindow ? hs : 0, x1 = inWindow ? he : PANEL_W - 1, y0 = inWindow ? vs : 0, y1 = inWindow ? ve : PANEL_H - 1;
        bool otp = !(psr & 0x20);
        long stale = 0;
        if (!otp) computeDose();
        for (int y = y0; y <= y1; y++)
            for (int x = x0; x <= x1; x++) {
                bool glassBlack = refl[y][x] < 128;
                if (ramOld[y][x] != glassBlack) stale++;
                int target = ramNew[y][x] ? 0 : 255;
                if (otp || ramOld[y][x] != ramNew[y][x]) refl[y][x] = (uint8_t)(refl[y][x] + (target - (int)refl[y][x]) * (otp ? 100 : eff) / 100);
            }
        if (cdi & 0x08)  // N2OCP: NEW -> OLD after the refresh, inside the window
            for (int y = y0; y <= y1; y++) memcpy(&ramOld[y][x0], &ramNew[y][x0], x1 - x0 + 1);
        refreshes++;
        fulls += otp;
        staleTotal += stale;
        if (stale > staleMax) staleMax = stale;
        lastStale = stale;
        lastOtp = otp;
    }
    static double levelV(int level, double vdh, double vdl) { return level == 1 ? vdh : level == 2 ? -vdl : level == 3 ? 3.0 : 0.0; }
    void computeDose()
    {
        double vdh = 2.4 + 0.2 * (pwr[2] & 0x3f), vdl = 2.4 + 0.2 * (pwr[3] & 0x3f), vcom = -0.1 - 0.05 * (vdcs & 0x7f);
        int frames[4] = {0, 0, 0, 0};
        double sum[4] = {0, 0, 0, 0};  // sum of f * source level
        const int idx[4] = {1, 2, 3, 4};  // 0x21 WW, 0x22 KW, 0x23 WK, 0x24 KK
        int total = 0;
        for (int g = 0; g < 4; g++) {
            const uint8_t *l = lut[idx[g]];
            for (int grp = 0; grp < 7; grp++) {
                const uint8_t *e = l + grp * 6;
                for (int ph = 0; ph < 4; ph++) {
                    int f = e[1 + ph] * e[5];
                    frames[g] += f;
                    sum[g] += f * levelV((e[0] >> (6 - 2 * ph)) & 3, vdh, vdl);
                }
            }
            if (frames[g] > total) total = frames[g];
        }
        total += 3;
        for (int g = 0; g < 4; g++) doseGroup[g] = sum[g] + (total - frames[g]) * 0.0 - total * vcom;
        if (doseGroup[0] > doseMaxWW) doseMaxWW = doseGroup[0];
        if (doseGroup[3] > doseMaxKK) doseMaxKK = doseGroup[3];
    }
    long lastStale = 0;
    bool lastOtp = false;

    // after the driver returned: compare against the firmware framebuffer
    void check(const char *kind, int x, int y, int w, int h)
    {
        long diff = 0, gray = 0;
        for (int yy = 0; yy < PANEL_H; yy++)
            for (int xx = 0; xx < PANEL_W; xx++) {
                bool fbBlack = (fb[yy * STRIDE + xx / 8] >> (7 - xx % 8)) & 1;
                bool glassBlack = refl[yy][xx] < 128;
                if (fbBlack != glassBlack) diff++;
                int r = refl[yy][xx];
                if (r > 31 && r < 224) gray++;
            }
        fbDiffTotal += diff; grayTotal += gray;
        if (diff > fbDiffMax) fbDiffMax = diff;
        if (gray > grayMax) grayMax = gray;
        printf("%s %d %d %d %d stale_old=%ld fb_diff=%ld gray=%ld otp=%d\n", kind, x, y, w, h, lastStale, diff, gray, lastOtp);
    }
};

static Model M;

static void spiByte(uint8_t b)
{
    if (g_cs != LOW) return;
    if (g_dc == LOW) M.startCmd(b);
    else M.dataByte(b);
}
void SPIClass::transfer(uint8_t b) { spiByte(b); }
void SPIClass::writeBytes(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) spiByte(p[i]);
}

static fastpartial::Epd EPD;

struct VecLink : usbproto::Link {
    size_t write(const uint8_t *, size_t n) override { return n; }
};

struct ModelPanel : usbproto::Panel {
    const uint8_t *fbp = nullptr;
    void present(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h, bool full) override
    {
        if (full) EPD.full(fb);
        else EPD.partial(fb, x, y, w, h);
        M.endCmd();
        M.fb = fb;
        M.check(EPD.last.full ? "F" : "P", x, y, w, h);
    }
};

int main(int argc, char **argv)
{
    for (int i = 1; i + 1 < argc; i++)
        if (!strcmp(argv[i], "--eff")) M.eff = atoi(argv[i + 1]);
    static const usbproto::Info info = {"model", 0, 0, 8192};
    VecLink link;
    ModelPanel panel;
    static usbproto::Device dev(link, panel, info);
    EPD.begin();
    EPD.holdMs = 2000;
    uint8_t buf[4096];
    uint32_t now = 0;
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) {
        now += 1;
        dev.onBytes(buf, n, now);
    }
    printf("SUMMARY refreshes=%ld fulls=%ld stale_old_total=%ld stale_old_max=%ld fb_diff_total=%ld fb_diff_max=%ld gray_max=%ld\n",
           M.refreshes, M.fulls, M.staleTotal, M.staleMax, M.fbDiffTotal, M.fbDiffMax, M.grayMax);
    printf("DOSE ww=%.1f kw=%.1f wk=%.1f kk=%.1f\n", M.doseGroup[0], M.doseGroup[1], M.doseGroup[2], M.doseGroup[3]);
    return 0;
}

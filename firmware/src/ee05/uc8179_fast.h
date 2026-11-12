// Bare-metal UC8179 driver: fast windowed partial refresh + OTP full refresh (EE05).
//
// The stock partial waveform of the Arduino e-paper library we started with is 70 frames
// at the 50 Hz controller default = 1.4 s of drive, plus a power on/off cycle and re-init
// on every refresh. Here the waveform is a runtime parameter (fastpartial.h), the old and
// new RAM planes are both written from our own shadow of the glass (nothing depends on
// the controller's new->old copy), and every stage is timed.
//
// Every register and LUT value below is traced to the panel datasheet (see docs/references.md
// for where to get it) or marked as our own tuning in docs/uc8179-tables.md; "p.N" in the comments
// is the datasheet page. host_test/golden/uc8179_sequence.txt pins the bytes on the wire.
#pragma once

#include <Arduino.h>
#include <SPI.h>

#include "fastpartial.h"

namespace fastpartial {


// 4-level gray: no waveform bytes live in this repository. The 4-gray waveform is the panel maker's own, stored in
// the controller's OTP, and is selected the way Good Display's demo for this panel (GDEY0583T81, EPD_Init_4G) does it:
// LUT from OTP (PSR 0x1F), Cascade Setting TSFIX=1 (0xE0 = 0x02: use the forced temperature below instead of the sensor)
// and Force Temperature TS_SET = 0x5F (0xE5). The OTP holds up to 12 waveform sets, one per temperature range
// (UC8179 datasheet, "TEMPERATURE RANGE"); the maker put the 4-gray set behind the forced value 0x5F (0x5A selects their fast
// mode, 0x6E their partial). Both registers are in the UC8179 command table; the value is the maker's convention.
// It is a full-screen refresh, never combined with partials. See docs/gray-waveform.md and docs/uc8179-tables.md section 6.
constexpr uint8_t GRAY_TSFIX = 0x02;      // 0xE0 CCSET: TSFIX=1, CCEN=0
constexpr uint8_t GRAY_TEMP_FORCE = 0x5F; // 0xE5 TSSET: selects the maker's 4-gray OTP waveform
constexpr uint8_t GRAY_CDI = 0x21;        // 0x50 CDI byte 0 (byte 1 is 0x07): BDV=10, N2OCP=0, DDX=01


struct Timing {  // microseconds
    uint32_t init = 0;      // hard reset + register/LUT load (0 when already initialised)
    uint32_t powerOn = 0;   // 0x04 + BUSY (0 when already on)
    uint32_t data = 0;      // window setup + both RAM planes over SPI
    uint32_t refresh = 0;   // 0x12 -> BUSY released
    uint32_t powerOff = 0;
    uint32_t total = 0;
    uint32_t busyStart = 0; // 0x12 -> BUSY asserted (latency)
    bool full = false;      // the refresh that ran was a full one (also when a partial was promoted)
};

class Epd {
  public:
    Timing last;
    Params params = FAST;
    bool keepPowered = false;  // leave the DC-DC on between refreshes (see powerOff())
    // Leave the DC-DC on for this long after a partial refresh, so back-to-back partials skip the 161 ms power-on and the
    // 41 ms power-off (0.68 s -> 0.47 s each); service() turns it off once nothing followed. 0 = off after every refresh.
    uint32_t holdMs = 0;

    void begin()
    {
        pinMode(EPD_CS, OUTPUT);
        pinMode(EPD_DC, OUTPUT);
        pinMode(EPD_RST, OUTPUT);
        pinMode(EPD_BUSY, INPUT);
        digitalWrite(EPD_CS, HIGH);
        SPI.begin(EPD_SCK, -1, EPD_MOSI, EPD_CS);
        invalidate();
    }

    // Forget the controller state and the glass shadow (something else touched the panel).
    // The next partial() then becomes a full refresh.
    // powered_ stays: a DC-DC still on in its hold window is turned off by service(), and every init hard-resets the chip anyway.
    void invalidate() { mode_ = NONE; shownValid_ = false; }

    // What the glass shows (1 = black), or nullptr when unknown.
    const uint8_t *glass() const { return shownValid_ ? shown_ : nullptr; }

    // New waveform: reloaded into the controller on the next partial.
    void setParams(const Params &p) { params = p; if (mode_ == PARTIAL) mode_ = NONE; }

    // Full refresh of `fb` (1 = black) with the controller's own OTP waveform (clears
    // ghosting). Both RAM planes get the image so the next partial starts clean.
    void full(const uint8_t *fb)
    {
        Tx tx;
        Timing t;
        t.full = true;
        uint32_t t0 = micros();
        if (mode_ != FULL) { initFull(); }
        t.init = micros() - t0;
        uint32_t t1 = micros();
        if (!powered_) { cmd(0x04); busyWait(); powered_ = true; }
        t.powerOn = micros() - t1;
        uint32_t t2 = micros();
        plane(0x10, fb, 0, 0, PANEL_W, PANEL_H);
        plane(0x13, fb, 0, 0, PANEL_W, PANEL_H);
        memcpy(shown_, fb, sizeof(shown_));
        shownValid_ = true;
        t.data = micros() - t2;
        uint32_t t3 = micros();
        cmd(0x12);
        t.busyStart = awaitBusy();
        t.refresh = micros() - t3;
        finish(t, t0);
    }

    // Partial refresh of the window (x, w snapped out to 8 px). Drives only pixels where
    // fb differs from what is on the glass; everything else gets a zero LUT.
    bool partial(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h)
    {
        Window win;
        if (!alignWindow(x, y, w, h, win)) return false;
        if (!shownValid_) { full(fb); return true; }  // unknown glass: old plane would be wrong
        Tx tx;
        Timing t;
        uint32_t t0 = micros();
        if (mode_ != PARTIAL) { initPartial(); }
        t.init = micros() - t0;
        uint32_t t1 = micros();
        if (!powered_) { cmd(0x04); busyWait(); powered_ = true; }
        t.powerOn = micros() - t1;
        uint32_t t2 = micros();
        plane(0x10, shown_, win.x8, win.y, win.w8, win.h);  // old = what the glass shows
        plane(0x13, fb, win.x8, win.y, win.w8, win.h);      // new
        for (uint16_t r = 0; r < win.h; r++)                          // glass will show fb
            memcpy(shown_ + (size_t)(win.y + r) * STRIDE + win.x8 / 8,
                   fb + (size_t)(win.y + r) * STRIDE + win.x8 / 8, win.rowBytes());
        t.data = micros() - t2;
        uint32_t t3 = micros();
        cmd(0x91);
        setWindow(win);
        cmd(0x12);
        t.busyStart = awaitBusy();
        cmd(0x92);
        t.refresh = micros() - t3;
        finish(t, t0);
        return true;
    }

    // Static 4-level gray full refresh. `gray2`: 2 bits per pixel, 4 px per byte MSB first, row
    // major, 0 black .. 3 white (idle_canvas.h). Plane 0x13 (NEW) = high bit, 0x10 (OLD) = low bit.
    void gray4(const uint8_t *gray2)
    {
        Tx tx;
        Timing t;
        uint32_t t0 = micros();
        initGray();
        t.init = micros() - t0;
        uint32_t t1 = micros();
        // E0/E5 go BEFORE PON: PON senses the temperature once, before the booster is enabled (GDEY0583T81 datasheet p.13), so the
        // forced temperature has to be in place already. Sending them after PON (Good Display's demo order) leaves level 1 striped.
        selectGrayWaveform();
        if (!powered_) {
            cmd(0x04);
            busyWait();
            powered_ = true;
        }
        t.powerOn = micros() - t1;
        uint32_t t2 = micros();
        grayPlane(0x10, gray2, false);  // OTP waveform: OLD = low bit, NEW = high bit (the maker's demo)
        grayPlane(0x13, gray2, true);
        t.data = micros() - t2;
        uint32_t t3 = micros();
        cmd(0x12);
        t.busyStart = awaitBusy();
        t.refresh = micros() - t3;
        shownValid_ = false;  // the glass now holds gray: the next mono partial must be a full refresh
        mode_ = NONE;         // LUTs in the controller are the gray ones
        finish(t, t0);
    }


    // Turn the DC-DC off (the panel should not sit powered between updates).
    void powerOff()
    {
        if (!powered_) return;
        Tx tx;
        uint32_t t = micros();
        cmd(0x02);
        busyWait();
        powered_ = false;
        offDue_ = false;
        last.powerOff = micros() - t;
    }

    // Call from the main loop: ends the hold window of the last partial (see holdMs).
    void service()
    {
        if (offDue_ && (int32_t)(millis() - offAt_) >= 0) powerOff();
    }

    void sleep() { powerOff(); mode_ = NONE; }

    // Escape hatch for the bench: raw command + data bytes.
    void raw(uint8_t c, const uint8_t *d, size_t n)
    {
        Tx tx;
        cmd(c);
        for (size_t i = 0; i < n; i++) dat(d[i]);
    }

    // True while BUSY is asserted (active low).
    static bool busy() { return digitalRead(EPD_BUSY) == LOW; }

  private:
    enum Mode : uint8_t { NONE, FULL, PARTIAL };
    Mode mode_ = NONE;
    bool powered_ = false;
    bool shownValid_ = false;
    bool offDue_ = false;     // a hold window (holdMs) is running: service() powers off at offAt_
    uint32_t offAt_ = 0;
    uint8_t shown_[PANEL_H * STRIDE] = {};  // what the glass currently shows (1 = black)

    // The SPI transaction is taken per call: other code shares the bus, never hold it across calls.
    struct Tx {
        Tx() { SPI.beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0)); }
        ~Tx() { SPI.endTransaction(); }
    };

    void finish(Timing &t, uint32_t t0)
    {
        if (holdMs && !keepPowered && mode_ == PARTIAL) {   // after a full/gray refresh the next refresh re-inits (hard reset): nothing to hold for
            offAt_ = millis() + holdMs;
            offDue_ = true;
        } else if (!keepPowered) {
            uint32_t p = micros();
            cmd(0x02);
            busyWait();
            powered_ = false;
            offDue_ = false;
            t.powerOff = micros() - p;
        }
        t.total = micros() - t0;
        last = t;
    }

    static void cmd(uint8_t c)
    {
        digitalWrite(EPD_DC, LOW);
        digitalWrite(EPD_CS, LOW);
        SPI.transfer(c);
        digitalWrite(EPD_CS, HIGH);
    }
    static void dat(uint8_t d)
    {
        digitalWrite(EPD_DC, HIGH);
        digitalWrite(EPD_CS, LOW);
        SPI.transfer(d);
        digitalWrite(EPD_CS, HIGH);
    }

    // Wait for BUSY to release (it is asserted a few hundred us after 0x12 / 0x04 / 0x02).
    static void busyWait(uint32_t timeoutMs = 20000)
    {
        uint32_t s = millis();
        delayMicroseconds(500);
        while (busy()) {
            if (millis() - s > timeoutMs) break;
            delayMicroseconds(200);
        }
    }

    // After 0x12: returns the latency until BUSY asserted, then waits for release.
    static uint32_t awaitBusy(uint32_t timeoutMs = 20000)
    {
        uint32_t s = micros();
        uint32_t lat = 0;
        while (!busy() && micros() - s < 5000) {}
        lat = micros() - s;
        uint32_t ms = millis();
        while (busy()) {
            if (millis() - ms > timeoutMs) break;
            delayMicroseconds(200);
        }
        return lat;
    }

    static void reset()
    {
        digitalWrite(EPD_RST, LOW);
        delay(10);
        digitalWrite(EPD_RST, HIGH);
        delay(10);
    }

    // Registers shared by all modes. Per-value sources: docs/uc8179-tables.md section 1.
    // `drive15V` false skips the PWR write: the controller keeps its power-on (OTP-loaded) drive voltages. The maker's own demo
    // does that, and the OTP 4-gray waveform was made for those voltages (gray mode only; the others run the 15 V tuning below).
    void initCommon(uint8_t panelSetting, bool drive15V = true)
    {
        reset();  // RES# low 10 ms, high 10 ms (p.39 asks > 50 us / > 1 ms)
        powered_ = false;
        if (drive15V) {
            cmd(0x01);  // PWR (p.14, p.17-18). Bytes: BD_EN=0 VSR_EN=1 VS_EN=1 VG_EN=1 (default 07);
                        // VCOM_SLEW=0 + VG_LVL=111 = VGH +20 V, VGL -20 V (VG_LVL default; the slew bit is "fixed at 1", p.17);
                        // VDH_LVL=111111 = +15.0 V and VDL_LVL=111111 = -15.0 V (p.18). Our tuning: default is 3A = +/-14.0 V.
            dat(0x07); dat(0x07); dat(0x3f); dat(0x3f);
        }
        cmd(0x00);  // PSR (p.14, p.17): bit 5 REG (0 OTP LUT, 1 register LUT), bit 4 KW/R=1 (B/W only), UD=1 SHL=1 SHD_N=1 RST_N=1
        dat(panelSetting);
        cmd(0x61);  // TRES (p.30): HRES=648 (0x288), VRES=480 (0x1E0)
        dat(PANEL_W >> 8); dat(PANEL_W & 0xFF); dat(PANEL_H >> 8); dat(PANEL_H & 0xFF);
        cmd(0x15); dat(0x00);  // DUSPI (p.21): single SPI, the default
        cmd(0x60); dat(0x22);  // TCON (p.30): S2G=G2S=12 x 667 ns, the default
    }

    void initFull()
    {
        initCommon(0x1f);  // PSR: LUT from OTP, KW mode
        // CDI (p.27-28): BDV=10 (border follows LUTKW), N2OCP=1 (new->old copy, unused: both planes are always written),
        // DDX=01 (RAM 1 = white, the default), interval 7 = 10 hsync (default). BDV and N2OCP differ from the defaults.
        cmd(0x50); dat(0x29); dat(0x07);
        mode_ = FULL;
    }

    void initPartial()
    {
        initCommon(0x3f);  // PSR: LUT from registers, KW mode
        cmd(0x82); dat(VCOM_DC_REG);  // VDCS (p.32): VCOM_DC -0.10 V (00, the default) so unchanged pixels (source GND) see ~0 V; see fastpartial.h
        // CDI: BDV=11 (border from LUTBD, all zero below), N2OCP=1 (unused, as in full), DDX=01, interval 7
        cmd(0x50); dat(0x39); dat(0x07);
        uint8_t lut[6][LUT_LEN];
        buildLuts(params, lut);
        for (uint8_t i = 0; i < 6; i++) {
            cmd(0x20 + i);
            digitalWrite(EPD_DC, HIGH);
            digitalWrite(EPD_CS, LOW);
            SPI.writeBytes(lut[i], LUT_LEN);
            digitalWrite(EPD_CS, HIGH);
        }
        mode_ = PARTIAL;
    }

    void initGray()
    {
        initCommon(0x1f, false);  // PSR: LUT from OTP, KW mode; no PWR write (maker's demo does none)
        cmd(0x50); dat(GRAY_CDI); dat(0x07);  // CDI as in the maker's 4-gray demo: BDV=10 (border follows LUTKW), N2OCP=0, DDX=01, interval 7
        mode_ = NONE;
    }

    // Forces the "temperature" that selects the OTP 4-gray waveform (see GRAY_TEMP_FORCE). Sent before PON (see gray4()).
    void selectGrayWaveform()
    {
        cmd(0xE0); dat(GRAY_TSFIX);
        cmd(0xE5); dat(GRAY_TEMP_FORCE);
    }

    static void grayPlane(uint8_t c, const uint8_t *g, bool high)
    {
        cmd(c);
        digitalWrite(EPD_DC, HIGH);
        digitalWrite(EPD_CS, LOW);
        uint8_t row[STRIDE];
        for (uint16_t y = 0; y < PANEL_H; y++) {
            const uint8_t *s = g + (size_t)y * (PANEL_W / 4);
            for (size_t xb = 0; xb < STRIDE; xb++) {
                uint8_t out = 0;
                for (int k = 0; k < 2; k++) {
                    uint8_t b = s[xb * 2 + k];
                    for (int p = 0; p < 4; p++) {
                        uint8_t code = (b >> ((3 - p) * 2)) & 3;
                        out = (uint8_t)((out << 1) | (high ? (code >> 1) & 1 : code & 1));
                    }
                }
                row[xb] = out;
            }
            SPI.writeBytes(row, STRIDE);
        }
        digitalWrite(EPD_CS, HIGH);
    }

    static void setWindow(const Window &w)
    {
        cmd(0x90);
        dat(w.x8 >> 8); dat(w.x8 & 0xFF); dat(w.xe >> 8); dat(w.xe & 0xFF);
        dat(w.y >> 8); dat(w.y & 0xFF); dat(w.ye >> 8); dat(w.ye & 0xFF);
        dat(0x00);
    }

    // Stream a window of a 1=black framebuffer into RAM plane `c` (RAM polarity: 1 = white).
    // Row stride on the wire is the windowed byte width: it MUST be w8/8 (a mismatch smears positioned bitmaps into streaks).
    static void plane(uint8_t c, const uint8_t *src, uint16_t x8, uint16_t y, uint16_t w8, uint16_t h)
    {
        Window win;
        alignWindow(x8, y, w8, h, win);
        cmd(0x91);
        setWindow(win);
        cmd(c);
        digitalWrite(EPD_DC, HIGH);
        digitalWrite(EPD_CS, LOW);
        uint8_t row[STRIDE];
        size_t rb = win.rowBytes();
        for (uint16_t r = 0; r < h; r++) {
            const uint8_t *s = src + (size_t)(y + r) * STRIDE + x8 / 8;
            for (size_t i = 0; i < rb; i++) row[i] = (uint8_t)~s[i];
            SPI.writeBytes(row, rb);
        }
        digitalWrite(EPD_CS, HIGH);
        cmd(0x92);
    }
};

}  // namespace fastpartial

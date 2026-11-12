// Host check of the DC-DC hold window (Epd::holdMs / service()): which commands uc8179_fast.h sends and when.
// decky/tests/test_uc8179_golden.py builds and runs it; exit code 0 = all checks passed.
// Build: g++ -std=c++17 -Ifake -I../src/ee05 -DEPD_SCK=1 -DEPD_MOSI=2 -DEPD_CS=3 -DEPD_DC=4 -DEPD_RST=5 -DEPD_BUSY=6 uc8179_hold_host.cpp
#include <Arduino.h>
#include <SPI.h>
#include <stdio.h>
#include <vector>

#include "uc8179_fast.h"

static int g_dc = HIGH, g_cs = HIGH;
static std::vector<uint8_t> g_cmds;  // command bytes since the last take()

void digitalWrite(int pin, int level)
{
    if (pin == EPD_DC) g_dc = level;
    if (pin == EPD_CS) g_cs = level;
}
void SPIClass::transfer(uint8_t b)
{
    if (g_cs == LOW && g_dc == LOW) g_cmds.push_back(b);
}
void SPIClass::writeBytes(const uint8_t *, size_t) {}

static fastpartial::Epd epd;
static uint8_t fb[fastpartial::PANEL_H * fastpartial::STRIDE];
static uint8_t gray[fastpartial::PANEL_H * fastpartial::PANEL_W / 4];
static int g_fail = 0;

static std::vector<uint8_t> take()
{
    std::vector<uint8_t> v;
    v.swap(g_cmds);
    return v;
}
static int count(const std::vector<uint8_t> &v, uint8_t c)
{
    int n = 0;
    for (uint8_t b : v) n += b == c;
    return n;
}
static void expect(bool ok, const char *what)
{
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail++;
}
// BUSY wait loops advance the fake clock by 700 us per poll; jump it explicitly for the hold window.
static void advance(uint32_t ms) { g_us += ms * 1000; }

int main()
{
    epd.begin();
    epd.holdMs = 2000;

    epd.full(fb);
    auto c = take();
    expect(count(c, 0x02) == 1 && count(c, 0x04) == 1, "full: powers on and off as before");
    epd.service();
    expect(take().empty(), "full: nothing pending for service()");

    fb[100] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    c = take();
    expect(count(c, 0x01) == 1 && count(c, 0x04) == 1 && count(c, 0x12) == 1, "partial 1: re-init, power on, refresh");
    expect(count(c, 0x02) == 0, "partial 1: DC-DC stays on");
    epd.service();
    expect(take().empty(), "service() inside the hold window sends nothing");

    fb[200] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    c = take();
    expect(count(c, 0x01) == 0 && count(c, 0x04) == 0 && count(c, 0x12) == 1, "partial 2: no re-init, no power-on");
    expect(count(c, 0x02) == 0, "partial 2: DC-DC stays on");
    expect(epd.last.powerOff == 0, "partial 2: timing shows no power-off");

    advance(1900);
    epd.service();
    expect(take().empty(), "hold window restarts after every partial (1.9 s after partial 2)");
    advance(200);
    epd.service();
    c = take();
    expect(c.size() == 1 && c[0] == 0x02, "service() powers off once the window is over");
    epd.service();
    expect(take().empty(), "service() powers off only once");

    fb[300] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    c = take();
    expect(count(c, 0x04) == 1 && count(c, 0x01) == 0 && count(c, 0x02) == 0, "partial after the power-off powers on again, no re-init");

    epd.full(fb);
    c = take();
    expect(count(c, 0x01) == 1 && count(c, 0x04) == 1 && count(c, 0x02) == 1, "full after a held partial: re-init, power on, power off");
    advance(3000);
    epd.service();
    expect(take().empty(), "full cancels the hold window");

    fb[400] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    take();
    epd.gray4(gray);
    c = take();
    expect(count(c, 0x00) == 1 && count(c, 0x04) == 1 && count(c, 0x02) == 1, "gray4 after a held partial: re-init (PSR; gray writes no PWR), power on, power off");
    advance(3000);
    epd.service();
    expect(take().empty(), "gray4 cancels the hold window");

    epd.full(fb);
    take();
    fb[500] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    take();
    epd.invalidate();
    advance(2100);
    epd.service();
    c = take();
    expect(c.size() == 1 && c[0] == 0x02, "invalidate() inside the window: service() still powers off");

    epd.holdMs = 0;
    epd.full(fb);
    take();
    fb[600] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    c = take();
    expect(count(c, 0x02) == 1, "holdMs = 0: off after every partial (default)");
    advance(5000);
    epd.service();
    expect(take().empty(), "holdMs = 0: service() has nothing to do");

    epd.holdMs = 2000;
    epd.keepPowered = true;
    fb[700] ^= 0xFF;
    epd.partial(fb, 0, 0, 64, 32);
    take();
    advance(5000);
    epd.service();
    expect(take().empty(), "keepPowered wins over the hold window");

    printf(g_fail ? "FAILED %d\n" : "ALL OK\n", g_fail);
    return g_fail ? 1 : 0;
}

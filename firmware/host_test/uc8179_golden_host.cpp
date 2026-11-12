// Host check of what firmware/src/ee05/uc8179_fast.h puts on the SPI wire: every command byte, every
// parameter byte and the LUT bytes, for a full refresh, two partial refreshes (the second without a
// re-init), a 4-gray refresh and a power off. Large RAM-plane transfers are printed as length + CRC-32.
// decky/tests/test_uc8179_golden.py compares this output with golden/uc8179_sequence.txt, so a change to
// a register or LUT byte cannot go unnoticed. Regenerate on purpose with:  <exe> > golden/uc8179_sequence.txt
// Build: g++ -std=c++17 -Ifake -I../src/ee05 -DEPD_SCK=1 -DEPD_MOSI=2 -DEPD_CS=3 -DEPD_DC=4 -DEPD_RST=5 -DEPD_BUSY=6 uc8179_golden_host.cpp
#include <Arduino.h>
#include <SPI.h>
#include <stdio.h>
#include <vector>

#include "uc8179_fast.h"

static int g_dc = HIGH, g_cs = HIGH;
static std::vector<uint8_t> g_run;  // data bytes since the last command
static bool g_haveCmd = false;

static uint32_t crc32(const std::vector<uint8_t> &v)
{
    uint32_t c = 0xFFFFFFFFu;
    for (uint8_t b : v) {
        c ^= b;
        for (int i = 0; i < 8; i++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return ~c;
}

static void flush()
{
    if (g_run.empty()) return;
    if (g_run.size() <= 64) {
        printf("  D");
        for (uint8_t b : g_run) printf(" %02x", b);
        printf("\n");
    } else {
        printf("  D[%zu] crc32=%08x\n", g_run.size(), crc32(g_run));
    }
    g_run.clear();
}

void digitalWrite(int pin, int level)
{
    if (pin == EPD_DC) g_dc = level;
    if (pin == EPD_CS) g_cs = level;
}

static void byte(uint8_t b)
{
    if (g_cs != LOW) return;
    if (g_dc == LOW) {
        flush();
        printf("C %02x\n", b);
        g_haveCmd = true;
    } else {
        g_run.push_back(b);
    }
}
void SPIClass::transfer(uint8_t b) { byte(b); }
void SPIClass::writeBytes(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) byte(p[i]);
}

static fastpartial::Epd epd;  // 39 KB shadow: keep it off the stack
static uint8_t fb[fastpartial::PANEL_H * fastpartial::STRIDE];
static uint8_t gray[fastpartial::PANEL_H * fastpartial::PANEL_W / 4];

static void step(const char *name)
{
    flush();
    printf("# %s\n", name);
}

int main()
{
    uint32_t s = 1;
    for (auto &b : fb) { s = s * 1664525u + 1013904223u; b = (uint8_t)(s >> 24); }
    for (auto &b : gray) { s = s * 1664525u + 1013904223u; b = (uint8_t)(s >> 24); }

    epd.begin();
    step("full");
    epd.full(fb);
    step("partial 100,50 200x96 (re-init: LUTs load)");
    fb[50 * fastpartial::STRIDE + 13] ^= 0xFF;
    epd.partial(fb, 100, 50, 200, 96);
    step("partial 8,8 64x32 (same mode, no re-init)");
    epd.partial(fb, 8, 8, 64, 32);
    step("gray4");
    epd.gray4(gray);
    step("partial after gray (unknown glass -> full)");
    epd.partial(fb, 0, 0, 16, 16);
    step("sleep");
    epd.sleep();
    flush();
    return g_haveCmd ? 0 : 1;
}

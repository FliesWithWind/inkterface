// Host-side checks for fastpartial.h (LUT bytes, window math, timing model, stats).
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 fastpartial_host.cpp -o fastpartial_host
#include "fastpartial.h"

#include <assert.h>
#include <stdio.h>

using namespace fastpartial;

static bool allZeroFrom(const uint8_t *p, size_t from)
{
    for (size_t i = from; i < LUT_LEN; i++)
        if (p[i]) return false;
    return true;
}

static void testLut()
{
    uint8_t l[LUT_LEN];

    // single phase, no pre: K->W is VDL (2) in phase A, W->K is VDH (1)
    buildTransitionLut(VDH, VDL, Params(0, 20), l);
    assert(l[0] == 0x80 && l[1] == 20 && l[2] == 0 && l[3] == 0 && l[4] == 0 && l[5] == 1);
    assert(allZeroFrom(l, 6));
    buildTransitionLut(VDL, VDH, Params(0, 20), l);
    assert(l[0] == 0x40 && l[1] == 20 && l[5] == 1 && allZeroFrom(l, 6));

    // stock-shaped: pre toward the old colour, then drive toward the new one
    buildTransitionLut(VDH, VDL, Params(35, 35), l);  // K->W: VDH then VDL
    assert(l[0] == 0x60 && l[1] == 35 && l[2] == 35 && l[3] == 0 && l[4] == 0 && l[5] == 1);
    buildTransitionLut(VDL, VDH, Params(35, 35), l);  // W->K: VDL then VDH
    assert(l[0] == 0x90 && l[1] == 35 && l[2] == 35);

    // nothing to drive -> all zero (no stray repeat count)
    buildTransitionLut(VDH, VDL, Params(0, 0), l);
    assert(allZeroFrom(l, 0));

    // only the two changing transitions are driven; WW, KK, VCOM, border stay zero
    uint8_t all[6][LUT_LEN];
    buildLuts(Params(0, 20), all);
    assert(allZeroFrom(all[0], 0) && allZeroFrom(all[1], 0) && allZeroFrom(all[4], 0) &&
           allZeroFrom(all[5], 0));
    assert(all[2][0] == 0x80 && all[3][0] == 0x40);
}

static void testTiming()
{
    // measured on the EE05: stock partial 1484 ms of refresh, default 467 ms (+-1%)
    uint32_t stock = refreshUs(STOCK) / 1000, fast = refreshUs(FAST) / 1000;
    assert(stock > 1469 && stock < 1499);
    assert(refreshUs(Params(0, 20)) / 1000 > 462 && refreshUs(Params(0, 20)) / 1000 < 472);
    assert(fast < 1000);
    // 255 + 255 frames must not overflow 32 bits
    assert(refreshUs(Params(255, 255)) == 513u * FRAME_US);
}

static void testWindow()
{
    Window w;
    assert(alignWindow(0, 0, 8, 1, w) && w.x8 == 0 && w.w8 == 8 && w.xe == 7 && w.rowBytes() == 1);
    // unaligned x/w snap outward: pixels 3..8 touch bytes 0 and 1
    assert(alignWindow(3, 0, 6, 1, w) && w.x8 == 0 && w.w8 == 16 && w.xe == 15 && w.rowBytes() == 2);
    assert(alignWindow(7, 5, 2, 3, w) && w.x8 == 0 && w.w8 == 16 && w.y == 5 && w.ye == 7);
    assert(alignWindow(8, 0, 8, 1, w) && w.x8 == 8 && w.w8 == 8 && w.xe == 15);
    // full panel: row stride on the wire is 81 bytes
    assert(alignWindow(0, 0, PANEL_W, PANEL_H, w) && w.w8 == 648 && w.xe == 647 && w.ye == 479 &&
           w.rowBytes() == STRIDE);
    assert(alignWindow(640, 479, 8, 1, w) && w.xe == 647 && w.ye == 479);
    // rejects
    assert(!alignWindow(0, 0, 0, 1, w) && !alignWindow(0, 0, 1, 0, w));
    assert(!alignWindow(641, 0, 8, 1, w) && !alignWindow(0, 480, 8, 1, w));
    assert(!alignWindow(0, 0, 649, 1, w) && !alignWindow(0, 0, 8, 481, w));
    // snapping never leaves the panel for any in-bounds input
    for (uint16_t x = 0; x < PANEL_W; x += 5)
        for (uint16_t ww = 1; x + ww <= PANEL_W; ww += 37) {
            assert(alignWindow(x, 0, ww, 1, w));
            assert(w.x8 <= x && w.x8 + w.w8 >= x + ww && w.xe < PANEL_W && !(w.x8 & 7) && !(w.w8 & 7));
        }
}

static void testStats()
{
    Stats<64> s;
    assert(s.percentile(50) == 0);
    s.add(7);
    assert(s.percentile(50) == 7 && s.percentile(90) == 7);
    Stats<64> t;
    for (int i = 20; i >= 1; i--) t.add(i * 10);  // unsorted input
    assert(t.percentile(50) == 100);               // nearest rank: ceil(0.5*20) = 10th
    assert(t.percentile(90) == 180);               // 18th
    assert(t.percentile(100) == 200);
    Stats<64> u;  // n not a multiple of 100/pct: rank rounds up (ceil(6.3) = 7, ceil(3.5) = 4)
    for (int i = 1; i <= 7; i++) u.add(i);
    assert(u.percentile(90) == 7 && u.percentile(50) == 4);
}

int main()
{
    testLut();
    testTiming();
    testWindow();
    testStats();
    puts("fastpartial host OK");
    return 0;
}

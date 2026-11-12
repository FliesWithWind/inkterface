// Partial-refresh bench for the EE05 (bare-metal UC8179). Line protocol over USB CDC,
// driven by decky/tools/pbench.py. Not a product firmware: no Wi-Fi, no BLE, boots at once.
//
//   set pre|drive|keep <n>         waveform / power policy
//   init                           forget controller state (next refresh re-inits)
//   fill w|b                       whole bench framebuffer white / black
//   box x y w h w|b                rectangle in the bench framebuffer
//   full                           OTP full refresh of the bench framebuffer
//   part x y w h                   one partial refresh of that window
//   run x y w h n mode [gap_ms]    n partials; mode 0 = invert window each time (worst case),
//                                  mode 1 = pseudo-random 8x16 cells (text-like churn)
//   raw cmd [hex data...]          raw controller command
//   sleep                          power off + deep sleep state forgotten
// Replies: "T k total init pon data refresh poff busylat" (us) per refresh, "OK ..." / "ERR ...".
#include <Arduino.h>

#include "../ee05/uc8179_fast.h"

using namespace fastpartial;

static Epd epd;
static uint8_t FB[PANEL_H * STRIDE];
static uint32_t RNG = 0x1234567;

static uint32_t rnd()
{
    RNG ^= RNG << 13; RNG ^= RNG >> 17; RNG ^= RNG << 5;
    return RNG;
}

static void boxFill(int x, int y, int w, int h, bool black)
{
    for (int r = y; r < y + h && r < PANEL_H; r++)
        for (int c = x; c < x + w && c < PANEL_W; c++) {
            uint8_t m = 0x80 >> (c & 7);
            uint8_t &b = FB[r * STRIDE + (c >> 3)];
            if (black) b |= m; else b &= ~m;
        }
}

static void printTiming(int k)
{
    const Timing &t = epd.last;
    Serial.printf("T %d %u %u %u %u %u %u %u\n", k, t.total, t.init, t.powerOn, t.data, t.refresh,
                  t.powerOff, t.busyStart);
}

static void churn(int x, int y, int w, int h, int mode, int k)
{
    if (mode == 0) {  // invert every pixel of the window (byte aligned, so fine)
        int x8 = x & ~7, xe = (x + w + 7) & ~7;
        for (int r = y; r < y + h; r++)
            for (int b = x8 / 8; b < xe / 8; b++) FB[r * STRIDE + b] ^= 0xFF;
    } else {  // 8x16 cells get random fill
        for (int cy = y; cy < y + h; cy += 16)
            for (int cx = x & ~7; cx < x + w; cx += 8) {
                uint8_t v = (uint8_t)rnd();
                for (int r = cy; r < cy + 16 && r < y + h; r++) FB[r * STRIDE + cx / 8] = (r & 1) ? v : (uint8_t)~v;
            }
    }
    (void)k;
}

static void runN(int x, int y, int w, int h, int n, int mode, int gap)
{
    Stats<128> total, refresh;
    for (int k = 0; k < n; k++) {
        churn(x, y, w, h, mode, k);
        epd.partial(FB, x, y, w, h);
        printTiming(k);
        total.add(epd.last.total / 1000);
        refresh.add(epd.last.refresh / 1000);
        if (gap) delay(gap);
    }
    Serial.printf("OK run n=%d total_ms median=%u p90=%u max=%u refresh_ms median=%u p90=%u\n", n,
                  total.percentile(50), total.percentile(90), total.percentile(100),
                  refresh.percentile(50), refresh.percentile(90));
}

static void handle(char *line)
{
    char *tok[16];
    int n = 0;
    for (char *p = strtok(line, " \t\r\n"); p && n < 16; p = strtok(nullptr, " \t\r\n")) tok[n++] = p;
    if (!n) return;
    auto num = [&](int i) { return i < n ? (int)strtol(tok[i], nullptr, 0) : 0; };
    String c = tok[0];
    if (c == "set" && n >= 3) {
        Params p = epd.params;
        String k = tok[1];
        if (k == "pre") p.pre = num(2);
        else if (k == "drive") p.drive = num(2);
        else if (k == "keep") epd.keepPowered = num(2);
        else { Serial.println("ERR unknown param"); return; }
        epd.setParams(p);
        Serial.printf("OK pre=%u drive=%u keep=%d\n", p.pre, p.drive, epd.keepPowered);
    } else if (c == "init") {
        epd.invalidate();
        Serial.println("OK");
    } else if (c == "fill") {
        memset(FB, n > 1 && tok[1][0] == 'b' ? 0xFF : 0x00, sizeof(FB));
        Serial.println("OK");
    } else if (c == "box" && n >= 6) {
        boxFill(num(1), num(2), num(3), num(4), tok[5][0] == 'b');
        Serial.println("OK");
    } else if (c == "full") {
        epd.full(FB);
        printTiming(-1);
        Serial.println("OK full");
    } else if (c == "part" && n >= 5) {
        bool ok = epd.partial(FB, num(1), num(2), num(3), num(4));
        printTiming(0);
        Serial.println(ok ? "OK part" : "ERR bad window");
    } else if (c == "run" && n >= 7) {
        runN(num(1), num(2), num(3), num(4), num(5), num(6), num(7));
    } else if (c == "raw" && n >= 2) {
        uint8_t d[12];
        int m = 0;
        for (int i = 2; i < n && m < 12; i++) d[m++] = num(i);
        epd.raw(num(1), d, m);
        Serial.println("OK raw");
    } else if (c == "sleep") {
        epd.sleep();
        Serial.println("OK");
    } else if (c == "hello") {
        Serial.println("OK pbench");
    } else {
        Serial.println("ERR unknown");
    }
}

void setup()
{
    Serial.begin(115200);
    epd.begin();
}

void loop()
{
    static char buf[128];
    static size_t len = 0;
    while (Serial.available()) {
        char ch = Serial.read();
        if (ch == '\n') {
            buf[len] = 0;
            len = 0;
            handle(buf);
        } else if (len < sizeof(buf) - 1) {
            buf[len++] = ch;
        }
    }
    delay(1);
}

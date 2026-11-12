// Host-side harness for usbproto.cpp (no Arduino, no display).
//
//   usbproto_host --selftest
//       asserts on the C++ parser/device (timeouts, stall reset, CRC vector, ...)
//   usbproto_host <chunk> <glass_out> <events_out> < host_bytes > panel_bytes
//       feeds stdin to a Device in `chunk`-byte reads; stdout = every byte the
//       panel wrote; glass_out = final displayed 1bpp image; events_out = one
//       line per present(). decky/tests/test_cpp_diff.py compares this against
//       the Python emulator byte for byte.
//
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 usbproto_host.cpp ../src/ee05/usbproto.cpp
#include "usbproto.h"

#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

using namespace usbproto;

struct VecLink : Link {
    std::vector<uint8_t> out;
    size_t write(const uint8_t *p, size_t n) override
    {
        out.insert(out.end(), p, p + n);
        return n;
    }
};

struct GlassPanel : Panel {
    std::vector<uint8_t> glass = std::vector<uint8_t>(FB_BYTES, 0);
    std::vector<char> events;
    int presents = 0;
    void present(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h, bool full) override
    {
        presents++;
        char line[64];
        int n = snprintf(line, sizeof line, "%c %u %u %u %u\n", full ? 'F' : 'P', x, y, w, h);
        events.insert(events.end(), line, line + n);
        for (uint16_t r = y; r < y + h; r++)
            memcpy(&glass[r * PANEL_STRIDE + x / 8], fb + r * PANEL_STRIDE + x / 8, w / 8);
    }
};

static const Info kInfo = {"host-test", 0, 0, 8192};

static std::vector<uint8_t> mk(uint8_t type, uint8_t seq, const std::vector<uint8_t> &p = {})
{
    std::vector<uint8_t> f(HDR_LEN + p.size() + CRC_LEN);
    size_t n = encodeFrame(type, seq, p.data(), (uint16_t)p.size(), f.data(), f.size());
    assert(n == f.size());
    return f;
}

static std::vector<uint8_t> rectPayload(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t fill)
{
    std::vector<uint8_t> p = {(uint8_t)x, (uint8_t)(x >> 8), (uint8_t)y, (uint8_t)(y >> 8),
                              (uint8_t)w, (uint8_t)(w >> 8), (uint8_t)h, (uint8_t)(h >> 8)};
    p.resize(8 + (size_t)h * (w / 8), fill);
    return p;
}

static int selftest()
{
    // CRC-32 check value (zlib.crc32(b"123456789") == 0xCBF43926) and incremental form
    assert(crc32((const uint8_t *)"123456789", 9) == 0xCBF43926u);
    assert(crc32((const uint8_t *)"6789", 4, crc32((const uint8_t *)"12345", 5)) == 0xCBF43926u);

    // golden frame: HEARTBEAT seq 7, empty payload
    uint8_t g[16];
    size_t gn = encodeFrame(T_HEARTBEAT, 7, nullptr, 0, g, sizeof g);
    assert(gn == 10 && g[0] == 0xA5 && g[1] == 0x5A && g[2] == 0x02 && g[3] == 7 && g[4] == 0 && g[5] == 0);
    uint32_t c = crc32(g + 2, 4);
    assert(g[6] == (c & 0xFF) && g[9] == (c >> 24));
    assert(encodeFrame(T_RECT, 0, g, MAX_PAYLOAD + 1, g, sizeof g) == 0);  // oversize refused

    // split reads: every 1-byte feed parses; garbage prefix; bad-CRC frame then good frame
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        std::vector<uint8_t> s = {0x00, 0xA5, 0xA5, 0x13, 0x37};  // garbage incl. lone/duplicate SYNC0
        auto bad = mk(T_HEARTBEAT, 1); bad[7] ^= 0xFF;            // corrupt CRC
        auto good = mk(T_MODE, 2, {MODE_HOST});
        s.insert(s.end(), bad.begin(), bad.end());
        s.insert(s.end(), good.begin(), good.end());
        for (uint8_t b : s) d.onBytes(&b, 1, 0);
        assert(d.mode() == MODE_HOST && d.alive());
        assert(d.parser().crcErrors() == 1);
        // reply = HELLO (new session) then ACK
        assert(link.out.size() > 10 && link.out[2] == T_HELLO);
    }

    // a corrupt frame with an inflated len must not swallow the frame behind it
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto inner = mk(T_MODE, 9, {MODE_HOST});
        std::vector<uint8_t> s = {0xA5, 0x5A, T_RECT, 0, 0x00, 0x08 /* len 2048 */};
        s.insert(s.end(), inner.begin(), inner.end());
        d.onBytes(s.data(), s.size(), 0);
        assert(d.mode() == MODE_PANEL);  // still waiting for the 2048 promised bytes
        d.tick(PARTIAL_FRAME_TIMEOUT_MS + 1);
        assert(d.mode() == MODE_HOST && !d.parser().pending());
    }

    // len > MAX_PAYLOAD is rejected immediately (does not wait for 65535 bytes)
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        std::vector<uint8_t> s = {0xA5, 0x5A, T_RECT, 0, 0xFF, 0xFF};
        auto good = mk(T_MODE, 1, {MODE_HOST});
        s.insert(s.end(), good.begin(), good.end());
        d.onBytes(s.data(), s.size(), 0);
        assert(d.parser().badLen() >= 1 && d.mode() == MODE_HOST);
    }

    // stalled half-frame is dropped by tick(), next frame parses
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        uint8_t half[] = {0xA5, 0x5A, T_RECT, 0, 0x00, 0x10};  // promises 4096 B, never arrives
        d.onBytes(half, sizeof half, 100);
        assert(d.parser().pending());
        d.tick(100 + PARTIAL_FRAME_TIMEOUT_MS + 1);
        assert(!d.parser().pending());
        auto good = mk(T_MODE, 1, {MODE_HOST});
        d.onBytes(good.data(), good.size(), 400);
        assert(d.mode() == MODE_HOST);
    }

    // host timeout releases the display: default 60 s, strictly after, no earlier
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto m = mk(T_MODE, 1, {MODE_HOST});
        d.onBytes(m.data(), m.size(), 1000);
        assert(d.hostOwnsDisplay());
        d.tick(1000 + 60000 - 1);
        assert(d.hostOwnsDisplay() && d.alive());
        d.tick(1000 + 60000);
        assert(!d.hostOwnsDisplay() && d.mode() == MODE_PANEL && d.idle().idle());
        d.tick(1000 + 60001);
        assert(!d.alive());
    }

    // USB attach edge: HELLO only without a live session (HWCDC flaps `Serial` during a session; a HELLO the host
    // did not ask for makes it redraw everything)
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        d.onAttach();
        assert(!link.out.empty() && link.out[2] == T_HELLO);       // no session: announce
        link.out.clear();
        auto hb = mk(T_HEARTBEAT, 1);
        d.onBytes(hb.data(), hb.size(), 1000);                     // session starts (HELLO + ACK)
        link.out.clear();
        d.onAttach();
        assert(link.out.empty());                                  // live session: stay quiet
        d.tick(1000 + 60001);                                      // host silent past the timeout: session over
        assert(!d.alive());
        d.onAttach();
        assert(!link.out.empty() && link.out[2] == T_HELLO);
    }

    // LOG text written while no host session exists is kept and delivered when one starts
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        const char *msg = "idle: enter";
        logWrite((const uint8_t *)msg, strlen(msg));
        d.tick(10);
        assert(link.out.empty());                         // nobody listening: nothing written
        auto hb = mk(T_HEARTBEAT, 1);
        d.onBytes(hb.data(), hb.size(), 20);              // session starts (HELLO + ACK)
        size_t before = link.out.size();
        d.tick(21);
        assert(link.out.size() > before);
        bool found = false;
        for (size_t i = 0; i + strlen(msg) <= link.out.size(); i++)
            if (!memcmp(&link.out[i], msg, strlen(msg))) found = true;
        assert(found);
    }

    // LOG frames are cut at line ends: two 150-byte lines never split across frames
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto hb = mk(T_HEARTBEAT, 1);
        d.onBytes(hb.data(), hb.size(), 0);
        link.out.clear();
        std::string l1(149, 'a'), l2(149, 'b');
        l1 += '\n'; l2 += '\n';
        logWrite((const uint8_t *)l1.data(), l1.size());
        logWrite((const uint8_t *)l2.data(), l2.size());
        d.tick(1);
        Parser ps;
        static std::vector<std::string> frames;
        frames.clear();
        ps.feed(link.out.data(), link.out.size(), [](void *, const Frame &f) {
            if (f.type == T_LOG) frames.push_back(std::string((const char *)f.payload, f.len));
        }, nullptr);
        assert(frames.size() == 2 && frames[0] == l1 && frames[1] == l2);
    }

    // LOG text waits while the link cannot take a whole frame (an ACK must never be crowded out)
    {
        struct Busy : VecLink { size_t room = 0; size_t writable() const override { return room; } } link;
        GlassPanel panel; Device d(link, panel, kInfo);
        auto hb = mk(T_HEARTBEAT, 1);
        d.onBytes(hb.data(), hb.size(), 0);
        const char *msg = "queued-log";
        logWrite((const uint8_t *)msg, strlen(msg));
        size_t before = link.out.size();
        d.tick(1);
        assert(link.out.size() == before);                 // no room: nothing sent, nothing lost
        link.room = 1000;
        d.tick(2);
        assert(link.out.size() > before);                  // room again: delivered
    }

    // max-size RECT: 8 + 50 rows * 81 B = 4058 <= 4096; full-width band blits
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto m = mk(T_MODE, 1, {MODE_HOST});
        auto r = mk(T_RECT, 2, rectPayload(0, 10, 648, 50, 0xAA));
        auto f = mk(T_REFRESH, 3, {0});
        d.onBytes(m.data(), m.size(), 0);
        d.onBytes(r.data(), r.size(), 0);
        d.onBytes(f.data(), f.size(), 0);
        assert(panel.presents == 1 && panel.glass[10 * PANEL_STRIDE] == 0xAA);
        assert(panel.glass[9 * PANEL_STRIDE] == 0 && panel.glass[60 * PANEL_STRIDE] == 0);
        // REFRESH with nothing dirty: ACK, no present()
        d.onBytes(f.data(), f.size(), 0);
        assert(panel.presents == 1);
    }

    // reject: zero-size, unaligned x, unaligned w, out of bounds, wrong length, wrong mode
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto send = [&](std::vector<uint8_t> f) { d.onBytes(f.data(), f.size(), 0); };
        auto lastNak = [&]() { return link.out[link.out.size() - 5]; };  // NAK payload[1] (reason)
        send(mk(T_RECT, 1, rectPayload(0, 0, 8, 8, 1)));
        assert(link.out.back() != 0 && lastNak() == NAK_WRONG_MODE);
        send(mk(T_MODE, 2, {MODE_HOST}));
        size_t n0 = link.out.size();
        send(mk(T_RECT, 3, rectPayload(0, 0, 0, 8, 0)));
        assert(link.out.size() > n0 && lastNak() == NAK_BAD_RECT);
        send(mk(T_RECT, 4, rectPayload(0, 0, 8, 0, 0)));
        assert(lastNak() == NAK_BAD_RECT);
        send(mk(T_RECT, 5, rectPayload(4, 0, 8, 8, 0)));
        assert(lastNak() == NAK_BAD_RECT);
        send(mk(T_RECT, 6, rectPayload(0, 0, 12, 8, 0)));
        assert(lastNak() == NAK_BAD_RECT);
        send(mk(T_RECT, 7, rectPayload(648, 0, 8, 8, 0)));
        assert(lastNak() == NAK_BAD_RECT);
        send(mk(T_RECT, 8, rectPayload(0, 475, 8, 8, 0)));
        assert(lastNak() == NAK_BAD_RECT);
        auto shortp = rectPayload(0, 0, 8, 8, 0); shortp.pop_back();
        send(mk(T_RECT, 9, shortp));
        assert(lastNak() == NAK_BAD_LEN);
        send(mk(0x55, 10));
        assert(lastNak() == NAK_BAD_TYPE);
        send(mk(T_OTA_BEGIN, 11));
        assert(lastNak() == NAK_UNSUPPORTED);
        assert(panel.presents == 0);
    }
    // encodeFrame: cap too small refused; HELLO layout; panel tx seq increments
    {
        uint8_t small[9];
        assert(encodeFrame(T_HEARTBEAT, 0, nullptr, 0, small, sizeof small) == 0);  // needs 10
        VecLink link; GlassPanel panel; Info info = {"fw-x", CAP_IDLE_STORAGE, 1234, 8192};
        Device d(link, panel, info);
        auto h = mk(T_HELLO, 3);
        d.onBytes(h.data(), h.size(), 0);
        Parser p; std::vector<std::vector<uint8_t>> got;
        p.feed(link.out.data(), link.out.size(),
               [](void *c, const Frame &f) {
                   static_cast<std::vector<std::vector<uint8_t>> *>(c)->push_back(
                       std::vector<uint8_t>(f.payload, f.payload + f.len));
               }, &got);
        assert(got.size() == 1 && got[0].size() == 18 + 4);
        const std::vector<uint8_t> &x = got[0];
        assert(x[0] == 1 && x[1] == 0);                    // proto 1
        assert(x[2] == 0x00 && x[3] == 0x10);              // max payload 4096
        assert(x[4] == 0x00 && x[5] == 0x20);              // rx window 8192
        assert(x[6] == (648 & 0xFF) && x[7] == (648 >> 8) && x[8] == (480 & 0xFF) && x[9] == (480 >> 8));
        assert(x[10] == CAP_IDLE_STORAGE && x[14] == (1234 & 0xFF) && x[15] == (1234 >> 8));
        assert(memcmp(&x[18], "fw-x", 4) == 0);
        assert(link.out[3] == 0);                          // first panel frame has tx seq 0
        auto hb = mk(T_HEARTBEAT, 1);
        d.onBytes(hb.data(), hb.size(), 0);
        d.onBytes(hb.data(), hb.size(), 0);
        size_t n = link.out.size();                        // two ACKs of 11 bytes after the HELLO
        assert(link.out[n - 11 + 3] == 2 && link.out[n - 22 + 3] == 1);
    }

    // MODE / REFRESH argument errors and partial-refresh bbox union
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto send = [&](std::vector<uint8_t> f) { d.onBytes(f.data(), f.size(), 0); };
        auto reason = [&]() { return link.out[link.out.size() - 5]; };
        auto isNak = [&]() { return link.out[link.out.size() - 12 + 2] == T_NAK; };
        send(mk(T_MODE, 1, {}));
        assert(isNak() && reason() == NAK_BAD_LEN);
        send(mk(T_MODE, 2, {2}));
        assert(isNak() && reason() == NAK_BAD_ARG);
        send(mk(T_MODE, 3, {MODE_HOST}));
        send(mk(T_REFRESH, 4, {}));
        assert(isNak() && reason() == NAK_BAD_LEN);
        send(mk(T_REFRESH, 5, {2}));
        assert(isNak() && reason() == NAK_BAD_ARG);
        send(mk(T_RECT, 6, rectPayload(8, 10, 8, 2, 0xFF)));
        send(mk(T_RECT, 7, rectPayload(400, 300, 16, 3, 0xFF)));
        send(mk(T_REFRESH, 8, {0}));
        assert(panel.presents == 1 && std::string(panel.events.begin(), panel.events.end()) == "P 8 10 408 293\n");
        send(mk(T_REFRESH, 9, {1}));  // full ignores dirty and repaints everything
        assert(panel.presents == 2);
        assert(std::string(panel.events.begin(), panel.events.end()).rfind("F 0 0 648 480\n") != std::string::npos);
    }

    // log ring overflow: the 2 KiB ring drops the excess, never overruns, and stays drainable
    {
        VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
        auto hb = mk(T_HEARTBEAT, 1);
        d.onBytes(hb.data(), hb.size(), 10);
        size_t base = link.out.size();
        std::vector<uint8_t> big(5000, 'x');
        logWrite(big.data(), big.size());
        for (uint32_t t = 20; t < 60; t++) d.tick(t);
        assert(link.out.size() > base);
        size_t after = link.out.size();
        for (uint32_t t = 60; t < 80; t++) d.tick(t);  // ring empty now
        assert(link.out.size() == after);
    }

    puts("usbproto selftest OK");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--selftest")) return selftest();
    if (argc != 4) return 2;
    size_t chunk = (size_t)atoi(argv[1]);
    if (!chunk) return 2;
    std::vector<uint8_t> in;
    uint8_t b[4096];
    size_t n;
    while ((n = fread(b, 1, sizeof b, stdin)) > 0) in.insert(in.end(), b, b + n);

    VecLink link; GlassPanel panel; Device d(link, panel, kInfo);
    for (size_t i = 0; i < in.size(); i += chunk)
        d.onBytes(&in[i], in.size() - i < chunk ? in.size() - i : chunk, 0);

    fwrite(link.out.data(), 1, link.out.size(), stdout);
    FILE *g = fopen(argv[2], "wb"); fwrite(panel.glass.data(), 1, panel.glass.size(), g); fclose(g);
    FILE *e = fopen(argv[3], "wb"); fwrite(panel.events.data(), 1, panel.events.size(), e); fclose(e);
    return 0;
}

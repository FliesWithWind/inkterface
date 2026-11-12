// Host tests for the USB bus-suspend fallback (usb_bus_watch.h + Controller/Device::onBusSuspend).
// The OTA part ("not while an update is in transit") is in ota_host.cpp (testBusy).
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 usb_bus_watch_host.cpp ../src/ee05/usbproto.cpp
#include <assert.h>
#include <stdio.h>
#include <vector>

#include "idle_mode.h"
#include "usb_bus_watch.h"
#include "usbproto.h"

using busdown::Watch;

struct Sim {   // drives Watch like the firmware loop: one sample per `step` ms
    Watch w;
    uint32_t t = 100000;
    int fires = 0;
    bool plugged = true, hold = false;
    void run(uint32_t ms, uint32_t step = 1)
    {
        for (uint32_t i = 0; i < ms; i += step) {
            t += step;
            if (w.update(plugged, hold, t)) fires++;
        }
    }
};

static void testNormalOperationNeverFires()
{
    Sim s;
    s.run(10 * 60 * 1000, 5);   // ten minutes of healthy bus
    assert(s.fires == 0);
    // flaps shorter than DOWN_MS, however often, never fire (the measured SOF flap on a hub-attached dev PC)
    for (int i = 0; i < 1000; i++) {
        s.plugged = false;
        s.run(busdown::DOWN_MS - 5, 5);
        s.plugged = true;
        s.run(20, 5);
    }
    assert(s.fires == 0);
}

static void testFiresOnceAfterDownMs()
{
    Sim s;
    s.run(100);
    s.plugged = false;
    s.run(busdown::DOWN_MS);       // first down sample at +1 ms ... last at +DOWN_MS: DOWN_MS-1 ms of outage so far
    assert(s.fires == 0);
    s.run(1);
    assert(s.fires == 1);          // exactly DOWN_MS after the first down sample
    s.run(60000);
    assert(s.fires == 1);          // once per outage
    s.plugged = true;
    s.run(10);
    s.plugged = false;
    s.run(busdown::DOWN_MS + 1);
    assert(s.fires == 2);          // the next outage fires again
}

static void testBlockedLoopDoesNotCountBlockedTime()
{
    // e-paper full refresh blocks the loop ~4.1 s. Bus down before and after the block, no sample in between:
    // the blocked time must not count, otherwise a long refresh next to a SOF hiccup would look like a suspend.
    Sim s;
    s.plugged = false;
    s.run(busdown::DOWN_MS / 2, 5);
    assert(s.w.update(false, false, s.t + 4100) == false);   // first sample after a 4.1 s stall: clock restarts
    s.t += 4100;
    for (uint32_t i = 0; i < busdown::DOWN_MS - 5; i += 5) assert(!s.w.update(false, false, s.t += 5));
    assert(s.w.update(false, false, s.t += 5));              // DOWN_MS counted from the sample after the stall
    // a stall during a healthy bus changes nothing
    Sim h;
    h.run(1000, 5);
    h.t += 4100;
    assert(!h.w.update(true, false, h.t));
    assert(h.fires == 0);
    // a gap of exactly MAX_GAP_MS is still a normal pass; one more ms is a stall
    Sim g;
    g.plugged = false;
    g.run(100, 5);
    uint32_t t0 = g.t;
    assert(!g.w.update(false, false, t0 + busdown::MAX_GAP_MS));                         // not a stall: down_ keeps its value
    assert(!g.w.update(false, false, t0 + 2 * busdown::MAX_GAP_MS));
    for (uint32_t t = t0 + 2 * busdown::MAX_GAP_MS; t < t0 + 100 + busdown::DOWN_MS; t += busdown::MAX_GAP_MS)
        if (g.w.update(false, false, t)) { g.fires++; break; }
    assert(g.fires == 1);
}

static void testHoldSuppressesAndRestarts()
{
    Sim s;
    s.plugged = false;
    s.hold = true;                       // OTA in transit
    s.run(5 * busdown::DOWN_MS, 5);
    assert(s.fires == 0);
    s.hold = false;
    s.run(busdown::DOWN_MS - 5, 5);      // the outage clock starts when the hold ends, not before it
    assert(s.fires == 0);
    s.run(10, 5);
    assert(s.fires == 1);
    // hold appearing in the middle of an outage cancels it
    Sim m;
    m.plugged = false;
    m.run(busdown::DOWN_MS - 10, 5);
    m.hold = true;
    m.run(100, 5);
    m.hold = false;
    m.run(busdown::DOWN_MS - 10, 5);
    assert(m.fires == 0);
}

static void testResetAndOutageReport()
{
    Sim s;
    s.plugged = false;
    s.run(busdown::DOWN_MS - 100, 5);
    s.w.reset();                         // idle took over: forget
    s.run(busdown::DOWN_MS - 100, 5);    // a fresh outage clock, not DOWN_MS in total
    assert(s.fires == 0);
    s.run(200, 5);
    assert(s.fires == 1);
    s.w.reset();
    s.plugged = true;
    s.run(10, 5);
    s.plugged = false;
    s.run(500, 5);
    s.plugged = true;
    s.run(10, 5);
    uint32_t o = s.w.takeOutageMs();     // 500 ms outage that ended without firing: reported once
    assert(o >= 495 && o <= 510 && s.w.takeOutageMs() == 0);
    s.plugged = false;
    s.run(20, 5);                        // below REPORT_MS: not worth a line
    s.plugged = true;
    s.run(10, 5);
    assert(s.w.takeOutageMs() == 0);
    // wrap of millis()
    Watch w;
    uint32_t t = 0xFFFFFFFFu - 500;
    int fires = 0;
    for (uint32_t i = 0; i < busdown::DOWN_MS + 10; i += 5) fires += w.update(false, false, t += 5);
    assert(fires == 1);
}

// ---- Controller / Device -------------------------------------------------------------------
struct VecLink : usbproto::Link {
    std::vector<uint8_t> out;
    size_t write(const uint8_t *p, size_t n) override { out.insert(out.end(), p, p + n); return n; }
};
struct NullPanel : usbproto::Panel {
    int presents = 0;
    void present(const uint8_t *, uint16_t, uint16_t, uint16_t, uint16_t, bool) override { presents++; }
};
static const usbproto::Info kInfo = {"t", 0, 0, 8192};

static void feed(usbproto::Device &d, uint8_t type, uint8_t seq, const std::vector<uint8_t> &p, uint32_t now)
{
    std::vector<uint8_t> f(usbproto::HDR_LEN + p.size() + usbproto::CRC_LEN);
    size_t n = usbproto::encodeFrame(type, seq, p.data(), (uint16_t)p.size(), f.data(), f.size());
    d.onBytes(f.data(), n, now);
}
static int countType(const std::vector<uint8_t> &out, uint8_t type)
{
    int k = 0;
    for (size_t i = 0; i + 2 < out.size(); i++)
        if (out[i] == usbproto::SYNC0 && out[i + 1] == usbproto::SYNC1 && out[i + 2] == type) k++;
    return k;
}

static void testController()
{
    idlemode::Controller c;
    c.start(0);
    c.onMode(false, 0);
    assert(c.hostOwns());
    c.onBusSuspend();
    assert(c.idle() && !c.pinned() && c.takeEnteredIdle());
    c.onBusSuspend();                                  // already idle: no second edge
    assert(!c.takeEnteredIdle());
    c.onFrame(1000);                                   // any valid frame brings the host back, as after a timeout
    assert(c.hostOwns() && c.takeLeftIdle());
    // from WAIT it returns to WAIT
    idlemode::Controller w;
    w.start(0);
    w.onBusSuspend();
    assert(w.idle());
    w.onFrame(5);
    assert(w.state() == idlemode::WAIT);
    // pinned idle (MODE PANEL) is not touched
    idlemode::Controller p;
    p.start(0);
    p.onMode(true, 0);
    p.takeEnteredIdle();
    p.onBusSuspend();
    assert(p.pinned() && !p.takeEnteredIdle());
    p.onFrame(10);
    assert(p.idle());                                  // still pinned: frames do not wake it
}

static void testDeviceSessionEndsSoTheHostRedraws()
{
    VecLink link;
    NullPanel panel;
    usbproto::Device d(link, panel, kInfo);
    d.start(0);
    feed(d, usbproto::T_MODE, 1, {usbproto::MODE_HOST}, 1000);
    assert(d.hostOwnsDisplay() && d.alive());
    link.out.clear();
    d.onBusSuspend();                                  // host suspended (far below the 60 s silence timeout)
    assert(d.idle().idle() && !d.alive() && !d.hostOwnsDisplay());
    // the host resumes: its first frame (a heartbeat) leaves idle and the panel announces itself, which makes the
    // host assume the panel lost its session and redraw everything (engine._on_hello)
    feed(d, usbproto::T_HEARTBEAT, 2, {}, 9000);
    assert(d.hostOwnsDisplay() && d.alive());
    assert(countType(link.out, usbproto::T_HELLO) == 1 && countType(link.out, usbproto::T_ACK) == 1);
    // a flapping bus (suspend called twice) is harmless
    d.onBusSuspend();
    d.onBusSuspend();
    assert(d.idle().idle());
    // RECT is refused while idle, accepted again after the first frame
    link.out.clear();
    feed(d, usbproto::T_REFRESH, 3, {0}, 9100);
    assert(d.hostOwnsDisplay());                       // woke
    // pinned idle stays pinned
    feed(d, usbproto::T_MODE, 4, {usbproto::MODE_PANEL}, 9200);
    assert(d.idle().pinned());
    d.onBusSuspend();
    assert(d.idle().pinned());
}

int main()
{
    testNormalOperationNeverFires();
    testFiresOnceAfterDownMs();
    testBlockedLoopDoesNotCountBlockedTime();
    testHoldSuppressesAndRestarts();
    testResetAndOutageReport();
    testController();
    testDeviceSessionEndsSoTheHostRedraws();
    puts("usb_bus_watch host tests OK");
    return 0;
}

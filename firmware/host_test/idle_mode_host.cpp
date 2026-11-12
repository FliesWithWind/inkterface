// Host tests for the idle plumbing that needs no pixels or network:
//   idlemode::Controller   (timeouts, heartbeat tolerance, wake, pinned idle, rollover)
//   usbproto::Device       (MODE extension, IDLE_LAYOUT hook, idle transitions)
//   idle::parseStore / FlashStore (two slots, power loss at every write) / Uploader / LayoutSet
//   idle::Painter          (full vs partial vs skip, ghost-clear cadence, gray4)
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 idle_mode_host.cpp ../src/ee05/idle_{layout,store,
//        layoutset,painter,render,canvas,fonts_data}.cpp ../src/ee05/usbproto.cpp
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <vector>

#include "idle_env.h"
#include "idle_fallback.h"
#include "idle_layoutset.h"
#include "idle_painter.h"
#include "idle_store.h"
#include "usbproto.h"

static_assert(idle::NAK_BAD_DATA_ == usbproto::NAK_BAD_DATA && idle::NAK_STORAGE_ == usbproto::NAK_STORAGE,
              "idle store NAK numbers must match usbproto (and stay clear of OTA 7..13)");

using namespace idle;
using idlemode::Controller;

// --------------------------------------------------------------------------------------------
static void testController()
{
    {  // boot: WAIT, idle after the default 60 s of silence, edge flag fires once
        Controller c;
        c.start(1000);
        assert(c.state() == idlemode::WAIT && c.timeoutS() == 60);
        c.tick(1000 + 59999);
        assert(c.state() == idlemode::WAIT && !c.takeEnteredIdle());
        c.tick(1000 + 60000);
        assert(c.state() == idlemode::IDLE && !c.pinned() && c.takeEnteredIdle() && !c.takeEnteredIdle());
    }
    {  // frames in WAIT keep the silence clock fresh but do not give the display to the host
        Controller c;
        c.start(0);
        c.onFrame(50000);
        c.tick(100000);
        assert(c.state() == idlemode::WAIT);
        c.tick(110000);
        assert(c.idle());
    }
    {  // HOST -> IDLE strictly at the timeout; heartbeats every 5 s never trip it; a 14.9 s gap is fine at 15 s
        Controller c;
        assert(c.setTimeoutS(15));
        c.start(0);
        c.onMode(false, 0);
        assert(c.hostOwns());
        for (uint32_t t = 5000; t <= 60000; t += 5000) { c.onFrame(t); c.tick(t); assert(c.hostOwns()); }
        c.tick(60000 + 14999);
        assert(c.hostOwns());
        c.tick(60000 + 15000);
        assert(c.idle() && c.takeEnteredIdle());
    }
    {  // wake: any frame leaves auto idle and restores what it was (HOST), left flag once
        Controller c;
        c.start(0);
        c.onMode(false, 0);
        c.tick(60000);
        c.takeEnteredIdle();
        assert(c.idle());
        c.onFrame(61000);
        assert(c.hostOwns() && c.takeLeftIdle() && !c.takeLeftIdle());
        // idle from WAIT wakes back to WAIT (no MODE HOST seen yet)
        Controller w;
        w.start(0);
        w.tick(60000);
        assert(w.idle());
        w.onFrame(70000);
        assert(w.state() == idlemode::WAIT);
    }
    {  // MODE PANEL = pinned idle: frames and heartbeats never wake it, ticks keep it, MODE HOST does
        Controller c;
        c.start(0);
        c.onMode(false, 0);
        c.onMode(true, 1000);
        assert(c.idle() && c.pinned());
        for (uint32_t t = 2000; t < 200000; t += 5000) { c.onFrame(t); c.tick(t); }
        assert(c.idle() && c.pinned());
        c.onMode(false, 200000);
        assert(c.hostOwns() && !c.pinned());
        c.tick(200000 + 59999);
        assert(c.hostOwns());
    }
    {  // timeout range: 0 keeps, 15..3600 ok, outside refused and unchanged
        Controller c;
        assert(c.setTimeoutS(0) && c.timeoutS() == 60);
        assert(!c.setTimeoutS(14) && c.timeoutS() == 60);
        assert(!c.setTimeoutS(3601) && c.timeoutS() == 60);
        assert(c.setTimeoutS(15) && c.timeoutS() == 15);
        assert(c.setTimeoutS(3600) && c.timeoutS() == 3600);
    }
    {  // millis() rollover
        Controller c;
        c.start(0xFFFFFFF0u);
        c.tick(0xFFFFFFF0u + 59000u);  // wraps past zero
        assert(c.state() == idlemode::WAIT);
        c.tick(0xFFFFFFF0u + 60000u);
        assert(c.idle());
    }
    puts("controller ok");
}

// --------------------------------------------------------------------------------------------
struct VecLink : usbproto::Link {
    std::vector<uint8_t> out;
    size_t write(const uint8_t *p, size_t n) override { out.insert(out.end(), p, p + n); return n; }
};
struct NullPanel : usbproto::Panel {
    int presents = 0;
    void present(const uint8_t *, uint16_t, uint16_t, uint16_t, uint16_t, bool) override { presents++; }
};
struct FakeHooks : usbproto::IdleHooks {
    uint8_t chunkResult = 0;
    uint32_t lastTotal = 0, lastOff = 0;
    size_t lastN = 0;
    int chunks = 0, nLayouts = 3, timeoutCalls = 0, selected = -1;
    uint16_t lastTimeout = 0;
    uint8_t layoutChunk(uint32_t t, uint32_t o, const uint8_t *, size_t n) override
    {
        chunks++; lastTotal = t; lastOff = o; lastN = n;
        return chunkResult;
    }
    bool selectLayout(uint8_t i) override { if (i >= nLayouts) return false; selected = i; return true; }
    void timeoutChanged(uint16_t s) override { timeoutCalls++; lastTimeout = s; }
};

static const usbproto::Info kInfo = {"host-test", 3, 49152, 8192};

static std::vector<uint8_t> mk(uint8_t type, uint8_t seq, const std::vector<uint8_t> &p = {})
{
    std::vector<uint8_t> f(usbproto::HDR_LEN + p.size() + usbproto::CRC_LEN);
    size_t n = usbproto::encodeFrame(type, seq, p.data(), (uint16_t)p.size(), f.data(), f.size());
    assert(n == f.size());
    return f;
}

// last frame the panel wrote (type, payload)
struct Reply { uint8_t type; std::vector<uint8_t> p; };
static Reply lastReply(const VecLink &l)
{
    usbproto::Parser ps;
    static Reply r;
    r = Reply{0, {}};
    ps.feed(l.out.data(), l.out.size(),
            [](void *, const usbproto::Frame &f) { r.type = f.type; r.p.assign(f.payload, f.payload + f.len); }, nullptr);
    return r;
}
static bool isNak(const VecLink &l, uint8_t reason)
{
    Reply r = lastReply(l);
    return r.type == usbproto::T_NAK && r.p.size() == 2 && r.p[1] == reason;
}
static bool isAck(const VecLink &l) { return lastReply(l).type == usbproto::T_ACK; }

static void send(usbproto::Device &d, const std::vector<uint8_t> &f, uint32_t now)
{
    d.onBytes(f.data(), f.size(), now);
}

static void testDevice()
{
    using namespace usbproto;
    {  // MODE variants
        VecLink link; NullPanel panel; FakeHooks hk; Device d(link, panel, kInfo, &hk);
        d.start(0);
        send(d, mk(T_MODE, 1, {MODE_HOST}), 100);
        assert(isAck(link) && d.hostOwnsDisplay() && d.idle().timeoutS() == 60 && hk.timeoutCalls == 0);
        send(d, mk(T_MODE, 2, {MODE_HOST, 20, 0}), 200);  // timeout 20 s
        assert(isAck(link) && d.idle().timeoutS() == 20 && hk.lastTimeout == 20 && hk.timeoutCalls == 1);
        send(d, mk(T_MODE, 3, {MODE_HOST, 0, 0}), 300);  // 0 = keep, not persisted again
        assert(isAck(link) && d.idle().timeoutS() == 20 && hk.timeoutCalls == 1);
        send(d, mk(T_MODE, 4, {MODE_HOST, 100, 0, 2}), 400);  // timeout 100 + layout 2
        assert(isAck(link) && hk.selected == 2 && d.idle().timeoutS() == 100);
        // refusals change nothing at all
        int calls = hk.timeoutCalls;
        send(d, mk(T_MODE, 5, {MODE_HOST, 14, 0}), 500);
        assert(isNak(link, NAK_BAD_ARG) && d.idle().timeoutS() == 100);
        send(d, mk(T_MODE, 6, {MODE_HOST, 0x11, 0x0E}), 500);  // 3601
        assert(isNak(link, NAK_BAD_ARG));
        send(d, mk(T_MODE, 7, {MODE_PANEL, 30, 0, 9}), 500);  // layout 9 does not exist: no mode change either
        assert(isNak(link, NAK_BAD_ARG) && d.hostOwnsDisplay() && d.idle().timeoutS() == 100 && hk.timeoutCalls == calls);
        send(d, mk(T_MODE, 8, {7}), 500);
        assert(isNak(link, NAK_BAD_ARG));
        send(d, mk(T_MODE, 9, {MODE_HOST, 20}), 500);  // len 2
        assert(isNak(link, NAK_BAD_LEN));
        send(d, mk(T_MODE, 10, {MODE_HOST, 20, 0, 0, 0}), 500);  // len 5
        assert(isNak(link, NAK_BAD_LEN));
        send(d, mk(T_MODE, 11, {}), 500);
        assert(isNak(link, NAK_BAD_LEN));
        send(d, mk(T_MODE, 12, {MODE_HOST, 0, 0, 0xFF}), 500);  // 0xFF = keep selection
        assert(isAck(link));
    }
    {  // no hooks: layout selection impossible, IDLE_LAYOUT unsupported, timeouts still work
        VecLink link; NullPanel panel; Device d(link, panel, kInfo);
        send(d, mk(T_IDLE_LAYOUT, 1, std::vector<uint8_t>(9, 1)), 0);
        assert(isNak(link, NAK_UNSUPPORTED));
        send(d, mk(T_MODE, 2, {MODE_HOST, 15, 0, 1}), 0);
        assert(isNak(link, NAK_BAD_ARG));
        send(d, mk(T_MODE, 3, {MODE_HOST, 15, 0}), 0);
        assert(isAck(link) && d.idle().timeoutS() == 15);
    }
    {  // IDLE_LAYOUT chunk framing and hook results
        VecLink link; NullPanel panel; FakeHooks hk; Device d(link, panel, kInfo, &hk);
        std::vector<uint8_t> p = {0x10, 0, 0, 0, 0x04, 0, 0, 0, 0xAA, 0xBB};  // total 16, offset 4, 2 bytes
        send(d, mk(T_IDLE_LAYOUT, 1, p), 0);
        assert(isAck(link) && hk.lastTotal == 16 && hk.lastOff == 4 && hk.lastN == 2);
        send(d, mk(T_IDLE_LAYOUT, 2, std::vector<uint8_t>(8, 0)), 0);  // no data at all
        assert(isNak(link, NAK_BAD_LEN) && hk.chunks == 1);
        send(d, mk(T_IDLE_LAYOUT, 3, std::vector<uint8_t>(3, 0)), 0);
        assert(isNak(link, NAK_BAD_LEN));
        hk.chunkResult = NAK_BAD_DATA;
        send(d, mk(T_IDLE_LAYOUT, 4, p), 0);
        assert(isNak(link, NAK_BAD_DATA));
        hk.chunkResult = NAK_STORAGE;
        send(d, mk(T_IDLE_LAYOUT, 5, p), 0);
        assert(isNak(link, NAK_STORAGE));
    }
    {  // timeline: HOST, silence -> idle, RECT while idle (auto) wakes and is served; a new HELLO follows
        VecLink link; NullPanel panel; Device d(link, panel, kInfo);
        d.start(0);
        send(d, mk(T_MODE, 1, {MODE_HOST, 20, 0}), 1000);
        std::vector<uint8_t> rect = {0, 0, 0, 0, 8, 0, 1, 0, 0xFF};
        send(d, mk(T_RECT, 2, rect), 2000);
        assert(isAck(link) && d.hostOwnsDisplay());
        d.tick(2000 + 19999);
        assert(d.hostOwnsDisplay());
        d.tick(2000 + 20000);
        assert(d.idle().idle() && !d.hostOwnsDisplay() && d.mode() == MODE_PANEL);
        d.tick(2000 + 20002);
        assert(!d.alive());
        send(d, mk(T_REFRESH, 3, {0}), 30000);  // wakes -> HOST; the dirty rect survived idle
        assert(d.hostOwnsDisplay() && panel.presents == 1);
        assert(isAck(link));
    }
    {  // pinned idle (MODE PANEL): RECT/REFRESH refused, heartbeat answered, MODE HOST resumes
        VecLink link; NullPanel panel; Device d(link, panel, kInfo);
        send(d, mk(T_MODE, 1, {MODE_PANEL}), 0);
        assert(isAck(link) && d.idle().idle() && d.idle().pinned());
        send(d, mk(T_RECT, 2, {0, 0, 0, 0, 8, 0, 1, 0, 0xFF}), 10);
        assert(isNak(link, NAK_WRONG_MODE));
        send(d, mk(T_REFRESH, 3, {1}), 20);
        assert(isNak(link, NAK_WRONG_MODE) && panel.presents == 0);
        send(d, mk(T_HEARTBEAT, 4), 30);
        assert(isAck(link) && d.idle().idle());
        send(d, mk(T_MODE, 5, {MODE_HOST}), 40);
        assert(d.hostOwnsDisplay());
    }
    {  // heartbeat every 5 s with timeout 15 never idles; one late tick (10 s) is still tolerated
        VecLink link; NullPanel panel; Device d(link, panel, kInfo);
        d.start(0);
        send(d, mk(T_MODE, 1, {MODE_HOST, 15, 0}), 0);
        uint32_t t = 0;
        for (int i = 0; i < 40; i++) {
            t += (i == 20 ? 10000 : 5000);
            send(d, mk(T_HEARTBEAT, (uint8_t)(i + 2)), t);
            d.tick(t);
            assert(d.hostOwnsDisplay());
        }
    }
    {  // boot with nobody: idle after the default timeout, wakes on the first frame (WAIT: RECT refused until MODE HOST)
        VecLink link; NullPanel panel; Device d(link, panel, kInfo);
        d.start(5000);
        d.tick(5000 + 59999);
        assert(!d.idle().idle());
        d.tick(5000 + 60000);
        assert(d.idle().idle());
        send(d, mk(T_HEARTBEAT, 1), 70000);
        assert(!d.idle().idle() && !d.hostOwnsDisplay());
        send(d, mk(T_RECT, 2, {0, 0, 0, 0, 8, 0, 1, 0, 0xFF}), 70001);
        assert(isNak(link, NAK_WRONG_MODE));
    }
    {  // persisted timeout restore path used by the firmware at boot
        VecLink link; NullPanel panel; Device d(link, panel, kInfo);
        assert(d.setIdleTimeoutS(120) && d.idle().timeoutS() == 120);
        assert(!d.setIdleTimeoutS(5) && d.idle().timeoutS() == 120);
    }
    puts("device ok");
}

// --------------------------------------------------------------------------------------------
static void put32(std::vector<uint8_t> &v, uint32_t x) { for (int i = 0; i < 4; i++) v.push_back((uint8_t)(x >> (8 * i))); }
static void put16(std::vector<uint8_t> &v, uint16_t x) { v.push_back((uint8_t)x); v.push_back((uint8_t)(x >> 8)); }

struct StoreSpec {
    std::vector<std::vector<uint8_t>> layouts;
    uint8_t active = 0, flags = 0;
    uint16_t rotateS = 0;
    std::vector<uint8_t> cfg;
};
static std::vector<uint8_t> fallback() { return std::vector<uint8_t>(kFallbackLayout, kFallbackLayout + kFallbackLayoutLen); }
static std::vector<uint8_t> refixCrc(std::vector<uint8_t> v)
{
    uint32_t c = usbproto::crc32(v.data(), v.size() - 4);
    for (int i = 0; i < 4; i++) v[v.size() - 4 + i] = (uint8_t)(c >> (8 * i));
    return v;
}
static std::vector<uint8_t> makeStore(const StoreSpec &s, uint8_t nOverride = 0xFF)
{
    std::vector<uint8_t> v = {'I', 'S', 'T', 'R', 1, nOverride == 0xFF ? (uint8_t)s.layouts.size() : nOverride, s.active, s.flags};
    put16(v, s.rotateS);
    put16(v, (uint16_t)s.cfg.size());
    v.insert(v.end(), s.cfg.begin(), s.cfg.end());
    for (auto &l : s.layouts) { put16(v, (uint16_t)l.size()); v.insert(v.end(), l.begin(), l.end()); }
    put32(v, 0);
    return refixCrc(v);
}
static std::vector<uint8_t> tlv(uint8_t tag, const std::string &val)
{
    std::vector<uint8_t> v = {tag, (uint8_t)val.size()};
    v.insert(v.end(), val.begin(), val.end());
    return v;
}
static std::vector<uint8_t> cat(std::vector<uint8_t> a, const std::vector<uint8_t> &b) { a.insert(a.end(), b.begin(), b.end()); return a; }

static StoreError parse(const std::vector<uint8_t> &b) { StoreView v; return parseStore(b.data(), b.size(), v); }

static void testParseStore()
{
    StoreSpec s;
    s.layouts = {fallback(), fallback()};
    s.active = 1; s.flags = 1; s.rotateS = 30;
    s.cfg = cat(cat(tlv(CT_WIFI_SSID, "my net"), tlv(CT_STEAM_KEY, "KEY")), cat(tlv(CT_UNITS, std::string(1, '\1')),
                tlv(CT_STEAM_APPID, std::string("\xDA\x02\x00\x00", 4))));
    auto good = makeStore(s);
    StoreView v;
    assert(parseStore(good.data(), good.size(), v) == ST_OK);
    assert(v.nLayouts == 2 && v.active == 1 && v.rotate && v.rotateS == 30);
    assert(!strcmp(v.cfg.wifiSsid, "my net") && !strcmp(v.cfg.steamKey, "KEY") && v.cfg.units == 1 && v.cfg.steamAppId == 730);
    assert(v.cfg.has(CT_WIFI_SSID) && !v.cfg.has(CT_WIFI_PASS) && v.layoutLen[0] == kFallbackLayoutLen);

    // every header / section rule
    assert(parse(std::vector<uint8_t>(good.begin(), good.begin() + 10)) == ST_TOO_SHORT);
    assert(parse(std::vector<uint8_t>(MAX_STORE_BYTES + 1, 0)) == ST_TOO_BIG);
    auto b = good; b[0] = 'X'; assert(parse(refixCrc(b)) == ST_BAD_MAGIC);
    b = good; b[4] = 2; assert(parse(refixCrc(b)) == ST_BAD_VERSION);
    b = good; b[10] ^= 1; assert(parse(b) == ST_BAD_CRC);
    b = good; b.back() ^= 1; assert(parse(b) == ST_BAD_CRC);
    { StoreSpec t = s; t.layouts.clear(); assert(parse(makeStore(t)) == ST_BAD_HEADER); }
    { StoreSpec t = s; t.layouts.assign(9, fallback()); assert(parse(makeStore(t)) == ST_BAD_HEADER); }
    { StoreSpec t = s; t.active = 2; assert(parse(makeStore(t)) == ST_BAD_HEADER); }
    { StoreSpec t = s; t.flags = 2; assert(parse(makeStore(t)) == ST_BAD_HEADER); }
    { StoreSpec t = s; t.rotateS = 9; assert(parse(makeStore(t)) == ST_BAD_HEADER); }
    { StoreSpec t = s; t.rotateS = 10; assert(parse(makeStore(t)) == ST_OK); }
    { StoreSpec t = s; t.flags = 0; t.rotateS = 1; assert(parse(makeStore(t)) == ST_OK); }  // rotate_s ignored when off
    { StoreSpec t = s; assert(parse(makeStore(t, 3)) == ST_BAD_HEADER); }  // claims 3 layouts, has 2
    { StoreSpec t = s; assert(parse(makeStore(t, 1)) == ST_BAD_HEADER); }  // claims 1: trailing layout bytes
    // config rules
    auto withCfg = [&](std::vector<uint8_t> c) { StoreSpec t = s; t.cfg = c; return parse(makeStore(t)); };
    assert(withCfg({}) == ST_OK);
    assert(withCfg(cat(tlv(CT_TZ, "a"), tlv(CT_TZ, "b"))) == ST_BAD_CONFIG);        // duplicate
    assert(withCfg({0, 1, 'x'}) == ST_BAD_CONFIG);                                   // tag 0
    assert(withCfg({10, 1, 'x'}) == ST_BAD_CONFIG);                                  // unknown tag
    assert(withCfg(tlv(CT_WIFI_SSID, std::string(33, 'a'))) == ST_BAD_CONFIG);       // too long
    assert(withCfg(tlv(CT_WIFI_SSID, std::string(32, 'a'))) == ST_OK);
    assert(withCfg(tlv(CT_WIFI_PASS, std::string(64, 'a'))) == ST_BAD_CONFIG);
    assert(withCfg(tlv(CT_LAT, std::string(16, '1'))) == ST_BAD_CONFIG);
    assert(withCfg(tlv(CT_STEAM_KEY, std::string(41, 'a'))) == ST_BAD_CONFIG);
    assert(withCfg(tlv(CT_STEAM_ID, std::string(21, '1'))) == ST_BAD_CONFIG);
    assert(withCfg(tlv(CT_TZ, std::string("a\nb"))) == ST_BAD_CONFIG);               // control byte
    assert(withCfg(tlv(CT_TZ, std::string("a\0b", 3))) == ST_BAD_CONFIG);            // NUL
    assert(withCfg(tlv(CT_UNITS, std::string(1, '\2'))) == ST_BAD_CONFIG);
    assert(withCfg(tlv(CT_UNITS, std::string(2, '\0'))) == ST_BAD_CONFIG);
    assert(withCfg(tlv(CT_STEAM_APPID, std::string(3, '\1'))) == ST_BAD_CONFIG);
    assert(withCfg({CT_TZ, 5, 'a'}) == ST_BAD_CONFIG);                               // value runs past section
    assert(withCfg({CT_TZ}) == ST_BAD_CONFIG);                                       // truncated header
    // a corrupt layout inside refuses the whole store
    { StoreSpec t = s; t.layouts[1][20] ^= 0xFF; assert(parse(makeStore(t)) == ST_BAD_LAYOUT); }
    { StoreSpec t = s; t.layouts[0].resize(10); assert(parse(makeStore(t)) == ST_BAD_LAYOUT); }
    { StoreSpec t = s; t.layouts[0].clear(); assert(parse(makeStore(t)) == ST_BAD_HEADER); }  // zero-length entry
    puts("parseStore ok");
}

// --------------------------------------------------------------------------------------------
struct RamFlash : Flash {
    std::vector<uint8_t> mem = std::vector<uint8_t>(2 * SLOT_BYTES, 0xFF);
    int ops = 0, failAt = -1;  // fail (power cut) when `ops` reaches failAt; ops counts erase+write
    size_t size() const override { return mem.size(); }
    bool read(size_t off, uint8_t *d, size_t n) override
    {
        if (off + n > mem.size()) return false;
        memcpy(d, &mem[off], n);
        return true;
    }
    bool erase(size_t off, size_t n) override
    {
        if (off % SECTOR || n % SECTOR || off + n > mem.size() || ops++ == failAt) return false;
        memset(&mem[off], 0xFF, n);
        return true;
    }
    bool write(size_t off, const uint8_t *s, size_t n) override
    {
        if (off + n > mem.size() || ops++ == failAt) return false;
        for (size_t i = 0; i < n; i++) {
            assert((mem[off + i] == 0xFF || mem[off + i] == s[i]) && "write into unerased flash");
            mem[off + i] = s[i];
        }
        return true;
    }
};

static std::vector<uint8_t> storeWith(uint8_t active, uint16_t marker)
{
    StoreSpec s;
    s.layouts.assign(8, fallback());  // ~1.6 KB: several 256-byte writes per commit
    s.active = active;
    s.cfg = tlv(CT_LAT, std::to_string(marker));
    return makeStore(s);
}

static void testFlashStore()
{
    RamFlash f;
    {
        FlashStore st(f);
        std::vector<uint8_t> out;
        assert(!st.load(out));  // blank flash
        auto a = storeWith(0, 1), b = storeWith(1, 2), c = storeWith(0, 3);
        assert(st.commit(a.data(), a.size()) && st.seq() == 1);
        FlashStore st2(f);
        assert(st2.load(out) && out == a && st2.seq() == 1);
        assert(st2.commit(b.data(), b.size()));
        FlashStore st3(f);
        assert(st3.load(out) && out == b && st3.seq() == 2);
        assert(st3.commit(c.data(), c.size()));
        FlashStore st4(f);
        assert(st4.load(out) && out == c && st4.seq() == 3);
        // slot A/B alternate: seq 1 and 3 in one slot, 2 in the other
        uint8_t h0[20] = {}, h1[20] = {};
        f.read(0, h0, 20); f.read(SLOT_BYTES, h1, 20);
        assert((h0[4] == 3 && h1[4] == 2) || (h0[4] == 2 && h1[4] == 3));
        // corrupt the newest slot's blob: falls back to the older one
        int newest = h0[4] == 3 ? 0 : 1;
        f.mem[newest * SLOT_BYTES + SECTOR + 30] ^= 0x55;
        FlashStore st5(f);
        assert(st5.load(out) && out == b && st5.seq() == 2);
        // ... and a commit after that goes into the corrupt slot, newer than the survivor
        assert(st5.commit(a.data(), a.size()));
        FlashStore st6(f);
        assert(st6.load(out) && out == a && st6.seq() == 3);
        // corrupt a header byte of both slots: nothing valid left
        f.mem[10] ^= 1; f.mem[SLOT_BYTES + 10] ^= 1;
        FlashStore st7(f);
        assert(!st7.load(out));
        // oversize / empty commits refused
        assert(!st7.commit(a.data(), 0) && !st7.commit(a.data(), MAX_STORE_BYTES + 1));
    }
    {  // power cut at EVERY erase/write of a commit: the previous store always survives intact
        auto base = storeWith(0, 10), next = storeWith(1, 20);
        RamFlash probe;
        { FlashStore s0(probe); assert(s0.commit(base.data(), base.size())); probe.ops = 0; assert(s0.commit(next.data(), next.size())); }
        int total = probe.ops;
        assert(total >= 4);  // erase + blob writes + header
        for (int k = 0; k < total; k++) {
            RamFlash g;
            { FlashStore s0(g); assert(s0.commit(base.data(), base.size())); }
            g.ops = 0; g.failAt = k;
            { FlashStore s1(g); std::vector<uint8_t> o; assert(s1.load(o)); assert(!s1.commit(next.data(), next.size())); }
            g.failAt = -1;
            FlashStore s2(g);
            std::vector<uint8_t> o;
            assert(s2.load(o) && o == base);  // reboot after the cut: old store, never a partial one
            assert(s2.commit(next.data(), next.size()));  // and the next upload still works
            FlashStore s3(g);
            assert(s3.load(o) && o == next);
        }
    }
    {  // flash too small
        struct Tiny : RamFlash { size_t size() const override { return SLOT_BYTES - 1; } } t;
        FlashStore st(t);
        auto a = storeWith(0, 1);
        assert(!st.commit(a.data(), a.size()));  // partition smaller than one slot
    }
    puts("flash store ok");
}

// --------------------------------------------------------------------------------------------
struct FakeSink : StoreSink {
    int commits = 0;
    uint8_t result = 0;
    std::vector<uint8_t> last;
    uint8_t commit(const uint8_t *b, size_t n) override { commits++; last.assign(b, b + n); return result; }
};

static uint8_t feed(Uploader &u, const std::vector<uint8_t> &blob, uint32_t total, size_t off, size_t n)
{
    return u.onChunk(total, (uint32_t)off, blob.data() + off, n);
}

static void testUploader()
{
    auto blob = storeWith(1, 7);
    uint32_t total = (uint32_t)blob.size();
    {
        FakeSink sink; Uploader u(sink);
        size_t o = 0;
        while (o < blob.size()) {
            size_t n = blob.size() - o < 100 ? blob.size() - o : 100;
            assert(feed(u, blob, total, o, n) == 0);
            o += n;
            if (o < blob.size()) assert(sink.commits == 0 && u.active());
        }
        assert(sink.commits == 1 && sink.last == blob && !u.active() && u.received() == 0);
        // lost ACK on the final chunk: the host repeats it
        size_t lastOff = blob.size() - (blob.size() % 100 ? blob.size() % 100 : 100);
        assert(feed(u, blob, total, lastOff, blob.size() - lastOff) == 0 && sink.commits == 1);
        // a stale non-final chunk after completion is refused
        assert(feed(u, blob, total, 100, 100) == NAK_BAD_ARG_);
    }
    {  // duplicate (retransmit) of an earlier chunk is ACKed without effect; gaps and total changes are not
        FakeSink sink; Uploader u(sink);
        assert(feed(u, blob, total, 0, 100) == 0 && feed(u, blob, total, 100, 100) == 0);
        assert(feed(u, blob, total, 100, 100) == 0 && u.received() == 200);
        std::vector<uint8_t> other = blob; other[150] ^= 0xFF;
        assert(u.onChunk(total, 100, other.data() + 100, 100) == NAK_BAD_ARG_);   // same offset, different bytes
        assert(feed(u, blob, total, 300, 50) == NAK_BAD_ARG_);                      // gap
        assert(u.onChunk(total + 1, 200, blob.data() + 200, 10) == NAK_BAD_ARG_);   // total changed mid upload
        assert(feed(u, blob, total, 200, 100) == 0);                                // upload still alive
    }
    {  // restart at offset 0 drops the partial upload
        FakeSink sink; Uploader u(sink);
        assert(feed(u, blob, total, 0, 100) == 0 && feed(u, blob, total, 100, 100) == 0);
        assert(feed(u, blob, total, 0, 50) == 0 && u.received() == 50);
    }
    {  // length rules
        FakeSink sink; Uploader u(sink);
        assert(u.onChunk(0, 0, blob.data(), 10) == NAK_BAD_LEN_);                       // total 0
        assert(u.onChunk(MAX_STORE_BYTES + 1, 0, blob.data(), 10) == NAK_BAD_LEN_);    // over the cap
        assert(u.onChunk(total, 0, blob.data(), 0) == NAK_BAD_LEN_);                    // empty chunk
        assert(u.onChunk(total, total - 5, blob.data(), 10) == NAK_BAD_LEN_);           // runs past total
        assert(u.onChunk(total, 100, blob.data(), 10) == NAK_BAD_ARG_);                 // no upload in progress
        assert(sink.commits == 0);
    }
    {  // validation failure -> BAD_DATA, nothing stored, state reset so the host can retry from 0
        FakeSink sink; Uploader u(sink);
        auto bad = blob; bad[40] ^= 0xFF;
        assert(u.onChunk(total, 0, bad.data(), total) == NAK_BAD_DATA_ && sink.commits == 0 && !u.active());
        assert(u.onChunk(total, 0, blob.data(), total) == 0 && sink.commits == 1);
    }
    {  // flash failure -> STORAGE, upload reset
        FakeSink sink; sink.result = NAK_STORAGE_; Uploader u(sink);
        assert(u.onChunk(total, 0, blob.data(), total) == NAK_STORAGE_ && !u.active());
        sink.result = 0;
        assert(u.onChunk(total, 0, blob.data(), total) == 0);
    }
    {  // single-chunk store at the size cap boundary: exactly the cap is accepted by the framing layer
        FakeSink sink; Uploader u(sink);
        std::vector<uint8_t> big(MAX_STORE_BYTES, 0);
        assert(u.onChunk((uint32_t)big.size(), 0, big.data(), 4000) == 0);
    }
    puts("uploader ok");
}

static void testLayoutSet()
{
    LayoutSet ls;
    ls.setFallback(kFallbackLayout, kFallbackLayoutLen);
    assert(!ls.fromStore() && ls.count() == 1 && ls.current().nWidgets > 0 && ls.config() == nullptr);
    assert(!ls.select(1) && ls.select(0));
    assert(!ls.tick(1000000));  // no rotation without a store

    StoreSpec s;
    s.layouts = {fallback(), fallback(), fallback()};
    s.active = 2; s.flags = 1; s.rotateS = 10;
    s.cfg = tlv(CT_TZ, "UTC0");
    auto blob = makeStore(s);
    std::vector<uint8_t> bad = blob; bad[20] ^= 1;
    assert(!ls.setStore(bad) && !ls.fromStore());           // rejected: still the fallback
    assert(ls.setStore(blob) && ls.fromStore() && ls.count() == 3 && ls.index() == 2);
    assert(ls.config() && ls.config()->has(CT_TZ));
    assert(!ls.setStore(bad) && ls.fromStore() && ls.count() == 3);  // a bad upload never replaces a good set
    // rotation: every 10 s, wraps, restart() re-bases
    ls.restart(1000);
    assert(!ls.tick(10999) && ls.index() == 2);
    assert(ls.tick(11000) && ls.index() == 0);
    assert(ls.tick(21000) && ls.index() == 1);
    assert(ls.tick(31000) && ls.index() == 2);
    assert(!ls.select(3) && ls.select(1) && ls.index() == 1);
    ls.restart(50000);
    assert(!ls.tick(59999) && ls.tick(60000));
    // pinned set (rotate off): never rotates
    s.flags = 0;
    assert(ls.setStore(makeStore(s)));
    assert(!ls.tick(0xFFFFFFF0u));
    // single layout with rotate on: nothing to rotate to
    s.layouts = {fallback()}; s.active = 0; s.flags = 1;
    assert(ls.setStore(makeStore(s)) && !ls.tick(100000));
    ls.clearStore();
    assert(!ls.fromStore() && ls.count() == 1 && ls.current().nWidgets > 0);
    puts("layoutset ok");
}

// --------------------------------------------------------------------------------------------
struct FakeEpd : Presenter {
    std::vector<uint8_t> shadow = std::vector<uint8_t>(MONO_BYTES, 0);
    bool known = false;
    int fulls = 0, partials = 0, grays = 0;
    uint16_t px = 0, py = 0, pw = 0, ph = 0;
    const uint8_t *glass() override { return known ? shadow.data() : nullptr; }
    void full(const uint8_t *fb) override { memcpy(shadow.data(), fb, MONO_BYTES); known = true; fulls++; }
    void partial(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h) override
    {
        assert(x % 8 == 0 && w % 8 == 0 && w > 0 && h > 0 && x + w <= SCREEN_W && y + h <= SCREEN_H);
        for (uint16_t r = y; r < y + h; r++) memcpy(&shadow[r * (SCREEN_W / 8) + x / 8], fb + r * (SCREEN_W / 8) + x / 8, w / 8);
        px = x; py = y; pw = w; ph = h;
        partials++;
    }
    void gray4(const uint8_t *) override { grays++; known = false; }
};
struct ClockData : Data {
    int64_t epoch = 0;
    Value get(uint8_t, uint8_t f, uint8_t) const override { return f == 0 && epoch ? Value::time(epoch) : Value(); }
};

static void testPainter()
{
    PosixEnv env;
    env.globalTz = "UTC0";
    ClockData data;
    data.epoch = 1794316020;  // 2026-11-10 13:07 UTC
    Layout mono;
    assert(decodeLayout(kFallbackLayout, kFallbackLayoutLen, mono) == DEC_OK && mono.mode == RM_MONO);
    mono.fullEvery = 3;
    FakeEpd epd;
    Painter p(epd);
    assert(p.paint(mono, data, env) == PAINT_FULL && epd.fulls == 1);             // first paint is always a full refresh
    assert(p.paint(mono, data, env) == PAINT_SKIPPED && epd.partials == 0);        // identical picture: nothing sent
    for (int i = 0; i < 3; i++) {                                                  // fullEvery = 3 partials, then a full one
        data.epoch += 60 * 61;                                                     // a different time every time
        assert(p.paint(mono, data, env) == PAINT_PARTIAL);
        assert(epd.pw > 0 && epd.pw <= SCREEN_W && epd.px % 8 == 0 && p.partialsSinceFull() == (uint32_t)i + 1);
    }
    data.epoch += 60 * 61;
    assert(p.paint(mono, data, env) == PAINT_FULL && p.partialsSinceFull() == 0 && epd.fulls == 2);
    // the window covers all the change: the glass shadow equals a fresh render
    data.epoch += 60 * 37;
    assert(p.paint(mono, data, env) == PAINT_PARTIAL);
    Painter fresh(epd);
    FakeEpd e2;
    Painter p2(e2);
    p2.paint(mono, data, env);
    assert(epd.shadow == e2.shadow);
    // unknown glass (host drew, reset): next paint is full even though the picture is unchanged
    epd.known = false;
    assert(p.paint(mono, data, env) == PAINT_FULL);
    p.invalidate();
    assert(p.paint(mono, data, env) == PAINT_FULL);
    // no data at all still renders (placeholders) and does not crash
    ClockData none;
    assert(p.paint(mono, none, env) == PAINT_PARTIAL);

    // gray4: always a gray refresh, identical picture skipped, then mono needs a full refresh again
    Layout g = mono;
    g.mode = RM_GRAY4;
    assert(p.paint(g, data, env) == PAINT_GRAY && epd.grays == 1);
    assert(p.paint(g, data, env) == PAINT_SKIPPED && epd.grays == 1);
    data.epoch += 3600;
    assert(p.paint(g, data, env) == PAINT_GRAY && epd.grays == 2);
    assert(p.paint(mono, data, env) == PAINT_FULL);
    puts("painter ok");
}

int main()
{
    testController();
    testDevice();
    testParseStore();
    testFlashStore();
    testUploader();
    testLayoutSet();
    testPainter();
    puts("idle_mode host tests OK");
    return 0;
}

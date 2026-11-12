// Host-side harness for ota_usb.cpp (no Arduino, no flash): a fake two-slot flash and a fake
// boot environment drive the real Receiver state machine behind the real usbproto::Device.
//
//   ota_host --selftest
//       asserts: sha256 vectors, happy path, out-of-order / overflow / bad hash / bad image,
//       flash faults, power loss mid-write (old image still bootable), resume, mark-valid,
//       verify-timer rollback, legacy images, reboot timing incl. millis() wrap
//   ota_host --replay <slot_bytes> <state_out> [--fail-write-at N] [--corrupt-at N] < host_bytes > panel_bytes
//       feeds stdin to a Device+Receiver in 61-byte reads; stdout = every byte the panel wrote;
//       state_out = "committed=<0|1> reboot=<0|1> image_sha256=<hex of the slot to boot, or ->".
//       decky/tests/test_ota_cpp.py compares this byte for byte with the Python emulator.
//
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 ota_host.cpp ../src/ee05/ota_usb.cpp ../src/ee05/usbproto.cpp
#include "ota_usb.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>

using namespace usbproto;
using namespace ota_usb;

typedef std::vector<uint8_t> Bytes;

// ---------------------------------------------------------------------------- fakes
struct FakeFlash : Flash {
    size_t cap;
    Bytes slot[2];
    int runSlot = 0, bootSlot = 0;  // otadata: which slot the bootloader picks
    bool open = false;
    size_t written = 0;
    // fault injection
    long failWriteAt = -1, corruptAt = -1;
    bool failBegin = false, failCommit = false, failRead = false;
    int begins = 0, commits = 0, aborts = 0;

    explicit FakeFlash(size_t c) : cap(c)
    {
        slot[0].assign(c, 0xFF);
        slot[1].assign(c, 0xFF);
    }
    int inactive() const { return 1 - runSlot; }
    size_t capacity() override { return cap; }
    bool begin() override
    {
        begins++;
        if (failBegin) return false;
        slot[inactive()].assign(cap, 0xFF);  // real flash: sectors erased before write
        open = true;
        written = 0;
        return true;
    }
    bool write(size_t off, const uint8_t *p, size_t n) override
    {
        assert(open && off == written && "writes must be sequential from 0");
        if (failWriteAt >= 0 && (size_t)failWriteAt >= off && (size_t)failWriteAt < off + n) return false;
        assert(off + n <= cap);
        memcpy(&slot[inactive()][off], p, n);
        if (corruptAt >= 0 && (size_t)corruptAt >= off && (size_t)corruptAt < off + n)
            slot[inactive()][corruptAt] ^= 1;
        written += n;
        return true;
    }
    bool read(size_t off, uint8_t *p, size_t n) override
    {
        if (failRead || off + n > cap) return false;
        memcpy(p, &slot[inactive()][off], n);
        return true;
    }
    bool commit() override
    {
        commits++;
        bool ok = open && failCommit == false && slot[inactive()][0] == 0xE9;  // esp_ota_end magic check
        open = false;
        if (ok) bootSlot = inactive();
        return ok;
    }
    void abort() override
    {
        aborts++;
        open = false;
    }
};

struct FakeEnv : Env {
    FakeFlash &fl;
    bool usbFlag = false;          // NVS
    Boot state = Boot::Valid;      // otadata state of the running image
    bool timerArmed = false, rebooted = false;
    uint32_t timerMs = 0;
    int markValidCalls = 0;
    explicit FakeEnv(FakeFlash &f) : fl(f) {}
    Boot bootState() override
    {
        if (state == Boot::Valid && usbFlag) usbFlag = false;  // stale flag, as EspEnv does
        return state;
    }
    void markValid() override
    {
        markValidCalls++;
        state = Boot::Valid;
        usbFlag = false;
    }
    void setUsbPending(bool v) override { usbFlag = v; }
    void armVerifyTimer(uint32_t ms) override { timerArmed = true; timerMs = ms; }
    void disarmVerifyTimer() override { timerArmed = false; }
    void reboot() override { rebooted = true; }

    // what the bootloader does at reset
    void powerCycle()
    {
        rebooted = false;
        timerArmed = false;
        if (fl.bootSlot != fl.runSlot) {  // new image: pending until confirmed
            fl.runSlot = fl.bootSlot;
            state = usbFlag ? Boot::PendingUsb : Boot::PendingOther;
        }
    }
    // verify timer expiry: esp_ota_mark_app_invalid_rollback_and_reboot
    void fireTimer()
    {
        assert(timerArmed);
        fl.bootSlot = 1 - fl.runSlot;
        fl.runSlot = fl.bootSlot;
        state = Boot::Valid;  // the old image was confirmed long ago
        timerArmed = false;
        rebooted = false;
    }
};

struct VecLink : Link {
    Bytes out;
    size_t write(const uint8_t *p, size_t n) override
    {
        out.insert(out.end(), p, p + n);
        return n;
    }
};

struct NullPanel : Panel {
    void present(const uint8_t *, uint16_t, uint16_t, uint16_t, uint16_t, bool) override {}
};

static const Info kInfo = {"2.0.0", 0, 0, 8192};

struct Rep {
    uint8_t type, seq;
    Bytes payload;
};

struct Rig {
    FakeFlash &fl;
    FakeEnv &env;
    VecLink link;
    NullPanel panel;
    Receiver rx;
    Device dev;
    uint32_t now = 1000;
    uint8_t seq = 0;
    Rig(FakeFlash &f, FakeEnv &e) : fl(f), env(e), rx(f, e), dev(link, panel, kInfo)
    {
        rx.begin();
        dev.setExt(&rx);
        send(T_HELLO, {});  // open the session so the first frame does not trigger an extra HELLO
        link.out.clear();
    }
    static Bytes mk(uint8_t type, uint8_t seq, const Bytes &p)
    {
        Bytes f(HDR_LEN + p.size() + CRC_LEN);
        size_t n = encodeFrame(type, seq, p.data(), (uint16_t)p.size(), f.data(), f.size());
        assert(n == f.size());
        return f;
    }
    static void collect(void *ctx, const Frame &f)
    {
        static_cast<std::vector<Rep> *>(ctx)->push_back({f.type, f.seq, Bytes(f.payload, f.payload + f.len)});
    }
    std::vector<Rep> take()
    {
        Parser p;
        std::vector<Rep> v;
        p.feed(link.out.data(), link.out.size(), &collect, &v);
        link.out.clear();
        return v;
    }
    // send one host frame, return the panel's frames
    std::vector<Rep> send(uint8_t type, const Bytes &p)
    {
        Bytes f = mk(type, seq++, p);
        dev.onBytes(f.data(), f.size(), now);
        return take();
    }
    Rep one(uint8_t type, const Bytes &p)
    {
        auto v = send(type, p);
        assert(v.size() == 1);
        return v[0];
    }
};

static Bytes le32(uint32_t v) { return {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24)}; }
static Bytes cat(Bytes a, const Bytes &b)
{
    a.insert(a.end(), b.begin(), b.end());
    return a;
}

static Bytes sha(const Bytes &d)
{
    Sha256 h;
    Bytes out(SHA_LEN);
    h.update(d.data(), d.size());
    h.final(out.data());
    return out;
}

static Bytes beginPayload(const Bytes &img, const char *ver = "2.0.0")
{
    Bytes p = cat(le32((uint32_t)img.size()), sha(img));
    return cat(p, Bytes(ver, ver + strlen(ver)));
}
static Bytes dataPayload(uint32_t off, const Bytes &img, size_t n)
{
    return cat(le32(off), Bytes(img.begin() + off, img.begin() + off + n));
}

static Bytes makeImage(size_t n, uint8_t salt = 7)
{
    Bytes b(n);
    uint32_t x = 12345 + salt;
    for (auto &c : b) {
        x = x * 1664525u + 1013904223u;
        c = (uint8_t)(x >> 24);
    }
    b[0] = 0xE9;
    return b;
}

// rep is evaluated once (it sends a frame); the expected seq is read after that
#define EXPECT_ACK(repExpr, s, recv)                                                          \
    do {                                                                                      \
        const Rep rep_ = (repExpr);                                                           \
        assert(rep_.type == T_ACK && rep_.payload.size() == 5 && rep_.payload[0] == (s));     \
        uint32_t r_ = rep_.payload[1] | (rep_.payload[2] << 8) | (rep_.payload[3] << 16) |    \
                      ((uint32_t)rep_.payload[4] << 24);                                      \
        assert(r_ == (uint32_t)(recv));                                                       \
    } while (0)
#define EXPECT_NAK(repExpr, s, reason)                                                        \
    do {                                                                                      \
        const Rep rep_ = (repExpr);                                                           \
        assert(rep_.type == T_NAK && rep_.payload.size() == 2 && rep_.payload[0] == (s) &&    \
               rep_.payload[1] == (reason));                                                  \
    } while (0)

// returns the seq used for the last frame
static void streamAll(Rig &r, const Bytes &img, const char *ver = "2.0.0", size_t chunk = 1000)
{
    Rep b = r.one(T_OTA_BEGIN, beginPayload(img, ver));
    EXPECT_ACK(b, r.seq - 1, 0);
    for (uint32_t off = 0; off < img.size(); off += chunk) {
        size_t n = img.size() - off < chunk ? img.size() - off : chunk;
        Rep d = r.one(T_OTA_DATA, dataPayload(off, img, n));
        EXPECT_ACK(d, r.seq - 1, off + n);
    }
}

// ---------------------------------------------------------------------------- selftests
static void testSha256()
{
    auto hex = [](const Bytes &d) {
        std::string s;
        char b[3];
        for (uint8_t c : d) { snprintf(b, 3, "%02x", c); s += b; }
        return s;
    };
    assert(hex(sha({})) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    Bytes abc = {'a', 'b', 'c'};
    assert(hex(sha(abc)) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    const char *m = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";  // two-block padding case
    assert(hex(sha(Bytes(m, m + strlen(m)))) == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    Bytes million(1000000, 'a');
    assert(hex(sha(million)) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    // incremental updates of odd sizes == one shot; padding boundaries 55/56/63/64/65
    for (size_t n : {0, 1, 54, 55, 56, 57, 63, 64, 65, 119, 120, 128, 1000}) {
        Bytes d = makeImage(n ? n : 1);
        d.resize(n);
        Sha256 h;
        Bytes a(SHA_LEN);
        for (size_t i = 0; i < n;) { size_t k = (i % 7) + 1; if (i + k > n) k = n - i; h.update(d.data() + i, k); i += k; }
        h.final(a.data());
        assert(a == sha(d));
    }
}

static void testHappyPath()
{
    FakeFlash fl(64 * 1024);
    FakeEnv env(fl);
    Rig r(fl, env);
    Bytes img = makeImage(20000);
    streamAll(r, img);
    assert(fl.bootSlot == 0 && fl.runSlot == 0);  // not switched yet
    Rep e = r.one(T_OTA_END, {});
    assert(e.type == T_ACK && e.payload.size() == 1 && e.payload[0] == r.seq - 1);
    assert(fl.bootSlot == 1 && fl.runSlot == 0);
    assert(env.usbFlag && r.rx.committed());
    assert(fl.slot[1][0] == 0xE9 && Bytes(fl.slot[1].begin(), fl.slot[1].begin() + img.size()) == img);
    // reboot only after the delay so the ACK can leave the FIFO
    r.dev.tick(r.now + REBOOT_DELAY_MS - 1);
    assert(!env.rebooted);
    r.dev.tick(r.now + REBOOT_DELAY_MS);
    assert(env.rebooted);
    // resent END after commit: idempotent ACK; BEGIN / DATA: BUSY
    Rep e2 = r.one(T_OTA_END, {});
    assert(e2.type == T_ACK && e2.payload.size() == 1);
    EXPECT_NAK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, NAK_OTA_BUSY);
    EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(0, img, 10)), r.seq - 1, NAK_OTA_BUSY);
    // HELLO advertises OTA
    auto h = r.send(T_HELLO, {});
    assert(h.size() == 1 && h[0].type == T_HELLO);
    uint32_t caps = h[0].payload[10] | (h[0].payload[11] << 8) | (h[0].payload[12] << 16);
    assert(caps & CAP_OTA);
}

static void testRebootTimingWrap()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    Rig r(fl, env);
    r.now = 0xFFFFFF00u;  // millis() about to wrap
    Bytes img = makeImage(3000);
    streamAll(r, img);
    r.one(T_OTA_END, {});
    r.dev.tick(0xFFFFFF00u + 100);
    assert(!env.rebooted);
    r.dev.tick(0xFFFFFF00u + REBOOT_DELAY_MS);  // wraps past zero
    assert(env.rebooted);
}

static void testBadFramesAndStates()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    Rig r(fl, env);
    Bytes img = makeImage(5000);
    // no session
    EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(0, img, 10)), r.seq - 1, NAK_OTA_NO_SESSION);
    EXPECT_NAK(r.one(T_OTA_END, {}), r.seq - 1, NAK_OTA_NO_SESSION);
    // lengths
    Bytes ok = beginPayload(img);
    EXPECT_NAK(r.one(T_OTA_BEGIN, Bytes(ok.begin(), ok.begin() + 36)), r.seq - 1, NAK_BAD_LEN);  // no version
    EXPECT_NAK(r.one(T_OTA_BEGIN, cat(ok, Bytes(VERSION_MAX, 'x'))), r.seq - 1, NAK_BAD_LEN);     // version too long
    EXPECT_NAK(r.one(T_OTA_BEGIN, {}), r.seq - 1, NAK_BAD_LEN);
    EXPECT_NAK(r.one(T_OTA_DATA, Bytes(4, 0)), r.seq - 1, NAK_BAD_LEN);  // offset, no data
    EXPECT_NAK(r.one(T_OTA_END, Bytes(1, 0)), r.seq - 1, NAK_BAD_LEN);
    // args
    Bytes zero = ok;
    zero[0] = zero[1] = zero[2] = zero[3] = 0;
    EXPECT_NAK(r.one(T_OTA_BEGIN, zero), r.seq - 1, NAK_BAD_ARG);
    Bytes ctl = ok;
    ctl.back() = 0x07;  // non-printable version byte
    EXPECT_NAK(r.one(T_OTA_BEGIN, ctl), r.seq - 1, NAK_BAD_ARG);
    ctl.back() = 0x7F;
    EXPECT_NAK(r.one(T_OTA_BEGIN, ctl), r.seq - 1, NAK_BAD_ARG);
    // too big for the slot
    Bytes huge = ok;
    huge[2] = 0x10;  // 0x100000+ > 8192
    EXPECT_NAK(r.one(T_OTA_BEGIN, huge), r.seq - 1, NAK_OTA_TOO_BIG);
    assert(fl.begins == 0);  // none of the above touched flash
    // longest allowed version works
    Bytes v32 = beginPayload(img, "abcdefghijklmnopqrstuvwxyz012345");
    EXPECT_ACK(r.one(T_OTA_BEGIN, v32), r.seq - 1, 0);
}

static void testOutOfOrderOverflowAndDuplicates()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    Rig r(fl, env);
    Bytes img = makeImage(5000);
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 0);
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(0, img, 1000)), r.seq - 1, 1000);
    // skipped ahead: bytes lost on the wire
    EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(2000, img, 500)), r.seq - 1, NAK_OTA_BAD_OFFSET);
    // partial overlap with held data
    EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(500, img, 1000)), r.seq - 1, NAK_OTA_BAD_OFFSET);
    // exact resend of held data: ACK, nothing rewritten (write() asserts sequential)
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(0, img, 1000)), r.seq - 1, 1000);
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(300, img, 200)), r.seq - 1, 1000);
    // running past the declared size
    Bytes tail = cat(le32(4900), Bytes(200, 1));
    EXPECT_NAK(r.one(T_OTA_DATA, cat(le32(1000), Bytes(4092, 1))), r.seq - 1, NAK_OTA_TOO_BIG);
    EXPECT_NAK(r.one(T_OTA_DATA, tail), r.seq - 1, NAK_OTA_BAD_OFFSET);  // ahead AND past end: offset check first
    // u32 offset near wrap must not overflow the bounds check
    EXPECT_NAK(r.one(T_OTA_DATA, cat(le32(0xFFFFFFF0u), Bytes(64, 1))), r.seq - 1, NAK_OTA_BAD_OFFSET);
    // END before everything arrived
    EXPECT_NAK(r.one(T_OTA_END, {}), r.seq - 1, NAK_OTA_BAD_OFFSET);
    assert(fl.bootSlot == 0 && !env.usbFlag);
    // the transfer still completes afterwards
    for (uint32_t off = 1000; off < 5000; off += 1000) EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(off, img, 1000)), r.seq - 1, off + 1000);
    assert(r.one(T_OTA_END, {}).type == T_ACK);
}

static void testBadHashAndBadImage()
{
    {
        FakeFlash fl(8192);
        FakeEnv env(fl);
        Rig r(fl, env);
        Bytes img = makeImage(5000);
        fl.corruptAt = 3333;  // flash returns a flipped bit
        streamAll(r, img);
        EXPECT_NAK(r.one(T_OTA_END, {}), r.seq - 1, NAK_OTA_HASH_MISMATCH);
        assert(fl.bootSlot == 0 && fl.commits == 0 && !env.usbFlag && !env.rebooted);
        r.dev.tick(r.now + 5000);
        assert(!env.rebooted);
        EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(0, img, 10)), r.seq - 1, NAK_OTA_NO_SESSION);  // session gone
        fl.corruptAt = -1;
        streamAll(r, img);  // clean retry from scratch works
        assert(r.one(T_OTA_END, {}).type == T_ACK && fl.bootSlot == 1);
    }
    {
        FakeFlash fl(8192);
        FakeEnv env(fl);
        Rig r(fl, env);
        Bytes img = makeImage(5000);
        img[0] = 0x00;  // hash matches (we hash what we send) but it is not an app image
        streamAll(r, img);
        EXPECT_NAK(r.one(T_OTA_END, {}), r.seq - 1, NAK_OTA_BAD_IMAGE);
        assert(fl.bootSlot == 0 && !env.usbFlag && fl.commits == 1);
    }
}

static void testFlashFaults()
{
    {
        FakeFlash fl(8192);
        FakeEnv env(fl);
        Rig r(fl, env);
        fl.failBegin = true;
        Bytes img = makeImage(3000);
        EXPECT_NAK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, NAK_OTA_FLASH_FAIL);
        EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(0, img, 10)), r.seq - 1, NAK_OTA_NO_SESSION);
    }
    {
        FakeFlash fl(8192);
        FakeEnv env(fl);
        Rig r(fl, env);
        Bytes img = makeImage(5000);
        fl.failWriteAt = 2500;
        EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 0);
        EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(0, img, 2000)), r.seq - 1, 2000);
        EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(2000, img, 1000)), r.seq - 1, NAK_OTA_FLASH_FAIL);
        assert(fl.aborts >= 1 && fl.bootSlot == 0);
        EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(2000, img, 1000)), r.seq - 1, NAK_OTA_NO_SESSION);
    }
    {
        FakeFlash fl(8192);
        FakeEnv env(fl);
        Rig r(fl, env);
        Bytes img = makeImage(3000);
        streamAll(r, img);
        fl.failRead = true;
        EXPECT_NAK(r.one(T_OTA_END, {}), r.seq - 1, NAK_OTA_FLASH_FAIL);
        assert(fl.bootSlot == 0 && !env.usbFlag);
    }
}

static void testPowerLossMidWrite()
{
    FakeFlash fl(64 * 1024);
    FakeEnv env(fl);
    Bytes old = makeImage(10000, 1), img = makeImage(30000, 2);
    // the running image (slot 0) is a known good one
    memcpy(fl.slot[0].data(), old.data(), old.size());
    {
        Rig r(fl, env);
        EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 0);
        for (uint32_t off = 0; off < 15000; off += 1000) EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(off, img, 1000)), r.seq - 1, off + 1000);
    }  // <- power cut: Receiver, Device, all RAM state gone
    env.powerCycle();
    assert(fl.bootSlot == 0 && fl.runSlot == 0);        // still booting the old image
    assert(Bytes(fl.slot[0].begin(), fl.slot[0].begin() + old.size()) == old);  // and it is intact
    assert(env.state == Boot::Valid && !env.usbFlag);
    {
        Rig r(fl, env);  // fresh boot: no timer, nothing pending
        assert(!env.timerArmed && !r.rx.pendingVerify());
        EXPECT_NAK(r.one(T_OTA_DATA, dataPayload(15000, img, 1000)), r.seq - 1, NAK_OTA_NO_SESSION);
        streamAll(r, img);  // restart from byte 0 succeeds
        assert(r.one(T_OTA_END, {}).type == T_ACK && fl.bootSlot == 1);
    }
}

static void testPowerLossBetweenFlagAndCommit()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    Bytes img = makeImage(3000);
    {
        Rig r(fl, env);
        streamAll(r, img);
        fl.failCommit = true;  // stands in for "power lost after setUsbPending(true), before commit"
        EXPECT_NAK(r.one(T_OTA_END, {}), r.seq - 1, NAK_OTA_BAD_IMAGE);
        env.usbFlag = true;  // as if the cleanup never ran
    }
    env.powerCycle();
    assert(fl.bootSlot == 0);
    Rig r(fl, env);  // boots the old image: bootState() sees Valid and clears the stale flag
    assert(!env.usbFlag && !env.timerArmed);
}

static void testResume()
{
    FakeFlash fl(16 * 1024);
    FakeEnv env(fl);
    Rig r(fl, env);
    Bytes img = makeImage(9000);
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 0);
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(0, img, 4000)), r.seq - 1, 4000);
    int begins = fl.begins;
    // same params again (host reconnected): resume at 4000, flash untouched
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 4000);
    assert(fl.begins == begins);
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(4000, img, 2500)), r.seq - 1, 6500);
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(6500, img, 2500)), r.seq - 1, 9000);
    // session survives host silence beyond the host timeout
    r.dev.tick(r.now + (uint32_t)r.dev.idle().timeoutS() * 1000u + 1);
    assert(r.rx.received() == 9000);
    r.send(T_HELLO, {});  // host session restarts with a HELLO
    // different version string = different image = restart from 0
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img, "2.0.1")), r.seq - 1, 0);
    assert(fl.begins == begins + 1 && fl.aborts >= 1);
    // different content likewise
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(0, img, 100)), r.seq - 1, 100);
    Bytes other = makeImage(9000, 99);
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(other, "2.0.1")), r.seq - 1, 0);
}

// busy() gates the bus-suspend fallback (usb_bus_watch.h): true while an image is in transit or committed, but an
// abandoned session must stop counting, or one aborted update would disable the fallback until the next reboot.
static void testBusy()
{
    FakeFlash fl(16 * 1024);
    FakeEnv env(fl);
    Rig r(fl, env);
    assert(!r.rx.busy(r.now) && !r.dev.extBusy(r.now));
    Bytes img = makeImage(9000);
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 0);
    assert(r.rx.busy(r.now) && r.dev.extBusy(r.now));
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(0, img, 4000)), r.seq - 1, 4000);
    assert(r.rx.busy(r.now + Receiver::BUSY_QUIET_MS - 1));
    assert(!r.rx.busy(r.now + Receiver::BUSY_QUIET_MS));   // host went away mid-transfer: not busy any more...
    EXPECT_ACK(r.one(T_OTA_BEGIN, beginPayload(img)), r.seq - 1, 4000);   // ...and busy again when it resumes
    assert(r.rx.busy(r.now) && r.rx.received() == 4000);
    r.now += 5000;
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(4000, img, 4000)), r.seq - 1, 8000);
    EXPECT_ACK(r.one(T_OTA_DATA, dataPayload(8000, img, 1000)), r.seq - 1, 9000);
    assert(r.rx.busy(r.now + Receiver::BUSY_QUIET_MS - 1));
    r.one(T_OTA_END, {});
    assert(r.rx.committed() && r.rx.busy(r.now) && r.rx.busy(r.now + 5 * Receiver::BUSY_QUIET_MS));   // committed: busy until the reboot
    r.dev.tick(r.now + REBOOT_DELAY_MS);
    assert(env.rebooted);
    // wrap-safe
    FakeFlash fl2(16 * 1024);
    FakeEnv env2(fl2);
    Rig w(fl2, env2);
    w.now = 0xFFFFFFF0u;
    EXPECT_ACK(w.one(T_OTA_BEGIN, beginPayload(img)), w.seq - 1, 0);
    assert(w.rx.busy(w.now + 100) && !w.rx.busy(w.now + Receiver::BUSY_QUIET_MS));   // 0xFFFFFFF0 + 10000 wraps past zero
}

static Bytes helloAck(uint16_t proto, const char *ver)
{
    Bytes p = {(uint8_t)proto, (uint8_t)(proto >> 8)};
    return cat(p, Bytes(ver, ver + strlen(ver)));
}

static void testMarkValid()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    Bytes img = makeImage(3000);
    {
        Rig r(fl, env);
        streamAll(r, img);
        r.one(T_OTA_END, {});
    }
    env.powerCycle();  // reboot into the new image
    assert(env.state == Boot::PendingUsb);
    Rig r(fl, env);
    assert(r.rx.pendingVerify() && env.timerArmed && env.timerMs == VERIFY_TIMEOUT_MS);
    // a plain HELLO request, heartbeats, other frames: not an acceptance
    r.send(T_HELLO, {});
    r.send(T_HEARTBEAT, {});
    assert(env.state == Boot::PendingUsb && env.markValidCalls == 0);
    // acks that do not match what this panel reported
    r.send(T_HELLO, helloAck(2, "2.0.0"));        // wrong protocol
    r.send(T_HELLO, helloAck(PROTO_VER, "1.9.9"));  // wrong version
    r.send(T_HELLO, helloAck(PROTO_VER, "2.0.0x")); // longer
    r.send(T_HELLO, helloAck(PROTO_VER, "2.0."));   // shorter
    r.send(T_HELLO, Bytes{1});                       // too short to carry a proto_ver
    assert(env.state == Boot::PendingUsb && env.markValidCalls == 0 && env.timerArmed);
    // the real ack: still answered with a HELLO, then the image is valid
    auto rep = r.send(T_HELLO, helloAck(PROTO_VER, "2.0.0"));
    assert(rep.size() == 1 && rep[0].type == T_HELLO);
    assert(env.state == Boot::Valid && env.markValidCalls == 1 && !env.timerArmed && !env.usbFlag);
    assert(!r.rx.pendingVerify());
    r.send(T_HELLO, helloAck(PROTO_VER, "2.0.0"));  // repeat is harmless
    assert(env.markValidCalls == 1);
}

static void testVerifyTimerRollback()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    Bytes old = makeImage(2000, 3), img = makeImage(3000);
    memcpy(fl.slot[0].data(), old.data(), old.size());
    {
        Rig r(fl, env);
        streamAll(r, img);
        r.one(T_OTA_END, {});
    }
    env.powerCycle();
    assert(fl.runSlot == 1 && env.state == Boot::PendingUsb);
    {
        Rig r(fl, env);
        r.send(T_HELLO, helloAck(PROTO_VER + 1, "2.0.0"));  // host refuses: no valid ack
        env.fireTimer();                                     // nobody confirmed in time
    }
    assert(fl.runSlot == 0 && fl.bootSlot == 0);
    assert(Bytes(fl.slot[0].begin(), fl.slot[0].begin() + old.size()) == old);
    env.powerCycle();
    Rig r(fl, env);  // old image boots: valid, stale flag cleared, nothing armed
    assert(env.state == Boot::Valid && !env.usbFlag && !env.timerArmed && !r.rx.pendingVerify());
}

static void testLegacyImageValidatedImmediately()
{
    FakeFlash fl(8192);
    FakeEnv env(fl);
    env.state = Boot::PendingOther;  // installed through the Wi-Fi OTA page, no USB flag
    Rig r(fl, env);
    assert(env.state == Boot::Valid && env.markValidCalls == 1 && !env.timerArmed && !r.rx.pendingVerify());
}

static void testWithoutExtOtaIsUnsupported()
{
    VecLink link;
    NullPanel panel;
    Device dev(link, panel, kInfo);
    Bytes f = Rig::mk(T_OTA_BEGIN, 3, Bytes(40, 0));
    dev.onBytes(f.data(), f.size(), 1);
    std::vector<Rep> v;
    Parser p;
    p.feed(link.out.data(), link.out.size(), &Rig::collect, &v);
    assert(v.size() == 2 && v[0].type == T_HELLO && v[1].type == T_NAK && v[1].payload[1] == NAK_UNSUPPORTED);
}

static int selftest()
{
    testSha256();
    testHappyPath();
    testRebootTimingWrap();
    testBadFramesAndStates();
    testOutOfOrderOverflowAndDuplicates();
    testBadHashAndBadImage();
    testFlashFaults();
    testPowerLossMidWrite();
    testPowerLossBetweenFlagAndCommit();
    testResume();
    testBusy();
    testMarkValid();
    testVerifyTimerRollback();
    testLegacyImageValidatedImmediately();
    testWithoutExtOtaIsUnsupported();
    puts("ota_host selftest OK");
    return 0;
}

// ---------------------------------------------------------------------------- replay
static int replay(int argc, char **argv)
{
    if (argc < 4) return 2;
    size_t slot = strtoul(argv[2], nullptr, 10);
    const char *stateOut = argv[3];
    FakeFlash fl(slot);
    for (int i = 4; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--fail-write-at")) fl.failWriteAt = atol(argv[i + 1]);
        if (!strcmp(argv[i], "--corrupt-at")) fl.corruptAt = atol(argv[i + 1]);
    }
    FakeEnv env(fl);
    Bytes in;
    uint8_t buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) in.insert(in.end(), buf, buf + n);
    VecLink link;
    NullPanel panel;
    Device dev(link, panel, kInfo);
    Receiver rx(fl, env);
    rx.begin();
    dev.setExt(&rx);
    uint32_t now = 1000;
    for (size_t i = 0; i < in.size(); i += 61) {
        size_t k = in.size() - i < 61 ? in.size() - i : 61;
        dev.onBytes(in.data() + i, k, now);
        dev.tick(now);
        now += 3;
    }
    dev.tick(now + 2 * REBOOT_DELAY_MS);
    fwrite(link.out.data(), 1, link.out.size(), stdout);
    FILE *f = fopen(stateOut, "w");
    std::string hex = "-";
    if (fl.bootSlot != fl.runSlot) {
        Bytes s = sha(fl.slot[fl.bootSlot]);  // whole slot, caller knows the image size
        hex.clear();
        char b[3];
        for (uint8_t c : s) { snprintf(b, 3, "%02x", c); hex += b; }
    }
    fprintf(f, "committed=%d reboot=%d image_sha256=%s\n", rx.committed() ? 1 : 0, env.rebooted ? 1 : 0, hex.c_str());
    fclose(f);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc >= 2 && !strcmp(argv[1], "--selftest")) return selftest();
    if (argc >= 2 && !strcmp(argv[1], "--replay")) return replay(argc, argv);
    fprintf(stderr, "usage: ota_host --selftest | --replay <slot_bytes> <state_out> [...]\n");
    return 2;
}

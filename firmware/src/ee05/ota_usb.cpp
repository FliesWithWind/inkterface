#include "ota_usb.h"

#include <string.h>

namespace ota_usb {

using namespace usbproto;

// ---------------------------------------------------------------------------
// SHA-256 (FIPS 180-4)
namespace {
const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
inline uint32_t ror(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
inline uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
inline uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
}  // namespace

void Sha256::reset()
{
    static const uint32_t H0[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(h_, H0, sizeof(h_));
    bits_ = 0;
    n_ = 0;
}

void Sha256::block(const uint8_t *p)
{
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16) | ((uint32_t)p[4 * i + 2] << 8) | p[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h_[0], b = h_[1], c = h_[2], d = h_[3], e = h_[4], f = h_[5], g = h_[6], h = h_[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        uint32_t t2 = (ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h_[0] += a; h_[1] += b; h_[2] += c; h_[3] += d; h_[4] += e; h_[5] += f; h_[6] += g; h_[7] += h;
}

void Sha256::update(const uint8_t *p, size_t n)
{
    bits_ += (uint64_t)n * 8;
    while (n) {
        size_t take = 64 - n_;
        if (take > n) take = n;
        memcpy(buf_ + n_, p, take);
        n_ += take; p += take; n -= take;
        if (n_ == 64) { block(buf_); n_ = 0; }
    }
}

void Sha256::final(uint8_t out[SHA_LEN])
{
    uint64_t bits = bits_;
    uint8_t pad = 0x80;
    update(&pad, 1);
    pad = 0;
    while (n_ != 56) update(&pad, 1);
    uint8_t len[8];
    for (int i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56 - 8 * i));
    update(len, 8);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = h_[i] >> 24; out[4 * i + 1] = h_[i] >> 16; out[4 * i + 2] = h_[i] >> 8; out[4 * i + 3] = h_[i];
    }
    reset();
}

// ---------------------------------------------------------------------------
// Receiver. Rules (mirrored by emulator.py, checked byte for byte in decky/tests/test_ota_cpp.py):
//   BEGIN  len, committed->BUSY, size/version->BAD_ARG, size>slot->TOO_BIG; same params = resume
//   DATA   len, committed->BUSY, no session, offset>held->BAD_OFFSET, past end->TOO_BIG,
//          fully held->ACK (resend), partial overlap->BAD_OFFSET, else write
//   END    len, committed->ACK (resend), no session, incomplete->BAD_OFFSET, hash, commit
void Receiver::begin()
{
    Boot s = env_.bootState();
    if (s == Boot::PendingUsb) {
        pending_ = true;
        env_.armVerifyTimer(VERIFY_TIMEOUT_MS);
    } else if (s == Boot::PendingOther) {
        env_.markValid();  // not ours (Wi-Fi OTA): the old behaviour, valid on first boot
    }
}

void Receiver::drop()
{
    if (active_) flash_.abort();
    active_ = false;
    received_ = 0;
}

void Receiver::ackReceived(Device &d, uint8_t seq)
{
    uint8_t p[5] = {seq, (uint8_t)received_, (uint8_t)(received_ >> 8), (uint8_t)(received_ >> 16),
                    (uint8_t)(received_ >> 24)};
    d.send(T_ACK, p, sizeof(p));
}

bool Receiver::onFrame(Device &d, const Frame &f, uint32_t nowMs)
{
    switch (f.type) {
    case T_HELLO:
        if (f.len) onHostAccepts(d, f);
        return false;  // Device still answers with its HELLO
    case T_OTA_BEGIN: lastOtaMs_ = nowMs; onBegin(d, f); break;
    case T_OTA_DATA: lastOtaMs_ = nowMs; onData(d, f); break;
    case T_OTA_END:
        lastOtaMs_ = nowMs;
        onEnd(d, f);
        if (committed_ && !rebootDue_) {
            rebootDue_ = true;
            rebootAtMs_ = nowMs + REBOOT_DELAY_MS;
        }
        break;
    default: return false;
    }
    return true;
}

void Receiver::tick(uint32_t nowMs)
{
    if (rebootDue_ && (int32_t)(nowMs - rebootAtMs_) >= 0) {
        rebootDue_ = false;
        env_.reboot();
    }
}

void Receiver::onHostAccepts(Device &d, const Frame &f)
{
    if (!pending_) return;
    size_t n = strnlen(d.info().fwVer, 64);
    if (f.len < 2 || le16(f.payload) != PROTO_VER || (size_t)(f.len - 2) != n ||
        memcmp(f.payload + 2, d.info().fwVer, n) != 0)
        return;  // host did not accept what we reported: stay pending, the timer rolls back
    pending_ = false;
    env_.disarmVerifyTimer();
    env_.markValid();
}

void Receiver::onBegin(Device &d, const Frame &f)
{
    if (f.len < 4 + SHA_LEN + 1 || f.len > 4 + SHA_LEN + VERSION_MAX) return d.nak(f.seq, NAK_BAD_LEN);
    if (committed_) return d.nak(f.seq, NAK_OTA_BUSY);
    uint32_t size = le32(f.payload);
    const uint8_t *sha = f.payload + 4, *ver = f.payload + 4 + SHA_LEN;
    size_t vlen = f.len - 4 - SHA_LEN;
    bool printable = true;
    for (size_t i = 0; i < vlen; i++) printable = printable && ver[i] >= 0x20 && ver[i] < 0x7F;
    if (!size || !printable) return d.nak(f.seq, NAK_BAD_ARG);
    if (size > flash_.capacity()) return d.nak(f.seq, NAK_OTA_TOO_BIG);
    bool same = active_ && size == size_ && !memcmp(sha, sha_, SHA_LEN) && vlen == versionLen_ &&
                !memcmp(ver, version_, vlen);
    if (!same) {  // new image: restart
        drop();
        if (!flash_.begin()) return d.nak(f.seq, NAK_OTA_FLASH_FAIL);
        active_ = true;
        size_ = size;
        memcpy(sha_, sha, SHA_LEN);
        memcpy(version_, ver, vlen);
        versionLen_ = vlen;
    }
    ackReceived(d, f.seq);
}

void Receiver::onData(Device &d, const Frame &f)
{
    if (f.len < 5) return d.nak(f.seq, NAK_BAD_LEN);
    if (committed_) return d.nak(f.seq, NAK_OTA_BUSY);
    if (!active_) return d.nak(f.seq, NAK_OTA_NO_SESSION);
    uint32_t off = le32(f.payload);
    uint32_t n = f.len - 4;
    if (off > received_) return d.nak(f.seq, NAK_OTA_BAD_OFFSET);
    if ((uint64_t)off + n > size_) return d.nak(f.seq, NAK_OTA_TOO_BIG);
    if (off + n <= received_) return ackReceived(d, f.seq);  // resent chunk we already hold
    if (off < received_) return d.nak(f.seq, NAK_OTA_BAD_OFFSET);
    if (!flash_.write(off, f.payload + 4, n)) {
        drop();  // a failed sequential write leaves the slot in an unknown state
        return d.nak(f.seq, NAK_OTA_FLASH_FAIL);
    }
    received_ += n;
    ackReceived(d, f.seq);
}

void Receiver::onEnd(Device &d, const Frame &f)
{
    if (f.len != 0) return d.nak(f.seq, NAK_BAD_LEN);
    if (committed_) return d.ack(f.seq);  // resent END
    if (!active_) return d.nak(f.seq, NAK_OTA_NO_SESSION);
    if (received_ != size_) return d.nak(f.seq, NAK_OTA_BAD_OFFSET);

    // Read the slot back and hash what is really in flash (catches write faults, not just
    // transport errors), before touching the boot image.
    Sha256 h;
    uint8_t buf[512], got[SHA_LEN];
    for (uint32_t off = 0; off < size_;) {
        size_t n = size_ - off < sizeof(buf) ? size_ - off : sizeof(buf);
        if (!flash_.read(off, buf, n)) {
            drop();
            return d.nak(f.seq, NAK_OTA_FLASH_FAIL);
        }
        h.update(buf, n);
        off += n;
    }
    h.final(got);
    if (memcmp(got, sha_, SHA_LEN) != 0) {
        drop();
        return d.nak(f.seq, NAK_OTA_HASH_MISMATCH);
    }
    env_.setUsbPending(true);  // BEFORE commit: power loss in between is then a harmless stale flag
    if (!flash_.commit()) {
        env_.setUsbPending(false);
        active_ = false;
        received_ = 0;
        return d.nak(f.seq, NAK_OTA_BAD_IMAGE);
    }
    active_ = false;
    committed_ = true;
    d.ack(f.seq);
}

}  // namespace ota_usb

// ===========================================================================
// ESP32 backend. Needs CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE + CONFIG_APP_ROLLBACK_ENABLE, which
// the stock Arduino-ESP32 2.0.17 libs for esp32s3 already set (checked, docs/ota.md).
#if defined(ARDUINO)

#include <Arduino.h>
#include <Preferences.h>
#include <sdkconfig.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <esp_system.h>
#include <esp_timer.h>

// Fail the build, not the field, if a platform upgrade ever drops rollback support from the
// prebuilt Arduino libs / bootloader (docs/ota.md "Rollback").
#if !defined(CONFIG_APP_ROLLBACK_ENABLE) || !defined(CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE)
#error "OTA rollback needs CONFIG_APP_ROLLBACK_ENABLE and CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE"
#endif

// Arduino's initArduino() calls this weak hook before setup(). Default `false` makes it mark
// every new image valid immediately, which defeats rollback. `true` leaves the decision to
// Receiver::begin() (USB OTA images wait for the host; anything else is marked valid there).
extern "C" bool verifyRollbackLater() { return true; }

namespace ota_usb {
namespace {

class EspFlash : public Flash {
  public:
    size_t capacity() override
    {
        const esp_partition_t *p = esp_ota_get_next_update_partition(nullptr);
        return p ? p->size : 0;
    }
    bool begin() override
    {
        abort();
        part_ = esp_ota_get_next_update_partition(nullptr);
        // SEQUENTIAL_WRITES: erase sector by sector as data arrives, so OTA_BEGIN is instant
        // and the USB link is never starved during a multi-second whole-slot erase.
        open_ = part_ && esp_ota_begin(part_, OTA_WITH_SEQUENTIAL_WRITES, &h_) == ESP_OK;
        return open_;
    }
    bool write(size_t, const uint8_t *p, size_t n) override { return open_ && esp_ota_write(h_, p, n) == ESP_OK; }
    bool read(size_t off, uint8_t *p, size_t n) override
    {
        return part_ && esp_partition_read(part_, off, p, n) == ESP_OK;
    }
    bool commit() override
    {
        if (!open_) return false;
        open_ = false;
        // esp_ota_end validates the image (magic, segments, checksum); only then flip otadata
        return esp_ota_end(h_) == ESP_OK && esp_ota_set_boot_partition(part_) == ESP_OK;
    }
    void abort() override
    {
        if (open_) esp_ota_abort(h_);
        open_ = false;
    }

  private:
    const esp_partition_t *part_ = nullptr;
    esp_ota_handle_t h_ = 0;
    bool open_ = false;
};

class EspEnv : public Env {
  public:
    Boot bootState() override
    {
        prefs_.begin("inkota", false);
        esp_ota_img_states_t st;
        const esp_partition_t *run = esp_ota_get_running_partition();
        bool pend = run && esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY;
        bool flag = prefs_.getBool("usbpend", false);
        if (pend) return flag ? Boot::PendingUsb : Boot::PendingOther;
        if (flag) prefs_.putBool("usbpend", false);  // stale: update never booted (or already validated)
        return Boot::Valid;
    }
    void markValid() override
    {
        esp_ota_mark_app_valid_cancel_rollback();
        setUsbPending(false);
    }
    void setUsbPending(bool v) override { prefs_.putBool("usbpend", v); }
    void armVerifyTimer(uint32_t ms) override
    {
        if (!timer_) {
            esp_timer_create_args_t a = {};
            a.callback = [](void *) { esp_ota_mark_app_invalid_rollback_and_reboot(); };
            a.name = "ota_verify";
            if (esp_timer_create(&a, &timer_) != ESP_OK) return;
        }
        esp_timer_start_once(timer_, (uint64_t)ms * 1000ULL);
    }
    void disarmVerifyTimer() override
    {
        if (timer_) esp_timer_stop(timer_);
    }
    void reboot() override { esp_restart(); }

  private:
    Preferences prefs_;
    esp_timer_handle_t timer_ = nullptr;
};

}  // namespace

void attach(Device &dev)
{
    static EspFlash flash;
    static EspEnv env;
    static Receiver rx(flash, env);
    rx.begin();
    dev.setExt(&rx);
}

}  // namespace ota_usb
#endif  // ARDUINO

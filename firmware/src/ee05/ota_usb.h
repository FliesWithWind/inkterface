// USB OTA receiver (OTA_BEGIN / OTA_DATA / OTA_END) with rollback. See docs/ota.md and
// docs/usb-protocol-v1.md "OTA".
//
// The state machine (Receiver) is pure C++ over two small interfaces, Flash (the inactive OTA
// slot) and Env (boot state, reboot, verify timer), so firmware/host_test/ota_host.cpp runs
// it against a fake flash. The ESP32 backend is at the bottom of ota_usb.cpp (#ifdef ARDUINO).
//
// Hook in main.cpp: `#include "ota_usb.h"` and `ota_usb::attach(USB);` first thing in setup().
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "usbproto.h"

namespace ota_usb {

constexpr size_t SHA_LEN = 32;
constexpr size_t VERSION_MAX = 32;
// A new image must be confirmed by the host (HELLO ack) within this long after boot or the
// bootloader rolls back. Covers setup() (Wi-Fi connect can take ~25 s) plus host reconnect.
constexpr uint32_t VERIFY_TIMEOUT_MS = 180000;
constexpr uint32_t REBOOT_DELAY_MS = 400;  // let the END ACK leave the USB FIFO first

// Portable SHA-256 (no mbedtls dependency, so the host test runs the same code).
class Sha256 {
  public:
    Sha256() { reset(); }
    void reset();
    void update(const uint8_t *p, size_t n);
    void final(uint8_t out[SHA_LEN]);

  private:
    void block(const uint8_t *p);
    uint32_t h_[8];
    uint64_t bits_;
    uint8_t buf_[64];
    size_t n_;
};

// The inactive OTA slot. Writes are strictly sequential from offset 0.
struct Flash {
    virtual size_t capacity() = 0;
    virtual bool begin() = 0;  // start a new image (may erase lazily); drops any open one
    virtual bool write(size_t off, const uint8_t *p, size_t n) = 0;
    virtual bool read(size_t off, uint8_t *p, size_t n) = 0;  // read back what was written
    virtual bool commit() = 0;  // validate the image and make it the next boot image
    virtual void abort() = 0;   // drop the open image; the boot image is untouched
};

enum class Boot {
    Valid,        // running a confirmed image
    PendingUsb,   // first boot of an image this receiver installed: needs the host's ack
    PendingOther  // first boot of an image installed some other way (legacy Wi-Fi OTA)
};

struct Env {
    virtual Boot bootState() = 0;
    virtual void markValid() = 0;          // cancel rollback; clears the USB-pending flag
    virtual void setUsbPending(bool) = 0;  // persistent: "the next boot is a USB OTA boot"
    virtual void armVerifyTimer(uint32_t ms) = 0;  // on expiry: roll back + reboot
    virtual void disarmVerifyTimer() = 0;
    virtual void reboot() = 0;
};

class Receiver : public usbproto::Ext {
  public:
    Receiver(Flash &flash, Env &env) : flash_(flash), env_(env) {}

    void begin();  // once per boot, early. Arms the verify timer after a USB OTA boot.
    bool onFrame(usbproto::Device &dev, const usbproto::Frame &f, uint32_t nowMs) override;
    void tick(uint32_t nowMs) override;
    uint32_t caps() const override { return usbproto::CAP_OTA; }
    // An image is in transit (BEGIN..END, last OTA frame < BUSY_QUIET_MS ago) or committed and about to reboot. A session the host
    // abandoned (it stays resumable forever) stops counting after BUSY_QUIET_MS, so it cannot disable the bus-suspend fallback.
    bool busy(uint32_t nowMs) const override
    {
        return committed_ || rebootDue_ || (active_ && (uint32_t)(nowMs - lastOtaMs_) < BUSY_QUIET_MS);
    }
    static constexpr uint32_t BUSY_QUIET_MS = 10000;

    bool pendingVerify() const { return pending_; }
    bool committed() const { return committed_; }
    uint32_t received() const { return received_; }

  private:
    void onBegin(usbproto::Device &d, const usbproto::Frame &f);
    void onData(usbproto::Device &d, const usbproto::Frame &f);
    void onEnd(usbproto::Device &d, const usbproto::Frame &f);
    void onHostAccepts(usbproto::Device &d, const usbproto::Frame &f);
    void ackReceived(usbproto::Device &d, uint8_t seq);
    void drop();

    Flash &flash_;
    Env &env_;
    bool active_ = false, committed_ = false, pending_ = false, rebootDue_ = false;
    uint32_t size_ = 0, received_ = 0, rebootAtMs_ = 0, lastOtaMs_ = 0;
    uint8_t sha_[SHA_LEN] = {};
    uint8_t version_[VERSION_MAX] = {};
    size_t versionLen_ = 0;
};

#if defined(ARDUINO)
// ESP32 backend: esp_ota_* on the next update partition, NVS flag, esp_timer verify timer.
void attach(usbproto::Device &dev);
#endif

}  // namespace ota_usb

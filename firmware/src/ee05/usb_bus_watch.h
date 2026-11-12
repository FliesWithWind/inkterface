// USB bus-suspend detector: "the host stopped talking USB" within seconds, instead of the idle timeout (default 60 s).
// Pure C++11, no clock of its own (callers pass millis()), unit tested on the host (firmware/host_test/usb_bus_watch_host.cpp).
//
// Input is `plugged`: HWCDC::isPlugged(), which the Arduino core derives from the SOF packets of the USB-Serial-JTAG
// peripheral (a FreeRTOS tick hook looks for one every tick). A host that suspends, powers down its ports or
// disappears stops sending SOF, while VBUS can stay up: that is exactly the "machine went to sleep" case.
//
// It must never fire on a healthy bus. Three guards:
//   * the outage must last DOWN_MS of *consecutive* samples, so a SOF flap or a bus reset (re-enumeration) is ignored;
//   * a gap longer than MAX_GAP_MS between two samples means the loop was blocked (e-paper refresh, Wi-Fi connect):
//     what happened meanwhile is unknown, so the outage clock restarts instead of counting the blocked time;
//   * `hold` (an OTA session is open) suppresses it entirely, and the outage clock restarts when the hold ends.
#pragma once

#include <stdint.h>

#ifndef INK_BUS_DOWN_MS
#define INK_BUS_DOWN_MS 2000   // bench builds may override with -D INK_BUS_DOWN_MS=...
#endif
#ifndef INK_BUS_REPORT_MS
#define INK_BUS_REPORT_MS 50   // ... and -D INK_BUS_REPORT_MS=1 to log every SOF gap
#endif

namespace busdown {

constexpr uint32_t DOWN_MS = INK_BUS_DOWN_MS;
constexpr uint32_t MAX_GAP_MS = 250;
constexpr uint32_t REPORT_MS = INK_BUS_REPORT_MS;   // outages shorter than this are not worth a log line

class Watch {
  public:
    // One sample per loop pass. true exactly once per outage, when it has lasted DOWN_MS.
    bool update(bool plugged, bool hold, uint32_t nowMs)
    {
        bool gap = haveLast_ && (uint32_t)(nowMs - last_) > MAX_GAP_MS;
        last_ = nowMs;
        haveLast_ = true;
        if (plugged) {
            if (downValid_ && (uint32_t)(nowMs - down_) >= REPORT_MS) outageMs_ = nowMs - down_;
            downValid_ = fired_ = false;
            return false;
        }
        if (hold) {
            downValid_ = false;
            return false;
        }
        if (!downValid_ || gap) {
            downValid_ = true;
            down_ = nowMs;
            return false;
        }
        if (!fired_ && (uint32_t)(nowMs - down_) >= DOWN_MS) {
            fired_ = true;
            return true;
        }
        return false;
    }

    // Idle owns the glass: nothing to detect. Forget everything.
    void reset() { downValid_ = fired_ = haveLast_ = false; }

    // Length of the last outage that ended without firing (0 = none since the last call). For the debug log only.
    uint32_t takeOutageMs() { uint32_t r = outageMs_; outageMs_ = 0; return r; }

  private:
    bool downValid_ = false, fired_ = false, haveLast_ = false;
    uint32_t last_ = 0, down_ = 0, outageMs_ = 0;
};

}  // namespace busdown

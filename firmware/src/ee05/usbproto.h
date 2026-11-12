// Inkterface USB-CDC host protocol v1 (panel side). See docs/usb-protocol-v1.md.
//
// Pure C++11, no Arduino or display-driver dependency: the firmware supplies a Link (byte
// sink) and a Panel (pixel pusher), so the same code is unit-tested on the host
// (firmware/host_test/usbproto_host.cpp).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "idle_mode.h"

namespace usbproto {

constexpr uint8_t SYNC0 = 0xA5;
constexpr uint8_t SYNC1 = 0x5A;
constexpr uint16_t PROTO_VER = 1;
constexpr size_t MAX_PAYLOAD = 4096;
constexpr size_t HDR_LEN = 6;  // sync(2) type(1) seq(1) len(2)
constexpr size_t CRC_LEN = 4;
constexpr size_t MAX_FRAME = HDR_LEN + MAX_PAYLOAD + CRC_LEN;

// EE05 panel geometry (648x480, datasheet p.4). 1bpp, MSB first, 1 = black.
constexpr uint16_t PANEL_W = 648;
constexpr uint16_t PANEL_H = 480;
constexpr size_t PANEL_STRIDE = PANEL_W / 8;
constexpr size_t FB_BYTES = PANEL_STRIDE * PANEL_H;

enum Type : uint8_t {
    T_HELLO = 0x01,      // panel->host; host->panel with empty payload = "send HELLO"
    T_HEARTBEAT = 0x02,  // host->panel
    T_ACK = 0x03,        // panel->host, payload: acked seq
    T_NAK = 0x04,        // panel->host, payload: nacked seq, reason
    T_RECT = 0x10,
    T_REFRESH = 0x11,
    T_MODE = 0x12,
    T_IDLE_LAYOUT = 0x20,  // chunked layout store upload (docs/idle-mode.md); NAK UNSUPPORTED without hooks
    T_OTA_BEGIN = 0x30,    // handled by an Ext (ota_usb.h); NAK UNSUPPORTED without one
    T_OTA_DATA = 0x31,
    T_OTA_END = 0x32,
    T_LOG = 0x7F,          // panel->host, UTF-8 text
};


enum Nak : uint8_t {
    NAK_BAD_TYPE = 1,
    NAK_BAD_LEN = 2,
    NAK_BAD_RECT = 3,
    NAK_BAD_ARG = 4,
    NAK_WRONG_MODE = 5,
    NAK_UNSUPPORTED = 6,
    NAK_OTA_BAD_OFFSET = 7,     // DATA offset != bytes held / END before all bytes arrived
    NAK_OTA_FLASH_FAIL = 8,
    NAK_OTA_HASH_MISMATCH = 9,  // sha256 of what was written != OTA_BEGIN sha256
    NAK_OTA_TOO_BIG = 10,
    NAK_OTA_BUSY = 11,          // image already committed, panel is about to reboot
    NAK_OTA_NO_SESSION = 12,    // DATA/END without OTA_BEGIN
    NAK_OTA_BAD_IMAGE = 13,     // not a bootable app image
    // 7..13 are OTA's (above); idle mode takes 14 and 15
    NAK_BAD_DATA = 14,  // payload parsed but failed validation (idle store)
    NAK_STORAGE = 15,   // flash write failed (idle store)
};

enum Mode : uint8_t {
    MODE_HOST = 0,   // host renders; panel's idle drawing is suspended
    MODE_PANEL = 1,  // panel renders its idle layout now and stays there until MODE HOST
};

constexpr uint32_t CAP_GRAY4 = 1u << 0;
constexpr uint32_t CAP_IDLE_STORAGE = 1u << 1;
constexpr uint32_t CAP_OTA = 1u << 2;  // OTA_* frames supported (reported by the Ext)

constexpr uint8_t LAYOUT_KEEP = 0xFF;  // MODE: leave the selected idle layout alone
constexpr uint32_t PARTIAL_FRAME_TIMEOUT_MS = 100;  // stalled half-frame is discarded

// IEEE CRC-32 (same as zlib.crc32). Incremental: pass the previous result as `crc`.
uint32_t crc32(const uint8_t *data, size_t len, uint32_t crc = 0);

// Writes a complete frame to `out`. Returns bytes written, or 0 if len > MAX_PAYLOAD
// or cap is too small.
size_t encodeFrame(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len, uint8_t *out,
                   size_t cap);

struct Frame {
    uint8_t type;
    uint8_t seq;
    uint16_t len;
    const uint8_t *payload;  // valid only during the callback
};

// Streaming frame parser. Feed arbitrary byte chunks; complete, CRC-valid frames
// are delivered to the handler. On bad length / bad CRC it rewinds to one byte
// after the false sync and rescans, so a real frame hiding inside a corrupt one
// is still found.
class Parser {
  public:
    typedef void (*Handler)(void *ctx, const Frame &f);
    void feed(const uint8_t *data, size_t len, Handler h, void *ctx);
    // Give up on whatever is buffered (stalled half-frame): rewind byte by byte so
    // complete frames hiding behind a false header are still delivered.
    void flushStale(Handler h, void *ctx);
    bool pending() const { return n_ > 0; }  // holds a (possibly partial) frame start
    uint32_t crcErrors() const { return crcErrors_; }
    uint32_t badLen() const { return badLen_; }
    uint32_t skippedBytes() const { return skipped_; }

  private:
    void process(Handler h, void *ctx);
    void discard(size_t k);
    uint8_t buf_[MAX_FRAME];
    size_t n_ = 0;
    uint32_t crcErrors_ = 0, badLen_ = 0, skipped_ = 0;
};

struct Link {  // byte sink to the host (USB CDC)
    virtual size_t write(const uint8_t *p, size_t n) = 0;
    // Bytes that can be written without blocking or dropping. LOG text is only sent when a whole
    // frame fits, so a debug burst can never crowd out an ACK the host is waiting for.
    virtual size_t writable() const { return (size_t)-1; }
};

struct Panel {  // pixel pusher; fb is the full PANEL_W x PANEL_H shadow buffer
    // Show fb region (x,y,w,h) (x,w multiples of 8) with a partial refresh, or the
    // whole fb with a full refresh. Blocks until the panel is done.
    virtual void present(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                         bool full) = 0;
};

// Idle-mode plumbing supplied by the firmware (null = the spike behaviour: IDLE_LAYOUT is UNSUPPORTED).
struct IdleHooks {
    virtual ~IdleHooks() {}
    // One IDLE_LAYOUT chunk. 0 = ACK, else the NAK reason.
    virtual uint8_t layoutChunk(uint32_t total, uint32_t offset, const uint8_t *data, size_t n) = 0;
    // MODE asked for layout `idx`. false = no such layout (NAK BAD_ARG).
    virtual bool selectLayout(uint8_t idx) = 0;
    // MODE changed the silence timeout (seconds, already range checked): persist it.
    virtual void timeoutChanged(uint16_t seconds) = 0;
};

struct Info {
    const char *fwVer;
    uint32_t caps;
    uint32_t idleFlashFree;
    uint16_t rxWindow;  // bytes the host may have unacknowledged (< RX queue size)
};

// Thread-safe, non-blocking debug text sink (any task). Drained into LOG frames by
// Device::tick() on the loop task while a host session is active; otherwise it queues in a
// 2 KiB ring (new text is dropped when full) until the next session starts.
void logWrite(const uint8_t *p, size_t n);

class Device;

// Optional extension: sees every valid host frame after the session bookkeeping and before the
// built-in handlers. Returns true when it consumed (and answered) the frame. Used by ota_usb.
struct Ext {
    virtual bool onFrame(Device &dev, const Frame &f, uint32_t nowMs) = 0;
    virtual void tick(uint32_t nowMs) { (void)nowMs; }
    virtual uint32_t caps() const { return 0; }  // OR-ed into the HELLO caps
    virtual bool busy(uint32_t nowMs) const { (void)nowMs; return false; }  // a transfer the bus-suspend fallback must not interrupt (OTA)
};

class Device {
  public:
    Device(Link &link, Panel &panel, const Info &info, IdleHooks *hooks = nullptr)
        : link_(link), panel_(panel), info_(info), hooks_(hooks) {}

    // Boot reference for the idle timeout (the silence clock starts here).
    void start(uint32_t nowMs) { ctl_.start(nowMs); lastFrameMs_ = nowMs; }
    // Restore the persisted silence timeout (seconds); false = out of range, default kept.
    bool setIdleTimeoutS(uint16_t s) { return ctl_.setTimeoutS(s); }
    idlemode::Controller &idle() { return ctl_; }

    void onBytes(const uint8_t *data, size_t len, uint32_t nowMs);
    void tick(uint32_t nowMs);  // host timeout, stalled-frame timeout, LOG drain
    void sendHello();
    // USB attach seen by the firmware (HWCDC `Serial` went false -> true). Announces the panel only when there is no
    // live host session: HWCDC flaps that bool on a missed SOF/slow IN poll (measured: ~every 100 s on a hub-attached
    // dev machine), and a HELLO the host did not ask for makes it assume the panel reset -> a 4 s full redraw.
    void onAttach() { if (!alive_) sendHello(); }
    void setExt(Ext *e) { ext_ = e; }
    bool extBusy(uint32_t nowMs) const { return ext_ && ext_->busy(nowMs); }
    // The USB bus went quiet (usb_bus_watch.h): show the idle screens now and end the host session, so the first frame
    // after the host is back makes the panel announce itself (HELLO) and the host redraws everything.
    void onBusSuspend() { alive_ = false; ctl_.onBusSuspend(); }
    const Info &info() const { return info_; }
    void send(uint8_t type, const uint8_t *payload, uint16_t len);  // frame to the host
    void ack(uint8_t seq);
    void nak(uint8_t seq, uint8_t reason);

    bool hostOwnsDisplay() const { return ctl_.hostOwns(); }
    bool alive() const { return alive_; }
    uint8_t mode() const { return ctl_.hostOwns() ? MODE_HOST : MODE_PANEL; }
    const Parser &parser() const { return parser_; }
    const uint8_t *framebuffer() const { return fb_; }

  private:
    static void thunk(void *ctx, const Frame &f);
    void onFrame(const Frame &f);
    void onRect(const Frame &f);
    void onRefresh(const Frame &f);
    void onMode(const Frame &f);
    void onIdleLayout(const Frame &f);

    Link &link_;
    Panel &panel_;
    Info info_;
    Ext *ext_ = nullptr;
    IdleHooks *hooks_;
    idlemode::Controller ctl_;
    Parser parser_;
    uint8_t fb_[FB_BYTES] = {};
    uint16_t dx0_ = 0, dy0_ = 0, dx1_ = 0, dy1_ = 0;  // dirty bbox, exclusive end; empty if dx0>=dx1
    uint8_t txSeq_ = 0;
    bool alive_ = false;
    uint32_t nowMs_ = 0, lastFrameMs_ = 0, lastByteMs_ = 0, lastCrcLogMs_ = 0, lastCrcErrs_ = 0;
    uint8_t tx_[300];
};

}  // namespace usbproto

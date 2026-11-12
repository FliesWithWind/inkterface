#include "usbproto.h"

#include <mutex>
#include <stdio.h>
#include <string.h>

namespace usbproto {

// ---------------------------------------------------------------------------
// CRC-32 (IEEE 802.3, reflected, zlib-compatible), nibble table = 64 bytes.
uint32_t crc32(const uint8_t *data, size_t len, uint32_t crc)
{
    static const uint32_t T[16] = {0x00000000, 0x1DB71064, 0x3B6E20C8, 0x26D930AC,
                                   0x76DC4190, 0x6B6B51F4, 0x4DB26158, 0x5005713C,
                                   0xEDB88320, 0xF00F9344, 0xD6D6A3E8, 0xCB61B38C,
                                   0x9B64C2B0, 0x86D3D2D4, 0xA00AE278, 0xBDBDF21C};
    crc = ~crc;
    while (len--) {
        crc ^= *data++;
        crc = (crc >> 4) ^ T[crc & 15];
        crc = (crc >> 4) ^ T[crc & 15];
    }
    return ~crc;
}

static inline uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline void put16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
static inline void put32(uint8_t *p, uint32_t v)
{
    p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}

size_t encodeFrame(uint8_t type, uint8_t seq, const uint8_t *payload, uint16_t len, uint8_t *out,
                   size_t cap)
{
    if (len > MAX_PAYLOAD || cap < HDR_LEN + len + CRC_LEN) return 0;
    out[0] = SYNC0;
    out[1] = SYNC1;
    out[2] = type;
    out[3] = seq;
    put16(out + 4, len);
    if (len) memcpy(out + HDR_LEN, payload, len);
    put32(out + HDR_LEN + len, crc32(out + 2, 4 + len));  // type..payload
    return HDR_LEN + len + CRC_LEN;
}

// ---------------------------------------------------------------------------
// Parser. buf_ always starts at a candidate sync (or is empty / a lone 0xA5).
void Parser::discard(size_t k)
{
    n_ -= k;
    if (n_) memmove(buf_, buf_ + k, n_);
}

void Parser::feed(const uint8_t *data, size_t len, Handler h, void *ctx)
{
    while (len) {
        size_t take = sizeof(buf_) - n_;
        if (take > len) take = len;
        memcpy(buf_ + n_, data, take);
        n_ += take;
        data += take;
        len -= take;
        process(h, ctx);
    }
}

void Parser::flushStale(Handler h, void *ctx)
{
    while (n_) {
        discard(1);
        process(h, ctx);
    }
}

void Parser::process(Handler h, void *ctx)
{
    for (;;) {
        size_t i = 0;
        while (i + 1 < n_ && !(buf_[i] == SYNC0 && buf_[i + 1] == SYNC1)) i++;
        if (i + 1 >= n_) {  // no complete sync pair; keep a trailing SYNC0 only
            i = (n_ && buf_[n_ - 1] == SYNC0) ? n_ - 1 : n_;
            skipped_ += i;
            discard(i);
            return;
        }
        skipped_ += i;
        discard(i);
        if (n_ < HDR_LEN) return;
        uint16_t len = le16(buf_ + 4);
        if (len > MAX_PAYLOAD) {
            badLen_++;
            discard(1);
            continue;
        }
        size_t total = HDR_LEN + len + CRC_LEN;
        if (n_ < total) return;
        if (crc32(buf_ + 2, 4 + len) != le32(buf_ + HDR_LEN + len)) {
            crcErrors_++;
            discard(1);  // rewind: a real frame may start inside this one
            continue;
        }
        Frame f = {buf_[2], buf_[3], len, buf_ + HDR_LEN};
        h(ctx, f);
        discard(total);
    }
}

// ---------------------------------------------------------------------------
// Debug log ring (any task writes, loop task drains).
namespace {
std::mutex g_logMu;
uint8_t g_ring[2048];  // keeps the OLDEST bytes when full; a waking host reads what happened while idle
size_t g_head = 0, g_count = 0;

size_t logDrain(uint8_t *out, size_t max)
{
    std::lock_guard<std::mutex> g(g_logMu);
    size_t n = g_count < max ? g_count : max;
    if (n == max) {  // window full: cut after the last newline so a LOG frame holds whole lines
        for (size_t k = n; k > 0; k--)
            if (g_ring[(g_head + k - 1) % sizeof(g_ring)] == '\n') { n = k; break; }
    }
    for (size_t i = 0; i < n; i++) out[i] = g_ring[(g_head + i) % sizeof(g_ring)];
    g_head = (g_head + n) % sizeof(g_ring);
    g_count -= n;
    return n;
}
}  // namespace

void logWrite(const uint8_t *p, size_t n)
{
    std::lock_guard<std::mutex> g(g_logMu);
    for (size_t i = 0; i < n && g_count < sizeof(g_ring); i++)  // drop on overflow
        g_ring[(g_head + g_count++) % sizeof(g_ring)] = p[i];
}

// ---------------------------------------------------------------------------
void Device::send(uint8_t type, const uint8_t *payload, uint16_t len)
{
    size_t n = encodeFrame(type, txSeq_++, payload, len, tx_, sizeof(tx_));
    if (n) link_.write(tx_, n);
}

void Device::ack(uint8_t seq) { send(T_ACK, &seq, 1); }

void Device::nak(uint8_t seq, uint8_t reason)
{
    uint8_t p[2] = {seq, reason};
    send(T_NAK, p, 2);
}

void Device::sendHello()
{
    uint8_t p[18 + 64];
    put16(p + 0, PROTO_VER);
    put16(p + 2, MAX_PAYLOAD);
    put16(p + 4, info_.rxWindow);
    put16(p + 6, PANEL_W);
    put16(p + 8, PANEL_H);
    put32(p + 10, info_.caps | (ext_ ? ext_->caps() : 0));
    put32(p + 14, info_.idleFlashFree);
    size_t n = strnlen(info_.fwVer, 64);
    memcpy(p + 18, info_.fwVer, n);
    send(T_HELLO, p, (uint16_t)(18 + n));
}

void Device::thunk(void *ctx, const Frame &f) { static_cast<Device *>(ctx)->onFrame(f); }

void Device::onBytes(const uint8_t *data, size_t len, uint32_t nowMs)
{
    nowMs_ = nowMs;
    lastByteMs_ = nowMs;
    parser_.feed(data, len, &Device::thunk, this);
}

void Device::onFrame(const Frame &f)
{
    lastFrameMs_ = nowMs_;
    ctl_.onFrame(nowMs_);
    if (!alive_) {  // first frame of a session: make sure the host learns our geometry
        alive_ = true;
        if (f.type != T_HELLO) sendHello();
    }
    if (ext_ && ext_->onFrame(*this, f, nowMs_)) return;
    switch (f.type) {
    case T_HELLO: sendHello(); break;  // a HELLO with payload is the host's "accepted" ack: the Ext acts on it, we still answer
    case T_HEARTBEAT: ack(f.seq); break;
    case T_RECT: onRect(f); break;
    case T_REFRESH: onRefresh(f); break;
    case T_MODE: onMode(f); break;
    case T_IDLE_LAYOUT: onIdleLayout(f); break;
    case T_OTA_BEGIN:
    case T_OTA_DATA:
    case T_OTA_END: nak(f.seq, NAK_UNSUPPORTED); break;
    case T_ACK:
    case T_NAK:
    case T_LOG: break;  // panel->host types; ignore if echoed
    default: nak(f.seq, NAK_BAD_TYPE);
    }
}

// MODE: mode u8 [| idle_timeout_s u16 [| layout u8]]. All arguments are checked before any is applied.
void Device::onMode(const Frame &f)
{
    if (f.len != 1 && f.len != 3 && f.len != 4) return nak(f.seq, NAK_BAD_LEN);
    if (f.payload[0] > MODE_PANEL) return nak(f.seq, NAK_BAD_ARG);
    uint16_t to = f.len >= 3 ? le16(f.payload + 1) : 0;
    if (to && (to < idlemode::MIN_TIMEOUT_S || to > idlemode::MAX_TIMEOUT_S)) return nak(f.seq, NAK_BAD_ARG);
    uint8_t layout = f.len == 4 ? f.payload[3] : LAYOUT_KEEP;
    if (layout != LAYOUT_KEEP && !(hooks_ && hooks_->selectLayout(layout))) return nak(f.seq, NAK_BAD_ARG);
    if (to) {
        ctl_.setTimeoutS(to);
        if (hooks_) hooks_->timeoutChanged(to);
    }
    ctl_.onMode(f.payload[0] == MODE_PANEL, nowMs_);
    ack(f.seq);
}

void Device::onIdleLayout(const Frame &f)
{
    if (!hooks_) return nak(f.seq, NAK_UNSUPPORTED);
    if (f.len < 9) return nak(f.seq, NAK_BAD_LEN);  // total u32 + offset u32 + at least one byte
    uint8_t r = hooks_->layoutChunk(le32(f.payload), le32(f.payload + 4), f.payload + 8, f.len - 8u);
    if (r) nak(f.seq, r);
    else ack(f.seq);
}

void Device::onRect(const Frame &f)
{
    if (!ctl_.hostOwns()) return nak(f.seq, NAK_WRONG_MODE);
    if (f.len < 8) return nak(f.seq, NAK_BAD_LEN);
    uint32_t x = le16(f.payload), y = le16(f.payload + 2), w = le16(f.payload + 4),
             h = le16(f.payload + 6);
    if (!w || !h || (x & 7) || (w & 7) || x + w > PANEL_W || y + h > PANEL_H)
        return nak(f.seq, NAK_BAD_RECT);
    size_t stride = w / 8;
    if (f.len != 8 + h * stride) return nak(f.seq, NAK_BAD_LEN);
    const uint8_t *src = f.payload + 8;
    for (uint32_t r = 0; r < h; r++)
        memcpy(fb_ + (y + r) * PANEL_STRIDE + x / 8, src + r * stride, stride);
    if (dx0_ >= dx1_) {
        dx0_ = x; dy0_ = y; dx1_ = x + w; dy1_ = y + h;
    } else {
        if (x < dx0_) dx0_ = x;
        if (y < dy0_) dy0_ = y;
        if (x + w > dx1_) dx1_ = x + w;
        if (y + h > dy1_) dy1_ = y + h;
    }
    ack(f.seq);
}

void Device::onRefresh(const Frame &f)
{
    if (!ctl_.hostOwns()) return nak(f.seq, NAK_WRONG_MODE);
    if (f.len != 1) return nak(f.seq, NAK_BAD_LEN);
    if (f.payload[0] > 1) return nak(f.seq, NAK_BAD_ARG);
    if (f.payload[0] == 1) {
        panel_.present(fb_, 0, 0, PANEL_W, PANEL_H, true);
    } else if (dx0_ < dx1_) {
        panel_.present(fb_, dx0_, dy0_, dx1_ - dx0_, dy1_ - dy0_, false);
    }  // else: nothing dirty, ACK without touching the panel
    dx0_ = dy0_ = dx1_ = dy1_ = 0;
    ack(f.seq);
}

void Device::tick(uint32_t nowMs)
{
    nowMs_ = nowMs;
    if (ext_) ext_->tick(nowMs);
    if (parser_.pending() && (uint32_t)(nowMs - lastByteMs_) > PARTIAL_FRAME_TIMEOUT_MS)
        parser_.flushStale(&Device::thunk, this);  // dead half-frame must not swallow the next frames
    ctl_.tick(nowMs);
    // a session ends with the host's silence timeout (>= 15 s, default 60 s), never earlier
    if (alive_ && (uint32_t)(nowMs - lastFrameMs_) > (uint32_t)ctl_.timeoutS() * 1000u) alive_ = false;
    uint8_t buf[200];
    // no host session: keep the text queued (bounded ring) so the next session sees it
    for (int i = 0; alive_ && i < 2 && link_.writable() >= sizeof(tx_); i++) {
        size_t n = logDrain(buf, sizeof(buf));
        if (!n) break;
        send(T_LOG, buf, (uint16_t)n);
    }
    uint32_t errs = parser_.crcErrors() + parser_.badLen();
    if (alive_ && errs != lastCrcErrs_ && (uint32_t)(nowMs - lastCrcLogMs_) > 1000) {
        int n = snprintf((char *)buf, sizeof(buf), "rx errors: crc=%u badlen=%u skipped=%u",
                         (unsigned)parser_.crcErrors(), (unsigned)parser_.badLen(),
                         (unsigned)parser_.skippedBytes());
        send(T_LOG, buf, (uint16_t)n);
        lastCrcErrs_ = errs;
        lastCrcLogMs_ = nowMs;
    }
}

}  // namespace usbproto

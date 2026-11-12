#include "idle_store.h"

#include <string.h>

#include "usbproto.h"

namespace idle {

const char *storeErrorName(StoreError e)
{
    static const char *n[] = {"OK",         "TOO_SHORT", "TOO_BIG",    "BAD_MAGIC",  "BAD_VERSION",
                              "BAD_CRC",    "BAD_HEADER", "BAD_CONFIG", "BAD_LAYOUT"};
    return e <= ST_BAD_LAYOUT ? n[e] : "?";
}

namespace {
inline uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
inline uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
inline void wr32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

struct TagInfo { uint8_t maxLen; bool text; };
// index = tag
const TagInfo kTags[CT_LAST + 1] = {{0, false}, {32, true}, {63, true}, {63, true}, {15, true},
                                    {15, true}, {1, false}, {40, true}, {20, true}, {4, false}};

bool textOk(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (p[i] < 0x20 || p[i] == 0x7F) return false;
    return true;
}

bool parseConfig(const uint8_t *p, size_t n, StoreConfig &c)
{
    size_t i = 0;
    while (i < n) {
        if (i + 2 > n) return false;
        uint8_t tag = p[i], len = p[i + 1];
        i += 2;
        if (tag < 1 || tag > CT_LAST || len > kTags[tag].maxLen || i + len > n) return false;
        if (c.have & (1u << (tag - 1))) return false;  // duplicate
        const uint8_t *v = p + i;
        i += len;
        if (kTags[tag].text && !textOk(v, len)) return false;
        c.have |= 1u << (tag - 1);
        switch (tag) {
        case CT_WIFI_SSID: memcpy(c.wifiSsid, v, len); c.wifiSsid[len] = 0; break;
        case CT_WIFI_PASS: memcpy(c.wifiPass, v, len); c.wifiPass[len] = 0; break;
        case CT_TZ: memcpy(c.tz, v, len); c.tz[len] = 0; break;
        case CT_LAT: memcpy(c.lat, v, len); c.lat[len] = 0; break;
        case CT_LON: memcpy(c.lon, v, len); c.lon[len] = 0; break;
        case CT_STEAM_KEY: memcpy(c.steamKey, v, len); c.steamKey[len] = 0; break;
        case CT_STEAM_ID: memcpy(c.steamId, v, len); c.steamId[len] = 0; break;
        case CT_UNITS:
            if (len != 1 || v[0] > 1) return false;
            c.units = v[0];
            break;
        case CT_STEAM_APPID:
            if (len != 4) return false;
            c.steamAppId = rd32(v);
            break;
        }
    }
    return true;
}
}  // namespace

StoreError parseStore(const uint8_t *buf, size_t len, StoreView &out)
{
    if (len < 12 + 4) return ST_TOO_SHORT;
    if (len > MAX_STORE_BYTES) return ST_TOO_BIG;
    if (memcmp(buf, STORE_MAGIC, 4)) return ST_BAD_MAGIC;
    if (buf[4] != STORE_VER) return ST_BAD_VERSION;
    if (usbproto::crc32(buf, len - 4) != rd32(buf + len - 4)) return ST_BAD_CRC;

    StoreView v;
    v.nLayouts = buf[5];
    v.active = buf[6];
    uint8_t flags = buf[7];
    v.rotate = flags & 1;
    v.rotateS = rd16(buf + 8);
    size_t cfgLen = rd16(buf + 10);
    if (v.nLayouts < 1 || v.nLayouts > MAX_LAYOUTS || v.active >= v.nLayouts || (flags & ~1u) ||
        (v.rotate && v.rotateS < MIN_ROTATE_S))
        return ST_BAD_HEADER;
    size_t end = len - 4, pos = 12;
    if (cfgLen > end - pos) return ST_BAD_HEADER;
    if (!parseConfig(buf + pos, cfgLen, v.cfg)) return ST_BAD_CONFIG;
    pos += cfgLen;
    for (int i = 0; i < v.nLayouts; i++) {
        if (pos + 2 > end) return ST_BAD_HEADER;
        size_t n = rd16(buf + pos);
        pos += 2;
        if (!n || pos + n > end) return ST_BAD_HEADER;
        Layout l;
        if (decodeLayout(buf + pos, n, l) != DEC_OK) return ST_BAD_LAYOUT;
        v.layoutOff[i] = (uint16_t)pos;
        v.layoutLen[i] = (uint16_t)n;
        pos += n;
    }
    if (pos != end) return ST_BAD_HEADER;
    out = v;
    return ST_OK;
}

// ---------------------------------------------------------------------------
// Flash slots. Header (20 bytes) in the first sector of the slot:
//   magic "ISLT" | seq u32 | len u32 | blob crc32 u32 | crc32 of the previous 16 bytes
namespace {
constexpr size_t HDR = 20;
}

bool FlashStore::readSlot(int slot, std::vector<uint8_t> &blob, uint32_t &seq)
{
    size_t base = (size_t)slot * SLOT_BYTES;
    if (base + SLOT_BYTES > f_.size()) return false;
    uint8_t h[HDR];
    if (!f_.read(base, h, HDR)) return false;
    if (memcmp(h, SLOT_MAGIC, 4) || usbproto::crc32(h, 16) != rd32(h + 16)) return false;
    size_t len = rd32(h + 8);
    if (len == 0 || len > MAX_STORE_BYTES || len > SLOT_BYTES - SECTOR) return false;
    std::vector<uint8_t> b(len);
    if (!f_.read(base + SECTOR, b.data(), len)) return false;
    if (usbproto::crc32(b.data(), len) != rd32(h + 12)) return false;
    StoreView v;
    if (parseStore(b.data(), len, v) != ST_OK) return false;
    blob.swap(b);
    seq = rd32(h + 4);
    return true;
}

bool FlashStore::load(std::vector<uint8_t> &blob)
{
    std::vector<uint8_t> b[2];
    uint32_t s[2] = {0, 0};
    bool ok[2] = {readSlot(0, b[0], s[0]), readSlot(1, b[1], s[1])};
    int pick = -1;
    if (ok[0] && ok[1]) pick = ((int32_t)(s[1] - s[0]) > 0) ? 1 : 0;
    else if (ok[0]) pick = 0;
    else if (ok[1]) pick = 1;
    cur_ = pick;
    if (pick < 0) return false;
    seq_ = s[pick];
    blob.swap(b[pick]);
    return true;
}

bool FlashStore::commit(const uint8_t *blob, size_t len)
{
    if (!len || len > MAX_STORE_BYTES) return false;
    int t = cur_ == 0 ? 1 : 0;
    size_t base = (size_t)t * SLOT_BYTES;
    if (base + SLOT_BYTES > f_.size()) return false;
    size_t eraseLen = SECTOR + ((len + SECTOR - 1) / SECTOR) * SECTOR;
    if (!f_.erase(base, eraseLen)) return false;
    // blob in <=256 B pieces: bounded stack, and each write is a unit a power cut can split
    for (size_t o = 0; o < len; o += 256)
        if (!f_.write(base + SECTOR + o, blob + o, len - o < 256 ? len - o : 256)) return false;
    uint8_t h[HDR];
    memcpy(h, SLOT_MAGIC, 4);
    uint32_t nseq = seq_ + 1;
    wr32(h + 4, nseq);
    wr32(h + 8, (uint32_t)len);
    wr32(h + 12, usbproto::crc32(blob, len));
    wr32(h + 16, usbproto::crc32(h, 16));
    if (!f_.write(base, h, HDR)) return false;
    std::vector<uint8_t> chk;
    uint32_t cs = 0;
    if (!readSlot(t, chk, cs) || chk.size() != len || memcmp(chk.data(), blob, len)) return false;
    cur_ = t;
    seq_ = nseq;
    return true;
}

// ---------------------------------------------------------------------------
uint8_t Uploader::onChunk(uint32_t total, uint32_t offset, const uint8_t *data, size_t n)
{
    if (!total || total > MAX_STORE_BYTES || !n) return NAK_BAD_LEN_;
    if ((uint64_t)offset + n > total) return NAK_BAD_LEN_;
    if (offset == 0) {
        buf_.clear();
        buf_.reserve(total);
        total_ = total;
        done_ = false;
    } else if (done_ && total == total_ && offset == lastOff_ && n == lastLen_) {
        return 0;  // final chunk repeated after a lost ACK: already stored
    } else if (!total_ || total != total_ || done_) {
        return NAK_BAD_ARG_;
    }
    if (offset < buf_.size()) {  // retransmit of data we already hold
        if (offset + n > buf_.size() || memcmp(buf_.data() + offset, data, n)) return NAK_BAD_ARG_;
        return 0;
    }
    if (offset > buf_.size()) return NAK_BAD_ARG_;  // gap
    buf_.insert(buf_.end(), data, data + n);
    lastOff_ = offset;
    lastLen_ = (uint32_t)n;
    if (buf_.size() < total_) return 0;

    StoreView v;
    uint8_t r = parseStore(buf_.data(), buf_.size(), v) == ST_OK ? sink_.commit(buf_.data(), buf_.size())
                                                                 : NAK_BAD_DATA_;
    if (r == 0) {
        done_ = true;
        buf_.clear();
        buf_.shrink_to_fit();
    } else {
        reset();  // failed upload: the host restarts from offset 0
    }
    return r;
}

}  // namespace idle

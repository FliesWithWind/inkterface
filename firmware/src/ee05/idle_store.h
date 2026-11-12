// Idle layout store ("ISTR") parsing, chunked upload assembly and the two-slot flash
// record. Pure C++11; the flash is an interface so power loss is unit tested on the host
// (firmware/host_test/idle_store_host.cpp). Format: docs/idle-mode.md "Store format".
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <vector>

#include "idle_layout.h"

namespace idle {

constexpr uint8_t STORE_MAGIC[4] = {'I', 'S', 'T', 'R'};
constexpr uint8_t STORE_VER = 1;
constexpr size_t MAX_STORE_BYTES = 49152;
constexpr size_t MAX_LAYOUTS = 8;
constexpr uint16_t MIN_ROTATE_S = 10;

enum ConfigTag : uint8_t {
    CT_WIFI_SSID = 1, CT_WIFI_PASS, CT_TZ, CT_LAT, CT_LON, CT_UNITS, CT_STEAM_KEY, CT_STEAM_ID, CT_STEAM_APPID,
    CT_LAST = CT_STEAM_APPID
};

enum StoreError : uint8_t {
    ST_OK = 0, ST_TOO_SHORT, ST_TOO_BIG, ST_BAD_MAGIC, ST_BAD_VERSION, ST_BAD_CRC, ST_BAD_HEADER, ST_BAD_CONFIG,
    ST_BAD_LAYOUT
};
const char *storeErrorName(StoreError e);

// Panel configuration pushed by the host. Text is NUL-terminated; has* tells what was sent.
struct StoreConfig {
    char wifiSsid[33] = "", wifiPass[64] = "", tz[64] = "", lat[16] = "", lon[16] = "", steamKey[41] = "",
         steamId[21] = "";
    uint8_t units = 0;       // 0 metric, 1 imperial
    uint32_t steamAppId = 0;
    uint16_t have = 0;       // bit (tag-1) set when the tag was present
    bool has(ConfigTag t) const { return have & (1u << (t - 1)); }
};

struct StoreView {
    uint8_t nLayouts = 0, active = 0;
    bool rotate = false;
    uint16_t rotateS = 0;
    StoreConfig cfg;
    uint16_t layoutOff[MAX_LAYOUTS] = {}, layoutLen[MAX_LAYOUTS] = {};  // into the parsed buffer
};

// Validates everything (crc, config, every layout through decodeLayout). On ST_OK `v` is filled.
StoreError parseStore(const uint8_t *buf, size_t len, StoreView &v);

// ---------------------------------------------------------------------------
// Flash: two slots of SLOT_BYTES. Slot layout: sector 0 = header, sectors 1.. = blob.
struct Flash {
    virtual ~Flash() {}
    virtual size_t size() const = 0;  // total bytes available (2 * SLOT_BYTES)
    virtual bool read(size_t off, uint8_t *dst, size_t n) = 0;
    virtual bool erase(size_t off, size_t n) = 0;                   // 4096-aligned
    virtual bool write(size_t off, const uint8_t *src, size_t n) = 0;  // into erased space only
};

constexpr size_t SECTOR = 4096;
constexpr size_t SLOT_BYTES = 65536;
constexpr uint8_t SLOT_MAGIC[4] = {'I', 'S', 'L', 'T'};

class FlashStore {
  public:
    explicit FlashStore(Flash &f) : f_(f) {}
    // Newest slot whose record, blob crc and ISTR content all validate. false = none (use the built-in).
    bool load(std::vector<uint8_t> &blob);
    // Writes the blob into the slot that is NOT the newest valid one, blob first and header last,
    // then reads it back. false = flash error (the previous slot is untouched and stays valid).
    bool commit(const uint8_t *blob, size_t len);
    uint32_t seq() const { return seq_; }

  private:
    bool readSlot(int slot, std::vector<uint8_t> &blob, uint32_t &seq);
    Flash &f_;
    uint32_t seq_ = 0;
    int cur_ = -1;
};

// ---------------------------------------------------------------------------
// IDLE_LAYOUT chunk assembly. Returns 0 (ACK) or a NAK reason (usbproto::Nak numbering).
constexpr uint8_t NAK_BAD_LEN_ = 2, NAK_BAD_ARG_ = 4, NAK_BAD_DATA_ = 14, NAK_STORAGE_ = 15;  // == usbproto::NAK_BAD_DATA / NAK_STORAGE (7..13 are OTA's), checked by static_assert in main.cpp + idle_mode_host.cpp

struct StoreSink {
    virtual ~StoreSink() {}
    // Called once with a fully validated blob. 0 = stored durably, else NAK reason (15 STORAGE).
    virtual uint8_t commit(const uint8_t *blob, size_t len) = 0;
};

class Uploader {
  public:
    explicit Uploader(StoreSink &s) : sink_(s) {}
    uint8_t onChunk(uint32_t total, uint32_t offset, const uint8_t *data, size_t n);
    void reset() { buf_.clear(); buf_.shrink_to_fit(); total_ = 0; done_ = false; }
    size_t received() const { return buf_.size(); }
    bool active() const { return total_ != 0 && !done_; }

  private:
    StoreSink &sink_;
    std::vector<uint8_t> buf_;
    uint32_t total_ = 0;
    uint32_t lastOff_ = 0, lastLen_ = 0;
    bool done_ = false;  // last upload committed; only a repeat of its final chunk is tolerated
};

}  // namespace idle

// vim: foldmethod=marker:foldmarker={{{,}}}
//
// Inkterface firmware for the Seeed XIAO ePaper Display Board EE05 (XIAO ESP32-S3 Plus) driving the
// 5.83" 648x480 UC8179 panel. USB is the only transport (BLE is gone).
//
//   * HOST mode: the Decky plugin renders and pushes 1-bit RECTs over the USB CDC port
//     (docs/usb-protocol-v1.md); the bare-metal driver (uc8179_fast.h) refreshes in 0.67 s.
//   * IDLE mode: when the host goes quiet (default 60 s, set by the MODE frame) the panel draws a
//     layout stored in flash (docs/idle-mode.md) with data it fetches over Wi-Fi. Wi-Fi is on only
//     while idle.
//
// The logic lives in pure C++ units that are unit tested on the host (firmware/host_test/):
// usbproto, idle_mode, idle_layout, idle_store, idle_render, idle_painter, idle_sources.
// This file is the Arduino glue: flash, Wi-Fi, HTTP, NVS, the EPD driver and the main loop.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_partition.h>
#include <string>
#include <time.h>

#include "idle_cfg.h"
#include "idle_config.h"
#include "idle_env.h"
#include "idle_fallback.h"
#include "idle_layoutset.h"
#include "idle_painter.h"
#include "idle_sources.h"
#include "idle_store.h"
#include "ota_usb.h"  // ota_usb::attach(USB) in setup()
#include "uc8179_fast.h"
#include "usb_bus_watch.h"
#include "usbproto.h"

#ifndef GIT_REVISION
#define GIT_REVISION "0.0.0-dev+unknown"  // firmware/autorev.py sets it for real builds
#endif

// Serial (USB-Serial-JTAG CDC) is the host protocol data stream, so debug text must not go there raw.
// Default: forwarded as LOG frames. Build with -D INK_DEBUG_SERIAL for raw Serial debug (bench only,
// breaks the protocol). Never log Wi-Fi credentials or the Steam key.
#ifdef INK_DEBUG_SERIAL
#define Debug Serial
#else
struct LogPrint : public Print {
    // Every line starts with "[<millis>] ": text queues while no host session exists (idle mode), so the host
    // only learns when something happened from the panel's own clock.
    bool bol_ = true;
    size_t write(uint8_t c) override { return write(&c, 1); }
    size_t write(const uint8_t *p, size_t n) override
    {
        for (size_t i = 0; i < n;) {
            if (bol_) {
                char s[16];
                int k = snprintf(s, sizeof(s), "[%lu] ", (unsigned long)millis());
                usbproto::logWrite((const uint8_t *)s, (size_t)k);
            }
            size_t j = i;
            while (j < n && p[j] != '\n') j++;
            if (j < n) j++;   // keep the newline with its line
            usbproto::logWrite(p + i, j - i);
            bol_ = p[j - 1] == '\n';
            i = j;
        }
        return n;
    }
};
static LogPrint DebugLog;
#define Debug DebugLog
#endif

// ---------------------------------------------------------------------------
// Runtime configuration {{{
// Layers (idle_cfg.h): idle_config.h defaults < legacy NVS JSON (read-only migration of what the
// BLE app provisioned) < the CONFIG section of the stored ISTR blob (pushed over USB).
// SECURITY: Wi-Fi password and Steam key are plain text in flash/NVS; no flash encryption in this build.
static idle::RuntimeCfg makeDefaults()
{
    idle::RuntimeCfg c;
    c.ssid = WIFI_SSID;
    c.pass = WIFI_PASS;
    c.tz = IDLE_TZ;
    c.lat = String((double)IDLE_LAT, 4).c_str();
    c.lon = String((double)IDLE_LON, 4).c_str();
    c.imperial = !IDLE_USE_METRIC;
    c.steamKey = STEAM_API_KEY;
    c.steamId = STEAM_ID64;
    c.appId = STEAM_APPID;
    return c;
}
static idle::RuntimeCfg CFG = makeDefaults();

static Preferences PREFS;
static const char *PREFS_NS = "inktf";

static void loadLegacyConfig()
{
    if (!PREFS.begin(PREFS_NS, true)) return;
    String blob = PREFS.getString("idlecfg", "");
    PREFS.end();
    if (!blob.length()) return;
    JsonDocument doc;
    if (!deserializeJson(doc, blob)) CFG.applyLegacyJson(doc);
}

static void applyStoreConfig(const idle::StoreConfig &c) { CFG.applyStore(c); }
static idle::Config sourceCfg() { return CFG.sourceConfig(); }
// }}}

// ---------------------------------------------------------------------------
// Flash: raw partition "idlestore" (docs/idle-mode.md "Storage") {{{
struct PartFlash : idle::Flash {
    const esp_partition_t *p = nullptr;
    bool ok() const { return p != nullptr; }
    void init()
    {
        p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, (esp_partition_subtype_t)0x40, "idlestore");
    }
    size_t size() const override { return p ? p->size : 0; }
    bool read(size_t off, uint8_t *dst, size_t n) override { return p && esp_partition_read(p, off, dst, n) == ESP_OK; }
    bool erase(size_t off, size_t n) override { return p && esp_partition_erase_range(p, off, n) == ESP_OK; }
    bool write(size_t off, const uint8_t *src, size_t n) override { return p && esp_partition_write(p, off, src, n) == ESP_OK; }
};
static PartFlash FLASH;
static idle::FlashStore STORE(FLASH);
static idle::LayoutSet LAYOUTS;
// }}}

// ---------------------------------------------------------------------------
// EPD {{{
static fastpartial::Epd FAST_EPD;
// DC-DC stays on this long after a partial: a screen that changes all the time refreshes back to back
static constexpr uint32_t EPD_HOLD_MS = 2000;

static_assert(idle::NAK_BAD_DATA_ == usbproto::NAK_BAD_DATA && idle::NAK_STORAGE_ == usbproto::NAK_STORAGE,
              "idle store NAK numbers must match usbproto (and stay clear of OTA 7..13)");
static_assert(fastpartial::PANEL_W == usbproto::PANEL_W && fastpartial::PANEL_H == usbproto::PANEL_H,
              "usbproto panel geometry must match the driver");

struct EpdPanel : usbproto::Panel {
    void present(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h, bool full) override
    {
        if (full) FAST_EPD.full(fb);
        else FAST_EPD.partial(fb, x, y, w, h);
        const fastpartial::Timing &t = FAST_EPD.last;
        Debug.printf("host %s refresh %u ms (refresh %u, power %u+%u, data %u) heap %u min %u\n", t.full ? "full" : "partial",
                     t.total / 1000, t.refresh / 1000, t.powerOn / 1000, t.powerOff / 1000, t.data / 1000,
                     (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());   // heap: soak diagnostics (hw_e2e.py)
    }
};

struct EpdPresenter : idle::Presenter {
    const uint8_t *glass() override { return FAST_EPD.glass(); }
    void full(const uint8_t *fb) override { FAST_EPD.full(fb); log("full"); }
    void partial(const uint8_t *fb, uint16_t x, uint16_t y, uint16_t w, uint16_t h) override
    {
        FAST_EPD.partial(fb, x, y, w, h);
        log("partial");
    }
    void gray4(const uint8_t *g) override { FAST_EPD.gray4(g); log("gray4"); }
    static void log(const char *k)
    {
        const fastpartial::Timing &t = FAST_EPD.last;
        Debug.printf("idle %s refresh %u ms (refresh %u, data %u)\n", k, t.total / 1000, t.refresh / 1000, t.data / 1000);
    }
};
static EpdPresenter PRESENTER;
// }}}

// ---------------------------------------------------------------------------
// Wi-Fi (idle only), NTP, HTTP {{{
enum WifiState : uint8_t { WIFI_IDLE_OFF, WIFI_CONNECTING, WIFI_UP, WIFI_FAILED };
static WifiState WIFI_STATE = WIFI_IDLE_OFF;
static uint32_t WIFI_T0 = 0;
static constexpr uint32_t WIFI_CONNECT_MS = 20000, WIFI_RETRY_MS = 60000;

static void wifiBegin(uint32_t now)
{
    if (CFG.ssid.empty()) { WIFI_STATE = WIFI_FAILED; WIFI_T0 = now; return; }
    WiFi.setHostname("inktf");
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(true);
    WiFi.begin(CFG.ssid.c_str(), CFG.pass.c_str());
    WIFI_STATE = WIFI_CONNECTING;
    WIFI_T0 = now;
    Debug.println("wifi: connecting");
}

static void wifiOff()
{
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
    WIFI_STATE = WIFI_IDLE_OFF;
}

// Non-blocking: called every idle loop pass so USB keeps being serviced.
static void wifiPoll(uint32_t now)
{
    switch (WIFI_STATE) {
    case WIFI_CONNECTING:
        if (WiFi.status() == WL_CONNECTED) {
            WIFI_STATE = WIFI_UP;
            configTzTime(CFG.tz.c_str(), "pool.ntp.org", "time.nist.gov");
            Debug.println("wifi: connected");
        } else if (now - WIFI_T0 > WIFI_CONNECT_MS) {
            WiFi.disconnect(false, false);
            WIFI_STATE = WIFI_FAILED;
            WIFI_T0 = now;
            Debug.println("wifi: connect failed");
        }
        break;
    case WIFI_UP:
        if (WiFi.status() != WL_CONNECTED) { WIFI_STATE = WIFI_CONNECTING; WIFI_T0 = now; WiFi.reconnect(); }
        break;
    case WIFI_FAILED:
        if (now - WIFI_T0 > WIFI_RETRY_MS) wifiBegin(now);
        break;
    default: break;
    }
}

// Stream wrapper for ArduinoJson: counts bytes (size cap), enforces the deadline, polls the abort hook.
struct GuardedStream : Stream {
    Stream &in;
    size_t cap, n = 0;
    uint32_t deadline;
    bool (*abortFn)(void *);
    void *abortCtx;
    bool tooBig = false, timedOut = false, aborted = false;
    GuardedStream(Stream &s, size_t capBytes, uint32_t t0, uint32_t timeoutMs, bool (*a)(void *), void *c)
        : in(s), cap(capBytes), deadline(t0 + timeoutMs), abortFn(a), abortCtx(c) {}
    int available() override { return in.available(); }
    int peek() override { return in.peek(); }
    size_t write(uint8_t) override { return 0; }
    int read() override
    {
        while (!in.available()) {
            if (abortFn && abortFn(abortCtx)) { aborted = true; return -1; }
            if ((int32_t)(millis() - deadline) > 0) { timedOut = true; return -1; }
            delay(1);
            if (!in.available() && !static_cast<WiFiClient &>(in).connected()) return -1;
        }
        if (n >= cap) { tooBig = true; return -1; }
        n++;
        return in.read();
    }
    size_t readBytes(char *b, size_t len) override
    {
        size_t i = 0;
        for (; i < len; i++) {
            int c = read();
            if (c < 0) break;
            b[i] = (char)c;
        }
        return i;
    }
};

struct ArduinoHttp : idle::Http {
    idle::HttpStatus getJson(const char *url, const JsonDocument *filter, JsonDocument &doc, size_t maxBytes,
                             uint32_t timeoutMs, bool (*shouldAbort)(void *), void *abortCtx) override
    {
        using namespace idle;
        uint32_t t0 = millis();
        HTTPClient http;
        WiFiClientSecure tls;
        WiFiClient plain;
        // shortcut: TLS without certificate validation (public endpoints, home device); the Steam key
        // travels in a query string, so a hostile network could read it. Pin the CA bundle to fix.
        bool https = !strncmp(url, "https", 5);
        bool began;
        if (https) { tls.setInsecure(); began = http.begin(tls, url); }
        else began = http.begin(plain, url);
        if (!began) return HTTP_NET;
        http.useHTTP10(true);  // no chunked transfer: the body is the JSON document
        http.setConnectTimeout(timeoutMs < 5000 ? timeoutMs : 5000);
        http.setTimeout(timeoutMs);
        if (shouldAbort && shouldAbort(abortCtx)) { http.end(); return HTTP_ABORTED; }
        int code = http.GET();
        if (code <= 0) {
            http.end();
            return (millis() - t0 >= timeoutMs || code == HTTPC_ERROR_READ_TIMEOUT) ? HTTP_TIMEOUT : HTTP_NET;
        }
        if (code != 200) { http.end(); return HTTP_STATUS; }
        GuardedStream gs(http.getStream(), maxBytes, t0, timeoutMs, shouldAbort, abortCtx);
        DeserializationError err = filter ? deserializeJson(doc, gs, DeserializationOption::Filter(*filter))
                                          : deserializeJson(doc, gs);
        http.end();
        if (gs.aborted) return HTTP_ABORTED;
        if (gs.tooBig) return HTTP_TOO_BIG;
        if (gs.timedOut) return HTTP_TIMEOUT;
        return err ? HTTP_PARSE : HTTP_OK;
    }
};
static ArduinoHttp HTTP;
static idle::PosixEnv ENV;
// }}}

// ---------------------------------------------------------------------------
// Idle runner {{{
struct IdleRun {
    idle::SourceSet sources;
    idle::Painter painter;
    uint32_t enteredAt = 0, nextTick = 0, lastPaint = 0;
    bool painted = false, firstPollDone = false, dirty = true, wasSynced = false;
    IdleRun() : sources(ENV), painter(PRESENTER) {}
};
static IdleRun *RUN = nullptr;
static volatile bool RECONFIGURE = false;

static bool wakeAbort(void *) { return Serial.available() > 0; }  // a host byte arrived: stop fetching

static void idleConfigure()
{
    ENV.globalTz = CFG.tz.c_str();
    RUN->sources.configure(LAYOUTS.current(), sourceCfg());
    RUN->painter.invalidate();
    RUN->firstPollDone = false;
    RUN->dirty = true;
}

static void idleEnter(uint32_t now)
{
    Debug.printf("idle: enter (heap free %u, lowest ever %u, psram free %u)\n", (unsigned)ESP.getFreeHeap(),
                 (unsigned)ESP.getMinFreeHeap(), (unsigned)ESP.getFreePsram());
    RUN = new IdleRun();
    RUN->enteredAt = now;
    RUN->sources.setAbort(wakeAbort, nullptr);
    LAYOUTS.restart(now);
    idleConfigure();
    wifiBegin(now);
}

static void idleLeave()
{
    Debug.printf("idle: leave (heap free %u, lowest ever %u, psram free %u)\n", (unsigned)ESP.getFreeHeap(),
                 (unsigned)ESP.getMinFreeHeap(), (unsigned)ESP.getFreePsram());
    delete RUN;
    RUN = nullptr;
    wifiOff();
    FAST_EPD.invalidate();  // the glass holds the idle picture: the host's next refresh must be a full one
}

static void idlePaint(uint32_t now)
{
    const idle::Layout &l = LAYOUTS.current();
    idle::PaintResult r = RUN->painter.paint(l, RUN->sources, ENV);
    (void)r;
    RUN->painted = true;
    RUN->dirty = false;
    RUN->lastPaint = now;
    // mono: next repaint on the minute (clock widgets) or every refreshS; gray4: every refreshS
    uint32_t period = (uint32_t)l.refreshS * 1000u;
    if (l.mode == idle::RM_MONO && l.refreshS == 60 && ENV.synced()) period = (60 - (uint32_t)(ENV.epoch() % 60)) * 1000u + 300u;
    RUN->nextTick = now + period;
}

static void idleLoop(uint32_t now)
{
    if (RECONFIGURE) { RECONFIGURE = false; idleConfigure(); }
    if (LAYOUTS.tick(now)) idleConfigure();
    wifiPoll(now);
    if (WIFI_STATE == WIFI_UP && RUN->sources.nextDueMs(now) == 0) {
        if (RUN->sources.poll(now, HTTP)) RUN->dirty = true;
        RUN->firstPollDone = true;
    }
    bool synced = ENV.synced();
    if (synced != RUN->wasSynced) { RUN->wasSynced = synced; RUN->dirty = true; }

    const idle::Layout &l = LAYOUTS.current();
    bool tick = (int32_t)(now - RUN->nextTick) >= 0;
    if (!RUN->painted) {
        // gray4 costs a 4 s flash: wait for the first fetch (or give up on Wi-Fi) before the first paint
        bool wait = l.mode == idle::RM_GRAY4 && !RUN->firstPollDone && WIFI_STATE != WIFI_FAILED &&
                    now - RUN->enteredAt < 30000;
        if (!wait) idlePaint(now);
    } else if (tick || (RUN->dirty && l.mode == idle::RM_MONO)) {
        idlePaint(now);
    }
}
// }}}

// ---------------------------------------------------------------------------
// USB glue {{{
struct SerialLink : usbproto::Link {
    size_t write(const uint8_t *p, size_t n) override { return Serial.write(p, n); }
    size_t writable() const override { return Serial.availableForWrite(); }
};

struct IdleGlue : usbproto::IdleHooks, idle::StoreSink {
    idle::Uploader up;
    IdleGlue() : up(*this) {}
    uint8_t layoutChunk(uint32_t total, uint32_t offset, const uint8_t *d, size_t n) override
    {
        return up.onChunk(total, offset, d, n);
    }
    bool selectLayout(uint8_t idx) override
    {
        if (!LAYOUTS.select(idx)) return false;
        RECONFIGURE = true;
        return true;
    }
    void timeoutChanged(uint16_t s) override
    {
        if (PREFS.begin(PREFS_NS, false)) { PREFS.putUShort("idle_to", s); PREFS.end(); }
    }
    uint8_t commit(const uint8_t *blob, size_t len) override
    {
        if (!FLASH.ok() || !STORE.commit(blob, len)) return idle::NAK_STORAGE_;
        std::vector<uint8_t> v(blob, blob + len);
        if (!LAYOUTS.setStore(v)) return idle::NAK_BAD_DATA_;  // validated already; defensive
        if (LAYOUTS.config()) applyStoreConfig(*LAYOUTS.config());
        RECONFIGURE = true;
        Debug.printf("idle: stored %u bytes, %u layout(s)\n", (unsigned)len, LAYOUTS.count());
        return 0;
    }
};

static SerialLink USB_LINK;
static EpdPanel USB_PANEL;
static IdleGlue GLUE;
// RX queue 16 KiB; HWCDC's ISR silently DROPS bytes when it is full (no USB-level back-pressure), so
// the host is told to keep <= rxWindow bytes unacknowledged.
static constexpr size_t USB_RX_QUEUE = 16384;
static const usbproto::Info USB_INFO = {GIT_REVISION, usbproto::CAP_GRAY4 | usbproto::CAP_IDLE_STORAGE,
                                        (uint32_t)idle::MAX_STORE_BYTES, USB_RX_QUEUE / 2};
static usbproto::Device USB(USB_LINK, USB_PANEL, USB_INFO, &GLUE);


static void usbPoll(uint32_t now)
{
    static bool prevUp = false;
    // HWCDC: true when a USB host is enumerated, NOT when a program opened the port. A HELLO sent
    // here may be lost; the host's HELLO request is the reliable path.
    bool up = Serial;
    if (up && !prevUp) {
        Debug.println("usb: Serial came up");
        USB.onAttach();
    }
    if (!up && prevUp) Debug.println("usb: Serial went down");
    prevUp = up;
    if (up) {
        uint8_t buf[256];
        for (int budget = 32; budget > 0 && Serial.available() > 0; budget--) {
            size_t n = Serial.read(buf, sizeof(buf));
            if (!n) break;
            USB.onBytes(buf, n, now);
        }
    }
    USB.tick(now);
}

// Fallback for a lost sleep hint (docs/idle-mode.md "Sleep and wake"): a host that suspends keeps VBUS but stops the bus
// (no SOF), long before the 60 s silence timeout. The Arduino core's tick hook already turns SOF into HWCDC::isPlugged().
static busdown::Watch BUS_WATCH;

static void busPoll(uint32_t now)
{
    if (USB.idle().idle()) { BUS_WATCH.reset(); return; }   // idle owns the glass: nothing to detect
    bool fire = BUS_WATCH.update(Serial.isPlugged(), USB.extBusy(now), now);
    if (uint32_t gap = BUS_WATCH.takeOutageMs()) Debug.printf("usb: SOF gap %u ms (bus came back)\n", (unsigned)gap);
    if (fire) {
        Debug.printf("usb: no SOF for %u ms (host suspended or gone): idle\n", (unsigned)busdown::DOWN_MS);
        USB.onBusSuspend();
    }
}
// }}}

// TLS handshakes and the Steam fetchers (URL buffers, JSON documents) need more than the 8 KiB default.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

void setup()
{
    ota_usb::attach(USB);  // USB OTA receiver + rollback guard (ota_usb.cpp); keep this first

    // big RX queue: a refresh blocks the loop for up to ~4 s while the host keeps sending
    Serial.setRxBufferSize(USB_RX_QUEUE);
    Serial.setTxBufferSize(1024);
    Serial.begin(115200);

    loadLegacyConfig();

    FAST_EPD.begin();  // pins + SPI; the controller is initialised by the first refresh
    FAST_EPD.holdMs = EPD_HOLD_MS;

    FLASH.init();
    LAYOUTS.setFallback(idle::kFallbackLayout, idle::kFallbackLayoutLen);
    std::vector<uint8_t> blob;
    if (FLASH.ok() && STORE.load(blob) && LAYOUTS.setStore(blob)) {
        if (LAYOUTS.config()) applyStoreConfig(*LAYOUTS.config());
        Debug.printf("idle: loaded store seq %u, %u layout(s)\n", (unsigned)STORE.seq(), LAYOUTS.count());
    } else {
        Debug.println("idle: no valid store, using the built-in layout");
    }

    if (PREFS.begin(PREFS_NS, true)) {
        uint16_t to = PREFS.getUShort("idle_to", 0);
        PREFS.end();
        if (to) USB.setIdleTimeoutS(to);
    }
    USB.start(millis());
}

void loop()
{
    uint32_t now = millis();
    usbPoll(now);
    busPoll(now);
    FAST_EPD.service();

    idlemode::Controller &ctl = USB.idle();
    if (ctl.idle() && !RUN) idleEnter(now);
    if (!ctl.idle() && RUN) idleLeave();
    if (RUN) idleLoop(now);

    delay(USB.alive() || RUN ? 1 : 10);
}

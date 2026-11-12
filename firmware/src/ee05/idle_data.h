// Values the idle renderer reads (idle_render) and the objects that produce them
// (idle_sources). Pure C++11; the firmware and the host tests supply the Env.
#pragma once

#include <stdint.h>
#include <string.h>
#include <time.h>

namespace idle {

struct Value {
    enum Kind : uint8_t { NONE = 0, NUM = 1, STR = 2, TIME = 3 };
    Kind kind = NONE;
    double num = 0;  // NUM value, or epoch seconds (UTC) for TIME
    char str[64] = {0};

    static Value number(double v) { Value r; r.kind = NUM; r.num = v; return r; }
    static Value time(int64_t epoch) { Value r; r.kind = TIME; r.num = (double)epoch; return r; }
    static Value text(const char *s)
    {
        Value r;
        r.kind = STR;
        strncpy(r.str, s ? s : "", sizeof(r.str) - 1);
        return r;
    }
    bool has() const { return kind != NONE; }
};

// What a layout binds to. `src` is the index into Layout::src, `field` / `idx` as in
// docs/idle-mode.md. Missing data is NONE (the renderer then draws a placeholder).
struct Data {
    virtual ~Data() {}
    virtual Value get(uint8_t src, uint8_t field, uint8_t idx) const = 0;
};

// Wall clock and zone conversion. Firmware: time()/setenv(TZ). Tests: a fixed epoch.
struct Env {
    virtual ~Env() {}
    virtual int64_t epoch() const = 0;  // seconds since 1970 UTC; < 1.6e9 means "not synced"
    // Local broken-down time for `epoch` in POSIX TZ string `tz` (nullptr/"" = the global zone).
    virtual void localTime(int64_t epoch, const char *tz, struct tm &out) const = 0;
    bool synced() const { return epoch() >= 1600000000; }
};

// Runtime configuration pushed by the host (store CONFIG section) over idle_config.h defaults.
// Wi-Fi credentials are deliberately not here: only main.cpp's connect code sees them.
struct Config {
    const char *tz = "UTC0";        // POSIX TZ for the global zone
    const char *lat = "52.3676";    // decimal degrees, text so no float formatting surprises
    const char *lon = "4.9041";
    bool imperial = false;          // weather units when a source does not override
    const char *steamKey = "";      // Steam Web API key ("" = personal stats off)
    const char *steamId = "";       // 64-bit SteamID
    uint32_t steamAppId = 730;      // default app for steam `players`
    bool allowHttp = false;         // TEST ONLY: permit http:// URLs (host fake server)
};

}  // namespace idle

// Idle data sources: fetch weather / Steam / arbitrary JSON / clock values over
// HTTP and expose them as idle::Value (docs/idle-mode.md "Fields" table).
//
// Pure C++11 + ArduinoJson. No Arduino.h here: the firmware supplies an Http
// implementation (HTTPClient streaming into ArduinoJson with a Filter, a
// byte-counting wrapper and abort polling); the host tests supply a POSIX-socket
// one talking to a local python http.server. Both share this interface.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ArduinoJson.h>

#include "idle_data.h"
#include "idle_layout.h"

namespace idle {

// Result of a single HTTP+JSON fetch.
enum HttpStatus {
    HTTP_OK = 0,
    HTTP_NET,      // could not connect / socket error (connection refused, DNS, reset)
    HTTP_STATUS,   // connected, but response status was not 200
    HTTP_TOO_BIG,  // response body exceeded maxBytes before parsing could finish
    HTTP_TIMEOUT,  // exceeded timeoutMs
    HTTP_PARSE,    // body was not valid JSON (or did not fit the filter)
    HTTP_ABORTED,  // shouldAbort() asked us to stop mid-fetch
};

// A JSON-over-HTTP transport. `filter` (may be null) is an ArduinoJson filter
// document applied while streaming to bound memory; `doc` receives the parsed
// (filtered) result. `maxBytes` caps the body read (HTTP_TOO_BIG past it);
// `timeoutMs` bounds the whole call. `shouldAbort`, if non-null, is polled
// during the transfer and makes the call return HTTP_ABORTED when it returns
// true. Implementations must never leak credentials from `url` into any log.
struct Http {
    virtual ~Http() {}
    virtual HttpStatus getJson(const char *url, const JsonDocument *filter, JsonDocument &doc,
                               size_t maxBytes, uint32_t timeoutMs, bool (*shouldAbort)(void *),
                               void *abortCtx) = 0;
};

// Real endpoint base URLs. Defaults are the production https hosts; tests
// override them so a fake server on 127.0.0.1 answers. Each base is everything
// up to and including the host (no trailing slash), e.g. "https://api.open-meteo.com".
struct Endpoints {
    const char *openMeteo = "https://api.open-meteo.com";
    const char *steamApi = "https://api.steampowered.com";
    const char *steamStore = "https://store.steampowered.com";

    void overrideAll(const char *base)  // test only: point every Steam/weather host at `base`
    {
        openMeteo = base;
        steamApi = base;
        steamStore = base;
    }
};

// Holds the current value of every idle field and refreshes them on a schedule.
// Construct with an Env, call configure() once per layout, then poll() on a
// timer. get() reads the latest values (stale values are kept across a failed
// fetch). Not thread-safe; drive it from one task.
class SourceSet : public Data {
  public:
    explicit SourceSet(Env &env);

    // (Re)load the sources from a decoded layout. Copies everything it needs
    // (lat/lon override, appid, url/pointer, units, poll interval) into its own
    // storage; keeps no pointer into `layout` or `cfg`. Resets all values.
    void configure(const Layout &layout, const Config &cfg);

    // Fetch every source whose interval has elapsed (on the first call after
    // configure(), all of them are due). Returns true if any value changed.
    // Between sources it polls the abort callback (set via setAbort) and stops
    // early with no further fetches if it fires. A failed fetch keeps the last
    // good values and is retried after min(pollS, 60 s).
    bool poll(uint32_t nowMs, Http &http);

    // Milliseconds until the earliest source is next due (0 if one is due now).
    uint32_t nextDueMs(uint32_t nowMs) const;

    // Abort hook checked between sources during poll() (e.g. host woke up).
    void setAbort(bool (*fn)(void *), void *ctx);

    // Data interface: latest value for (source index, field id, array index).
    Value get(uint8_t src, uint8_t field, uint8_t idx) const override;

    // Status of the last fetch attempt for source index `src`.
    HttpStatus lastStatus(uint8_t src) const;

    // Test seam: point weather/steam endpoints at a fake base URL.
    Endpoints &endpoints() { return ep_; }

    // shortcut: MAX_SOURCES (8) sources, 3 forecast days and 3 recent games as
    // in the fields table; upgrade alongside idle_layout.h if those grow.
    static const int FORECAST_DAYS = 3;
    static const int RECENT_GAMES = 3;

  private:
    struct SrcState {
        uint8_t type = 0;      // SrcType, 0 = unused slot
        uint8_t flags = 0;
        uint16_t pollS = 0;    // effective interval, seconds
        char s1[256] = {0};    // tz / "lat,lon" / appid / url
        char s2[132] = {0};    // json pointer (http_json)
        uint32_t nextMs = 0;   // when this source is next due
        bool everPolled = false;
        HttpStatus status = HTTP_OK;
    };

    // Per-source value stores.
    struct Weather {
        Value temp, code, condition, max, min, sunrise, sunset, rain;
        Value fday[FORECAST_DAYS], fmax[FORECAST_DAYS], fmin[FORECAST_DAYS];
        Value fcode[FORECAST_DAYS], fcond[FORECAST_DAYS], frain[FORECAST_DAYS];
    };
    struct Steam {
        Value players, online, persona, level, gamesOwned, hoursTotal;
        Value friendsOnline, friendsInGame;
        Value recentName[RECENT_GAMES], recentHours2w[RECENT_GAMES];
        Value achName, achUnlocked, achTotal;
    };

    Env &env_;
    Config cfg_;
    Endpoints ep_;
    bool (*abortFn_)(void *) = nullptr;
    void *abortCtx_ = nullptr;

    SrcState src_[MAX_SOURCES];
    Weather weather_[MAX_SOURCES];
    Steam steam_[MAX_SOURCES];
    Value httpJson_[MAX_SOURCES];
    int nSrc_ = 0;

    bool fetchWeather(int i, Http &http);
    bool fetchSteam(int i, Http &http);
    bool fetchHttpJson(int i, Http &http);

    bool aborting();
    static Value weatherValue(const Weather &w, uint8_t field, uint8_t idx);
    static Value steamValue(const Steam &s, uint8_t field, uint8_t idx);
};

// Map an Open-Meteo WMO weather code to a short condition label. Ported from
// main.cpp weatherLabel(); exposed for tests.
const char *weatherLabel(int code);

// 3-letter uppercase weekday ("MON".."SUN") for an ISO date "YYYY-MM-DD".
// Pure arithmetic (Sakamoto); returns "" if the string is malformed.
const char *isoWeekday(const char *isoDate);

}  // namespace idle

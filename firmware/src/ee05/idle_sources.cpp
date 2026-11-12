// Implementation of the idle data sources. See idle_sources.h. Pure C++11 +
// ArduinoJson; the Http transport is injected.
#include "idle_sources.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace idle {

// ---------------------------------------------------------------------------
// helpers

const char *weatherLabel(int code)
{
    if (code == 0) return "Clear";
    if (code <= 2) return "Partly Cloudy";
    if (code == 3) return "Overcast";
    if (code <= 48) return "Fog";
    if (code <= 57) return "Drizzle";
    if (code <= 67) return "Rain";
    if (code <= 77) return "Snow";
    if (code <= 82) return "Showers";
    if (code <= 86) return "Snow Showers";
    return "Storm";
}

const char *isoWeekday(const char *isoDate)
{
    // "YYYY-MM-DD" -> "MON".."SUN" via Sakamoto's algorithm (pure arithmetic).
    static const char *kDays[7] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
    if (!isoDate || strlen(isoDate) < 10) return "";
    if (isoDate[4] != '-' || isoDate[7] != '-') return "";
    for (int p : {0, 1, 2, 3, 5, 6, 8, 9}) {
        if (isoDate[p] < '0' || isoDate[p] > '9') return "";
    }
    int y = (isoDate[0] - '0') * 1000 + (isoDate[1] - '0') * 100 + (isoDate[2] - '0') * 10 +
            (isoDate[3] - '0');
    int m = (isoDate[5] - '0') * 10 + (isoDate[6] - '0');
    int d = (isoDate[8] - '0') * 10 + (isoDate[9] - '0');
    if (m < 1 || m > 12 || d < 1 || d > 31) return "";
    static const int t[] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
    int yy = y;
    if (m < 3) yy -= 1;
    int dow = (yy + yy / 4 - yy / 100 + yy / 400 + t[m - 1] + d) % 7;  // 0 = Sunday
    return kDays[dow];
}

// "HH:MM" tail of an ISO "YYYY-MM-DDTHH:MM..." timestamp. Returns "" if short.
static void isoTimeTail(const char *iso, char out[6])
{
    out[0] = '\0';
    if (!iso || strlen(iso) < 16) return;
    memcpy(out, iso + 11, 5);
    out[5] = '\0';
}

static void copyStr(char *dst, size_t cap, const char *src)
{
    if (!src) src = "";
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

// Build "https://host/path..." (or whatever scheme the base carries) from an
// Endpoints base and a path+query. Caller owns the buffer.
static void joinUrl(char *out, size_t cap, const char *base, const char *path)
{
    snprintf(out, cap, "%s%s", base, path);
}

// ---------------------------------------------------------------------------
// SourceSet

SourceSet::SourceSet(Env &env) : env_(env) {}

void SourceSet::setAbort(bool (*fn)(void *), void *ctx)
{
    abortFn_ = fn;
    abortCtx_ = ctx;
}

bool SourceSet::aborting()
{
    return abortFn_ && abortFn_(abortCtx_);
}

void SourceSet::configure(const Layout &layout, const Config &cfg)
{
    cfg_ = cfg;
    nSrc_ = layout.nSources < (int)MAX_SOURCES ? layout.nSources : (int)MAX_SOURCES;
    for (int i = 0; i < (int)MAX_SOURCES; i++) {
        src_[i] = SrcState{};
        weather_[i] = Weather{};
        steam_[i] = Steam{};
        httpJson_[i] = Value{};
    }
    for (int i = 0; i < nSrc_; i++) {
        const Source &s = layout.src[i];
        src_[i].type = s.type;
        src_[i].flags = s.flags;
        src_[i].pollS = layout.effectivePollS(s);
        src_[i].nextMs = 0;        // all due on first poll
        src_[i].everPolled = false;
        src_[i].status = HTTP_OK;
        copyStr(src_[i].s1, sizeof(src_[i].s1), layout.str(s.s1));
        copyStr(src_[i].s2, sizeof(src_[i].s2), layout.str(s.s2));
    }
}

uint32_t SourceSet::nextDueMs(uint32_t nowMs) const
{
    uint32_t best = UINT32_MAX;
    for (int i = 0; i < nSrc_; i++) {
        if (src_[i].type == SRC_CLOCK) continue;  // clock has no fetch
        uint32_t due = src_[i].nextMs;
        uint32_t wait = (due > nowMs) ? (due - nowMs) : 0;
        if (wait < best) best = wait;
    }
    return best == UINT32_MAX ? 0 : best;
}

bool SourceSet::poll(uint32_t nowMs, Http &http)
{
    bool changed = false;
    for (int i = 0; i < nSrc_; i++) {
        if (aborting()) break;
        SrcState &st = src_[i];
        if (st.type == SRC_CLOCK) continue;  // produced from env at get() time
        bool due = !st.everPolled || (int32_t)(nowMs - st.nextMs) >= 0;
        if (!due) continue;
        st.everPolled = true;

        bool ok = false;
        switch (st.type) {
            case SRC_WEATHER: ok = fetchWeather(i, http); break;
            case SRC_STEAM: ok = fetchSteam(i, http); break;
            case SRC_HTTP_JSON: ok = fetchHttpJson(i, http); break;
            default: ok = false; break;
        }
        if (ok) changed = true;

        // schedule next: success -> full interval; failure -> retry sooner
        uint32_t interval = (uint32_t)st.pollS * 1000u;
        if (!ok) {
            uint32_t retry = st.pollS < 60 ? (uint32_t)st.pollS * 1000u : 60000u;
            interval = retry;
        }
        st.nextMs = nowMs + interval;
    }
    return changed;
}

HttpStatus SourceSet::lastStatus(uint8_t src) const
{
    if (src >= nSrc_) return HTTP_NET;
    return src_[src].status;
}

// ---------------------------------------------------------------------------
// weather (Open-Meteo)

bool SourceSet::fetchWeather(int i, Http &http)
{
    SrcState &st = src_[i];
    Weather &w = weather_[i];

    // units: source flags (SF_UNITS_MASK) win over Config.imperial.
    uint8_t units = st.flags & SF_UNITS_MASK;
    bool imperial = units == 2 ? true : units == 1 ? false : cfg_.imperial;
    const char *tempUnit = imperial ? "fahrenheit" : "celsius";

    // lat/lon: per-source "lat,lon" override (s1) else Config.
    char lat[16], lon[16];
    copyStr(lat, sizeof(lat), cfg_.lat);
    copyStr(lon, sizeof(lon), cfg_.lon);
    if (st.s1[0]) {
        const char *comma = strchr(st.s1, ',');
        if (comma) {
            size_t ln = (size_t)(comma - st.s1);
            if (ln < sizeof(lat)) {
                memcpy(lat, st.s1, ln);
                lat[ln] = '\0';
                copyStr(lon, sizeof(lon), comma + 1);
            }
        }
    }

    char path[512];
    snprintf(path, sizeof(path),
             "/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,weather_code"
             "&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset,weather_code,"
             "precipitation_probability_max&timezone=auto&forecast_days=4&temperature_unit=%s",
             lat, lon, tempUnit);
    char url[640];
    joinUrl(url, sizeof(url), ep_.openMeteo, path);

    // abort: checked between sources in poll() and, via the Http implementation, inside each transfer.
    JsonDocument doc;
    HttpStatus s = http.getJson(url, nullptr, doc, 24576, 8000, abortFn_, abortCtx_);
    st.status = s;
    if (s != HTTP_OK) return false;

    JsonObjectConst cur = doc["current"];
    JsonArrayConst dmax = doc["daily"]["temperature_2m_max"];
    JsonArrayConst dmin = doc["daily"]["temperature_2m_min"];
    JsonArrayConst dcode = doc["daily"]["weather_code"];
    JsonArrayConst dsr = doc["daily"]["sunrise"];
    JsonArrayConst dss = doc["daily"]["sunset"];
    JsonArrayConst drain = doc["daily"]["precipitation_probability_max"];
    JsonArrayConst dtime = doc["daily"]["time"];

    if (!cur["temperature_2m"].isNull())
        w.temp = Value::number(cur["temperature_2m"].as<double>());
    if (!cur["weather_code"].isNull()) {
        int code = cur["weather_code"].as<int>();
        w.code = Value::number(code);
        w.condition = Value::text(weatherLabel(code));
    }
    if (!dmax.isNull() && dmax.size() > 0) w.max = Value::number(dmax[0].as<double>());
    if (!dmin.isNull() && dmin.size() > 0) w.min = Value::number(dmin[0].as<double>());
    if (!drain.isNull() && drain.size() > 0) w.rain = Value::number(drain[0].as<double>());
    if (!dsr.isNull() && dsr.size() > 0) {
        char hhmm[6];
        isoTimeTail(dsr[0].as<const char *>(), hhmm);
        if (hhmm[0]) w.sunrise = Value::text(hhmm);
    }
    if (!dss.isNull() && dss.size() > 0) {
        char hhmm[6];
        isoTimeTail(dss[0].as<const char *>(), hhmm);
        if (hhmm[0]) w.sunset = Value::text(hhmm);
    }
    // forecast[0..2] = daily days 1..3 (tomorrow onward)
    for (int k = 0; k < FORECAST_DAYS; k++) {
        int d = k + 1;
        if (!dmax.isNull() && dmax.size() > (size_t)d) w.fmax[k] = Value::number(dmax[d].as<double>());
        if (!dmin.isNull() && dmin.size() > (size_t)d) w.fmin[k] = Value::number(dmin[d].as<double>());
        if (!dcode.isNull() && dcode.size() > (size_t)d) {
            int code = dcode[d].as<int>();
            w.fcode[k] = Value::number(code);
            w.fcond[k] = Value::text(weatherLabel(code));
        }
        if (!drain.isNull() && drain.size() > (size_t)d)
            w.frain[k] = Value::number(drain[d].as<double>());
        if (!dtime.isNull() && dtime.size() > (size_t)d) {
            const char *wd = isoWeekday(dtime[d].as<const char *>());
            if (wd[0]) w.fday[k] = Value::text(wd);
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// steam

bool SourceSet::fetchSteam(int i, Http &http)
{
    SrcState &st = src_[i];
    Steam &s = steam_[i];
    bool any = false;
    HttpStatus firstStatus = HTTP_OK;
    bool statusSet = false;
    auto note = [&](HttpStatus hs) {
        if (!statusSet) {
            firstStatus = hs;
            statusSet = true;
        }
    };

    // players (keyless): appid override (s1) else Config.steamAppId
    {
        long appid = st.s1[0] ? strtol(st.s1, nullptr, 10) : (long)cfg_.steamAppId;
        char path[160];
        snprintf(path, sizeof(path),
                 "/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=%ld", appid);
        char url[320];
        joinUrl(url, sizeof(url), ep_.steamApi, path);
        JsonDocument doc;
        HttpStatus hs = http.getJson(url, nullptr, doc, 24576, 8000, abortFn_, abortCtx_);
        note(hs);
        if (hs == HTTP_OK && (doc["response"]["result"] | 0) == 1 &&
            !doc["response"]["player_count"].isNull()) {
            s.players = Value::number(doc["response"]["player_count"].as<double>());
            any = true;
        }
    }

    // online (keyless): store userdata.json, first series' last point
    {
        char url[320];
        joinUrl(url, sizeof(url), ep_.steamStore, "/stats/userdata.json");
        JsonDocument filter;
        filter[0]["data"] = true;
        JsonDocument doc;
        HttpStatus hs = http.getJson(url, &filter, doc, 200000, 8000, abortFn_, abortCtx_);
        note(hs);
        if (hs == HTTP_OK) {
            JsonArrayConst data = doc[0]["data"];
            if (!data.isNull() && data.size() > 0) {
                JsonArrayConst last = data[data.size() - 1];
                if (!last.isNull() && last.size() >= 2) {
                    s.online = Value::number(last[1].as<double>());
                    any = true;
                }
            }
        }
    }

    // personal fields only with a key AND a steamId. Never log the key.
    bool personal = cfg_.steamKey && cfg_.steamKey[0] && cfg_.steamId && cfg_.steamId[0];
    if (personal) {
        const char *key = cfg_.steamKey;
        const char *sid = cfg_.steamId;

        // persona
        {
            char path[320];
            snprintf(path, sizeof(path),
                     "/ISteamUser/GetPlayerSummaries/v2/?key=%s&steamids=%s", key, sid);
            char url[480];
            joinUrl(url, sizeof(url), ep_.steamApi, path);
            JsonDocument doc;
            HttpStatus hs = http.getJson(url, nullptr, doc, 24576, 8000, abortFn_, abortCtx_);
            note(hs);
            const char *name = doc["response"]["players"][0]["personaname"];
            if (hs == HTTP_OK && name) {
                s.persona = Value::text(name);
                any = true;
            }
        }
        // level
        {
            char path[320];
            snprintf(path, sizeof(path),
                     "/IPlayerService/GetSteamLevel/v1/?key=%s&steamid=%s", key, sid);
            char url[480];
            joinUrl(url, sizeof(url), ep_.steamApi, path);
            JsonDocument doc;
            HttpStatus hs = http.getJson(url, nullptr, doc, 24576, 8000, abortFn_, abortCtx_);
            note(hs);
            if (hs == HTTP_OK && !doc["response"]["player_level"].isNull()) {
                s.level = Value::number(doc["response"]["player_level"].as<double>());
                any = true;
            }
        }
        // owned games + total hours (filtered)
        {
            char path[360];
            snprintf(path, sizeof(path),
                     "/IPlayerService/GetOwnedGames/v1/?key=%s&steamid=%s&include_played_free_games=1",
                     key, sid);
            char url[520];
            joinUrl(url, sizeof(url), ep_.steamApi, path);
            JsonDocument filter;
            filter["response"]["game_count"] = true;
            filter["response"]["games"][0]["playtime_forever"] = true;
            JsonDocument doc;
            HttpStatus hs = http.getJson(url, &filter, doc, 200000, 8000, abortFn_, abortCtx_);
            note(hs);
            if (hs == HTTP_OK) {
                JsonObjectConst resp = doc["response"];
                if (!resp.isNull() && !resp["game_count"].isNull()) {
                    long count = resp["game_count"].as<long>();
                    long total = 0;
                    JsonArrayConst games = resp["games"];
                    if (!games.isNull())
                        for (JsonObjectConst g : games) total += (long)(g["playtime_forever"] | 0);
                    s.gamesOwned = Value::number(count);
                    s.hoursTotal = Value::number(total / 60.0);  // minutes -> hours
                    any = true;
                }
            }
        }
        // recently played (up to 3)
        {
            char path[320];
            snprintf(path, sizeof(path),
                     "/IPlayerService/GetRecentlyPlayedGames/v1/?key=%s&steamid=%s&count=3", key, sid);
            char url[480];
            joinUrl(url, sizeof(url), ep_.steamApi, path);
            JsonDocument doc;
            HttpStatus hs = http.getJson(url, nullptr, doc, 24576, 8000, abortFn_, abortCtx_);
            note(hs);
            if (hs == HTTP_OK) {
                for (int k = 0; k < RECENT_GAMES; k++) {
                    s.recentName[k] = Value{};
                    s.recentHours2w[k] = Value{};
                }
                JsonArrayConst games = doc["response"]["games"];
                int k = 0;
                if (!games.isNull())
                    for (JsonObjectConst g : games) {
                        if (k >= RECENT_GAMES) break;
                        const char *name = g["name"];
                        if (name) s.recentName[k] = Value::text(name);
                        s.recentHours2w[k] = Value::number((g["playtime_2weeks"] | 0) / 60.0);
                        k++;
                    }
                any = true;
            }
        }
        // friends online / in-game
        {
            char path[320];
            snprintf(path, sizeof(path),
                     "/ISteamUser/GetFriendList/v1/?key=%s&steamid=%s&relationship=friend", key, sid);
            char url[480];
            joinUrl(url, sizeof(url), ep_.steamApi, path);
            JsonDocument flFilter;
            flFilter["friendslist"]["friends"][0]["steamid"] = true;
            JsonDocument fl;
            HttpStatus hs = http.getJson(url, &flFilter, fl, 200000, 8000, abortFn_, abortCtx_);
            note(hs);
            JsonArrayConst friends = fl["friendslist"]["friends"];
            if (hs == HTTP_OK && !friends.isNull()) {
                // GetPlayerSummaries takes <= 100 ids; batches of 50 like the original firmware.
                // shortcut: at most 8 batches (400 friends); more friends are not counted.
                int online = 0, ingame = 0, batches = 0;
                bool allOk = true;
                char ids[1100];
                size_t used = 0;
                ids[0] = '\0';
                auto flush = [&]() {
                    if (!used) return;
                    char path2[1300];
                    snprintf(path2, sizeof(path2),
                             "/ISteamUser/GetPlayerSummaries/v2/?key=%s&steamids=%s", key, ids);
                    char url2[1500];
                    joinUrl(url2, sizeof(url2), ep_.steamApi, path2);
                    JsonDocument filter;
                    filter["response"]["players"][0]["personastate"] = true;
                    filter["response"]["players"][0]["gameid"] = true;
                    JsonDocument doc;
                    HttpStatus hs2 = http.getJson(url2, &filter, doc, 200000, 8000, abortFn_, abortCtx_);
                    note(hs2);
                    if (hs2 != HTTP_OK) {
                        allOk = false;
                    } else {
                        JsonArrayConst players = doc["response"]["players"];
                        if (!players.isNull())
                            for (JsonObjectConst p : players) {
                                if ((p["personastate"] | 0) != 0) online++;
                                if (!p["gameid"].isNull()) ingame++;
                            }
                    }
                    batches++;
                    used = 0;
                    ids[0] = '\0';
                };
                int inBatch = 0;
                for (JsonObjectConst f : friends) {
                    const char *fid = f["steamid"];
                    if (!fid || strlen(fid) > 20) continue;
                    if (batches >= 8 || !allOk || aborting()) break;
                    if (used) ids[used++] = ',';
                    memcpy(ids + used, fid, strlen(fid));
                    used += strlen(fid);
                    ids[used] = '\0';
                    if (++inBatch >= 50) { flush(); inBatch = 0; }
                }
                if (allOk && batches < 8 && !aborting()) flush();
                if (allOk && !aborting()) {
                    s.friendsOnline = Value::number(online);
                    s.friendsInGame = Value::number(ingame);
                    any = true;
                }
            }
        }
        // achievements for the most recent game (needs a recent appid)
        {
            long appid = 0;
            // re-fetch recent only to learn the top appid without storing it
            char path[320];
            snprintf(path, sizeof(path),
                     "/IPlayerService/GetRecentlyPlayedGames/v1/?key=%s&steamid=%s&count=1", key, sid);
            char url[480];
            joinUrl(url, sizeof(url), ep_.steamApi, path);
            JsonDocument rdoc;
            HttpStatus hs = http.getJson(url, nullptr, rdoc, 24576, 8000, abortFn_, abortCtx_);
            note(hs);
            if (hs == HTTP_OK) appid = rdoc["response"]["games"][0]["appid"] | 0L;
            if (appid != 0) {
                char apath[360];
                snprintf(apath, sizeof(apath),
                         "/ISteamUserStats/GetPlayerAchievements/v1/?key=%s&steamid=%s&appid=%ld&l=en",
                         key, sid, appid);
                char aurl[520];
                joinUrl(aurl, sizeof(aurl), ep_.steamApi, apath);
                JsonDocument adoc;
                HttpStatus ahs = http.getJson(aurl, nullptr, adoc, 200000, 8000, abortFn_, abortCtx_);
                note(ahs);
                JsonArrayConst achs = adoc["playerstats"]["achievements"];
                if (ahs == HTTP_OK && !achs.isNull()) {
                    long bestTime = -1;
                    int unlocked = 0, total = 0;
                    const char *bestName = nullptr;
                    for (JsonObjectConst a : achs) {
                        total++;
                        if ((a["achieved"] | 0) == 1) {
                            unlocked++;
                            long t = a["unlocktime"] | 0L;
                            if (t > bestTime) {
                                bestTime = t;
                                bestName = a["name"];
                            }
                        }
                    }
                    if (bestName) s.achName = Value::text(bestName);
                    s.achUnlocked = Value::number(unlocked);
                    s.achTotal = Value::number(total);
                    any = true;
                }
            }
        }
    }

    st.status = statusSet ? firstStatus : HTTP_OK;
    // success if any endpoint produced a value; if the keyless ones all failed
    // and there are no personal creds, report the first failure status.
    return any;
}

// ---------------------------------------------------------------------------
// http_json: GET url, resolve an RFC 6901 pointer to a scalar

// Append one unescaped RFC 6901 reference token to an ArduinoJson filter node,
// descending. Returns the leaf node to set true. Numeric tokens index arrays.
static void unescapeToken(const char *tok, size_t len, char *out, size_t cap)
{
    size_t o = 0;
    for (size_t k = 0; k < len && o + 1 < cap; k++) {
        if (tok[k] == '~' && k + 1 < len) {
            if (tok[k + 1] == '0') { out[o++] = '~'; k++; continue; }
            if (tok[k + 1] == '1') { out[o++] = '/'; k++; continue; }
        }
        out[o++] = tok[k];
    }
    out[o] = '\0';
}

bool SourceSet::fetchHttpJson(int i, Http &http)
{
    SrcState &st = src_[i];
    Value &out = httpJson_[i];

    const char *url = st.s1;
    bool isHttps = strncmp(url, "https://", 8) == 0;
    bool isHttp = strncmp(url, "http://", 7) == 0;
    if (!isHttps && !(isHttp && cfg_.allowHttp)) {
        st.status = HTTP_NET;  // https required unless allowHttp (test)
        return false;
    }

    // shortcut: no ArduinoJson filter for http_json. A per-element array filter
    // in ArduinoJson applies element 0's spec to the whole array, so a filter
    // built from an arbitrary RFC 6901 pointer (which may select a specific
    // index deep in the document) cannot be expressed reliably. maxBytes
    // (32 KB) already bounds memory here; revisit if that proves too small for
    // a real endpoint and the pointer is shallow enough to express as a filter.
    const char *ptr = st.s2;

    JsonDocument doc;
    HttpStatus s = http.getJson(url, nullptr, doc, 32768, 8000, abortFn_, abortCtx_);
    st.status = s;
    if (s != HTTP_OK) return false;

    // resolve the pointer against the parsed doc
    JsonVariantConst v = doc.as<JsonVariantConst>();
    if (ptr && ptr[0]) {
        const char *p = ptr;
        if (*p == '/') p++;
        while (*p) {
            const char *slash = strchr(p, '/');
            size_t len = slash ? (size_t)(slash - p) : strlen(p);
            char tok[160];
            unescapeToken(p, len, tok, sizeof(tok));
            bool numeric = tok[0] != '\0';
            for (const char *q = tok; *q; q++)
                if (*q < '0' || *q > '9') { numeric = false; break; }
            if (v.is<JsonArrayConst>() && numeric) {
                v = v[(size_t)atoi(tok)];
            } else if (v.is<JsonObjectConst>()) {
                v = v[tok];
            } else {
                v = JsonVariantConst{};
                break;
            }
            if (!slash) break;
            p = slash + 1;
        }
    }

    Value nv;
    if (v.isNull() || v.is<JsonObjectConst>() || v.is<JsonArrayConst>()) {
        nv = Value{};  // NONE for object/array/null/missing
    } else if (v.is<bool>()) {
        nv = Value::number(v.as<bool>() ? 1 : 0);
    } else if (v.is<double>() || v.is<long>() || v.is<int>() || v.is<unsigned>()) {
        nv = Value::number(v.as<double>());
    } else if (v.is<const char *>()) {
        nv = Value::text(v.as<const char *>());  // Value::text truncates to 63 bytes
    } else {
        nv = Value{};
    }
    out = nv;
    return true;
}

// ---------------------------------------------------------------------------
// get()

Value SourceSet::weatherValue(const Weather &w, uint8_t field, uint8_t idx)
{
    switch (field) {
        case 0: return w.temp;
        case 1: return w.code;
        case 2: return w.condition;
        case 3: return w.max;
        case 4: return w.min;
        case 5: return w.sunrise;
        case 6: return w.sunset;
        case 7: return w.rain;
        case 8: return idx < FORECAST_DAYS ? w.fday[idx] : Value{};
        case 9: return idx < FORECAST_DAYS ? w.fmax[idx] : Value{};
        case 10: return idx < FORECAST_DAYS ? w.fmin[idx] : Value{};
        case 11: return idx < FORECAST_DAYS ? w.fcode[idx] : Value{};
        case 12: return idx < FORECAST_DAYS ? w.fcond[idx] : Value{};
        case 13: return idx < FORECAST_DAYS ? w.frain[idx] : Value{};
        default: return Value{};
    }
}

Value SourceSet::steamValue(const Steam &s, uint8_t field, uint8_t idx)
{
    switch (field) {
        case 0: return s.players;
        case 1: return s.online;
        case 2: return s.persona;
        case 3: return s.level;
        case 4: return s.gamesOwned;
        case 5: return s.hoursTotal;
        case 6: return s.friendsOnline;
        case 7: return s.friendsInGame;
        case 8: return idx < RECENT_GAMES ? s.recentName[idx] : Value{};
        case 9: return idx < RECENT_GAMES ? s.recentHours2w[idx] : Value{};
        case 10: return s.achName;
        case 11: return s.achUnlocked;
        case 12: return s.achTotal;
        default: return Value{};
    }
}

Value SourceSet::get(uint8_t src, uint8_t field, uint8_t idx) const
{
    if (src >= nSrc_) return Value{};
    switch (src_[src].type) {
        case SRC_CLOCK:
            if (field == 0) return env_.synced() ? Value::time(env_.epoch()) : Value{};
            return Value{};
        case SRC_WEATHER: return weatherValue(weather_[src], field, idx);
        case SRC_STEAM: return steamValue(steam_[src], field, idx);
        case SRC_HTTP_JSON: return field == 0 ? httpJson_[src] : Value{};
        default: return Value{};
    }
}

}  // namespace idle

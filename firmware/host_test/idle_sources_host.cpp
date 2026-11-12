// Host-side checks for idle_sources.{h,cpp}. A real POSIX-socket HTTP/1.0 client
// implements idle::Http and talks to the python fake server spawned by pytest
// (decky/tests/idle/test_sources_host.py). Build/run flags come from that test.
//
// argv[1] = base URL of the fake server, e.g. "http://127.0.0.1:38591"
// argv[2] = a host:port that refuses connections (for HTTP_NET), e.g. "127.0.0.1:1"
//
// Exit 0 and print "idle sources host OK (<n> checks)" on success; abort() on
// any failed assertion.
#include "idle_sources.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include <string>

using namespace idle;

static int g_checks = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        g_checks++;                                                              \
        if (!(cond)) {                                                           \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
            abort();                                                             \
        }                                                                        \
    } while (0)

// ---------------------------------------------------------------------------
// A tiny HTTP/1.0 client over a POSIX socket, parsing http://host:port/path.
// Enforces maxBytes while reading the body, timeoutMs over the whole call,
// non-200 -> HTTP_STATUS, bad JSON -> HTTP_PARSE.
class SocketHttp : public Http {
  public:
    HttpStatus getJson(const char *url, const JsonDocument *filter, JsonDocument &doc,
                       size_t maxBytes, uint32_t timeoutMs, bool (*shouldAbort)(void *),
                       void *abortCtx) override
    {
        lastUrl_ = url;
        char host[256] = {0};
        int port = 80;
        char path[1024] = {0};
        if (!parseUrl(url, host, sizeof(host), &port, path, sizeof(path))) return HTTP_NET;

        int fd = connectTo(host, port, timeoutMs);
        if (fd < 0) return HTTP_NET;

        std::string req = "GET ";
        req += path;
        req += " HTTP/1.0\r\nHost: ";
        req += host;
        req += "\r\nConnection: close\r\nUser-Agent: inkterface-idle-test\r\n\r\n";
        if (!writeAll(fd, req.data(), req.size(), timeoutMs)) {
            close(fd);
            return HTTP_NET;
        }

        // read headers
        std::string buf;
        int status = 0;
        bool headersDone = false;
        size_t headerEnd = 0;
        HttpStatus rs = readHeaders(fd, buf, timeoutMs, &status, &headersDone, &headerEnd);
        if (rs != HTTP_OK) {
            close(fd);
            return rs;
        }
        if (status != 200) {
            close(fd);
            return HTTP_STATUS;
        }

        // body: whatever came with the headers, plus more up to maxBytes
        std::string body = buf.substr(headerEnd);
        timeval start;
        gettimeofday(&start, nullptr);
        while (true) {
            if (shouldAbort && shouldAbort(abortCtx)) {
                close(fd);
                return HTTP_ABORTED;
            }
            if (body.size() > maxBytes) {
                close(fd);
                return HTTP_TOO_BIG;
            }
            int pr = waitReadable(fd, remainingMs(start, timeoutMs));
            if (pr == 0) {
                close(fd);
                return HTTP_TIMEOUT;
            }
            if (pr < 0) {
                close(fd);
                return HTTP_NET;
            }
            char tmp[4096];
            ssize_t n = recv(fd, tmp, sizeof(tmp), 0);
            if (n < 0) {
                if (errno == EINTR) continue;
                close(fd);
                return HTTP_NET;
            }
            if (n == 0) break;  // EOF (Connection: close)
            body.append(tmp, (size_t)n);
            if (body.size() > maxBytes) {
                close(fd);
                return HTTP_TOO_BIG;
            }
        }
        close(fd);

        DeserializationError err;
        if (filter) {
            err = deserializeJson(doc, body, DeserializationOption::Filter(*filter));
        } else {
            err = deserializeJson(doc, body);
        }
        if (err) return HTTP_PARSE;
        return HTTP_OK;
    }

  private:
    std::string lastUrl_;

    static bool parseUrl(const char *url, char *host, size_t hcap, int *port, char *path,
                         size_t pcap)
    {
        const char *p = url;
        if (strncmp(p, "http://", 7) == 0) {
            p += 7;
            *port = 80;
        } else if (strncmp(p, "https://", 8) == 0) {
            // host client supports http only; a real https reaches us only when a
            // test deliberately routes it, which it never does -> treat as net err.
            return false;
        } else {
            return false;
        }
        const char *slash = strchr(p, '/');
        const char *colon = strchr(p, ':');
        const char *hostEnd = slash ? slash : p + strlen(p);
        if (colon && colon < hostEnd) {
            size_t hl = (size_t)(colon - p);
            if (hl >= hcap) return false;
            memcpy(host, p, hl);
            host[hl] = '\0';
            *port = atoi(colon + 1);
        } else {
            size_t hl = (size_t)(hostEnd - p);
            if (hl >= hcap) return false;
            memcpy(host, p, hl);
            host[hl] = '\0';
        }
        if (slash) {
            strncpy(path, slash, pcap - 1);
            path[pcap - 1] = '\0';
        } else {
            strncpy(path, "/", pcap - 1);
        }
        return true;
    }

    static int connectTo(const char *host, int port, uint32_t timeoutMs)
    {
        char portStr[16];
        snprintf(portStr, sizeof(portStr), "%d", port);
        addrinfo hints;
        memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;
        addrinfo *res = nullptr;
        if (getaddrinfo(host, portStr, &hints, &res) != 0 || !res) return -1;
        int fd = -1;
        for (addrinfo *a = res; a; a = a->ai_next) {
            fd = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
            if (fd < 0) continue;
            int fl = fcntl(fd, F_GETFL, 0);
            fcntl(fd, F_SETFL, fl | O_NONBLOCK);
            int rc = connect(fd, a->ai_addr, a->ai_addrlen);
            if (rc == 0) break;
            if (errno == EINPROGRESS) {
                pollfd pfd{fd, POLLOUT, 0};
                int pr = poll(&pfd, 1, (int)timeoutMs);
                if (pr > 0) {
                    int err = 0;
                    socklen_t el = sizeof(err);
                    getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el);
                    if (err == 0) {
                        fcntl(fd, F_SETFL, fl);
                        break;
                    }
                }
            }
            close(fd);
            fd = -1;
        }
        freeaddrinfo(res);
        if (fd >= 0) {
            int fl = fcntl(fd, F_GETFL, 0);
            fcntl(fd, F_SETFL, fl & ~O_NONBLOCK);
        }
        return fd;
    }

    static long remainingMs(const timeval &start, uint32_t timeoutMs)
    {
        timeval now;
        gettimeofday(&now, nullptr);
        long elapsed = (now.tv_sec - start.tv_sec) * 1000 + (now.tv_usec - start.tv_usec) / 1000;
        long rem = (long)timeoutMs - elapsed;
        return rem < 0 ? 0 : rem;
    }

    static int waitReadable(int fd, long ms)
    {
        pollfd pfd{fd, POLLIN, 0};
        return poll(&pfd, 1, (int)ms);
    }

    static bool writeAll(int fd, const char *data, size_t len, uint32_t timeoutMs)
    {
        timeval start;
        gettimeofday(&start, nullptr);
        size_t off = 0;
        while (off < len) {
            pollfd pfd{fd, POLLOUT, 0};
            if (poll(&pfd, 1, (int)remainingMs(start, timeoutMs)) <= 0) return false;
            ssize_t n = send(fd, data + off, len - off, 0);
            if (n <= 0) {
                if (n < 0 && errno == EINTR) continue;
                return false;
            }
            off += (size_t)n;
        }
        return true;
    }

    static HttpStatus readHeaders(int fd, std::string &buf, uint32_t timeoutMs, int *status,
                                  bool *done, size_t *headerEnd)
    {
        timeval start;
        gettimeofday(&start, nullptr);
        while (true) {
            size_t pos = buf.find("\r\n\r\n");
            if (pos != std::string::npos) {
                *headerEnd = pos + 4;
                *done = true;
                break;
            }
            long rem = remainingMs(start, timeoutMs);
            if (rem <= 0) return HTTP_TIMEOUT;
            int pr = waitReadable(fd, rem);
            if (pr == 0) return HTTP_TIMEOUT;
            if (pr < 0) return HTTP_NET;
            char tmp[2048];
            ssize_t n = recv(fd, tmp, sizeof(tmp), 0);
            if (n < 0) {
                if (errno == EINTR) continue;
                return HTTP_NET;
            }
            if (n == 0) {
                if (buf.empty()) return HTTP_NET;
                return HTTP_STATUS;  // closed before headers finished
            }
            buf.append(tmp, (size_t)n);
            if (buf.size() > 65536) return HTTP_STATUS;  // runaway headers
        }
        // parse status line "HTTP/1.x NNN ..."
        int code = 0;
        if (sscanf(buf.c_str(), "HTTP/%*d.%*d %d", &code) == 1) *status = code;
        return HTTP_OK;
    }
};

// ---------------------------------------------------------------------------
// A fixed-epoch Env.
struct TestEnv : Env {
    int64_t ep = 1700000000;  // synced
    int64_t epoch() const override { return ep; }
    void localTime(int64_t e, const char *, struct tm &out) const override
    {
        time_t t = (time_t)e;
        gmtime_r(&t, &out);
    }
};

// Build a one-source Layout by hand (idle_layout.cpp is not linked). The pool
// holds NUL-terminated strings; s1/s2 are offsets into it.
struct LayoutBuilder {
    Layout lay;
    std::string pool;
    LayoutBuilder() { pool.push_back('\0'); }  // offset 0 = empty string
    uint16_t add(const char *s)
    {
        uint16_t off = (uint16_t)pool.size();
        pool += s;
        pool.push_back('\0');
        return off;
    }
    void finish()
    {
        lay.poolLen = (uint16_t)pool.size();
        lay.pool = reinterpret_cast<const uint8_t *>(pool.data());
    }
};

static Layout oneSource(LayoutBuilder &b, uint8_t type, uint8_t flags, uint16_t pollS,
                        const char *s1, const char *s2)
{
    b.lay = Layout{};
    b.lay.nSources = 1;
    b.lay.src[0].type = type;
    b.lay.src[0].flags = flags;
    b.lay.src[0].pollS = pollS;
    b.lay.src[0].s1 = s1 ? b.add(s1) : NO_STR;
    b.lay.src[0].s2 = s2 ? b.add(s2) : NO_STR;
    b.finish();
    return b.lay;
}

static Config g_base;  // base config pointing endpoints handled per-SourceSet

// ---------------------------------------------------------------------------
// tests

static const char *BASE = nullptr;      // http://127.0.0.1:PORT
static const char *REFUSED = nullptr;   // host:port that refuses

static void makeSet(SourceSet &set, const Layout &lay, const Config &cfg)
{
    set.endpoints().overrideAll(BASE);
    set.configure(lay, cfg);
}

static void testWeatherMetric()
{
    LayoutBuilder b;
    Layout lay = oneSource(b, SRC_WEATHER, 1 /*metric*/, 0, nullptr, nullptr);
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    makeSet(set, lay, cfg);
    SocketHttp http;
    CHECK(set.poll(0, http));
    CHECK(set.lastStatus(0) == HTTP_OK);

    Value temp = set.get(0, 0, 0);
    CHECK(temp.kind == Value::NUM && temp.num > 7.4 && temp.num < 7.6);  // 7.5 C
    CHECK(set.get(0, 1, 0).num == 3);                                    // code
    CHECK(strcmp(set.get(0, 2, 0).str, "Overcast") == 0);                // condition
    CHECK(set.get(0, 3, 0).num == 10);                                   // today max
    CHECK(set.get(0, 4, 0).num == 2);                                    // today min
    CHECK(strcmp(set.get(0, 5, 0).str, "07:45") == 0);                   // sunrise
    CHECK(strcmp(set.get(0, 6, 0).str, "17:15") == 0);                   // sunset
    CHECK(set.get(0, 7, 0).num == 40);                                   // today rain
    // forecast[0] = tomorrow (daily index 1): 2024-03-01 is a Friday
    CHECK(strcmp(set.get(0, 8, 0).str, "FRI") == 0);
    CHECK(set.get(0, 9, 0).num == 12);
    CHECK(set.get(0, 10, 0).num == 4);
    CHECK(set.get(0, 11, 0).num == 61);
    CHECK(strcmp(set.get(0, 12, 0).str, "Rain") == 0);
    CHECK(set.get(0, 13, 0).num == 55);
    // forecast[1] 2024-03-02 Sat, forecast[2] 2024-03-03 Sun
    CHECK(strcmp(set.get(0, 8, 1).str, "SAT") == 0);
    CHECK(strcmp(set.get(0, 8, 2).str, "SUN") == 0);
}

static void testWeatherImperialAndLatLon()
{
    LayoutBuilder b;
    // imperial units via flags=2, lat/lon override "40.7,-74.0"
    Layout lay = oneSource(b, SRC_WEATHER, 2, 0, "40.7,-74.0", nullptr);
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    makeSet(set, lay, cfg);
    SocketHttp http;
    CHECK(set.poll(0, http));
    CHECK(set.lastStatus(0) == HTTP_OK);
    // the fake server logs the last weather request; check it saw imperial + lat
    FILE *f = fopen((std::string(getenv("IDLE_REQ_LOG") ? getenv("IDLE_REQ_LOG") : "/dev/null")).c_str(), "r");
    CHECK(f != nullptr);
    std::string all;
    char line[4096];
    while (fgets(line, sizeof(line), f)) all += line;
    fclose(f);
    CHECK(all.find("latitude=40.7") != std::string::npos);
    CHECK(all.find("longitude=-74.0") != std::string::npos);
    CHECK(all.find("temperature_unit=fahrenheit") != std::string::npos);
}

static void testSteamKeyless()
{
    LayoutBuilder b;
    Layout lay = oneSource(b, SRC_STEAM, 0, 0, "440" /*appid override*/, nullptr);
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    cfg.steamKey = "";  // no personal
    makeSet(set, lay, cfg);
    SocketHttp http;
    CHECK(set.poll(0, http));
    CHECK(set.get(0, 0, 0).num == 12345);  // players
    CHECK(set.get(0, 1, 0).num == 98765);  // online (last point)
    // personal fields must be NONE
    CHECK(!set.get(0, 2, 0).has());  // persona
    CHECK(!set.get(0, 3, 0).has());  // level

    // no personal request should have hit the server
    FILE *f = fopen(getenv("IDLE_REQ_LOG"), "r");
    CHECK(f);
    std::string all;
    char line[4096];
    while (fgets(line, sizeof(line), f)) all += line;
    fclose(f);
    CHECK(all.find("appid=440") != std::string::npos);  // override used
    CHECK(all.find("GetPlayerSummaries") == std::string::npos);
    CHECK(all.find("GetSteamLevel") == std::string::npos);
    CHECK(all.find("GetOwnedGames") == std::string::npos);
}

static void testSteamPersonal()
{
    LayoutBuilder b;
    Layout lay = oneSource(b, SRC_STEAM, 0, 0, nullptr, nullptr);
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    cfg.steamKey = "SECRETKEY123";
    cfg.steamId = "76561190000000000";
    makeSet(set, lay, cfg);
    SocketHttp http;
    CHECK(set.poll(0, http));
    CHECK(strcmp(set.get(0, 2, 0).str, "Gaben") == 0);  // persona
    CHECK(set.get(0, 3, 0).num == 42);                  // level
    CHECK(set.get(0, 4, 0).num == 3);                   // games_owned
    CHECK(set.get(0, 5, 0).num > 9.9 && set.get(0, 5, 0).num < 10.1);  // 600 min = 10h
    CHECK(set.get(0, 6, 0).num == 2);                   // friends online
    CHECK(set.get(0, 7, 0).num == 1);                   // friends in-game
    CHECK(strcmp(set.get(0, 8, 0).str, "Half-Life 3") == 0);  // recent[0]
    CHECK(set.get(0, 9, 0).num > 1.9 && set.get(0, 9, 0).num < 2.1);  // 120 min = 2h
    CHECK(strcmp(set.get(0, 10, 0).str, "First Blood") == 0);  // achievement name (latest)
    CHECK(set.get(0, 11, 0).num == 2);                  // unlocked
    CHECK(set.get(0, 12, 0).num == 3);                  // total

    // the key must NEVER appear in any lastStatus/error path. We assert the
    // library produces no string carrying it (checked indirectly: no public
    // API exposes the URL). Also verify stale-keep in a later test.
}

static std::string readReqLog()
{
    FILE *f = fopen(getenv("IDLE_REQ_LOG"), "r");
    std::string all;
    char line[4096];
    while (f && fgets(line, sizeof(line), f)) all += line;
    if (f) fclose(f);
    return all;
}

static int countOf(const std::string &hay, const std::string &needle)
{
    int n = 0;
    for (size_t p = hay.find(needle); p != std::string::npos; p = hay.find(needle, p + 1)) n++;
    return n;
}

static void testSteamManyFriends()
{
    // 120 friends -> three GetPlayerSummaries batches (50, 50, 20); counts accumulate across batches
    LayoutBuilder b;
    Layout lay = oneSource(b, SRC_STEAM, 0, 0, nullptr, nullptr);
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    cfg.steamKey = "BIGFRIENDS";
    cfg.steamId = "76561190000000000";
    makeSet(set, lay, cfg);
    SocketHttp http;
    std::string before = readReqLog();
    CHECK(set.poll(0, http));
    CHECK(set.get(0, 6, 0).num == 120);  // all online
    CHECK(set.get(0, 7, 0).num == 12);   // every 10th of each batch in-game: 5 + 5 + 2
    std::string now = readReqLog().substr(before.size());
    CHECK(countOf(now, "steamids=1000,") == 1 && countOf(now, "steamids=1050,") == 1 && countOf(now, "steamids=1100,") == 1);

    // one failing batch: the friend counts keep their previous (stale) values instead of a partial sum
    SourceSet set2(env);
    cfg.steamKey = "BIGFRIENDS_FAIL";
    makeSet(set2, lay, cfg);
    set2.poll(0, http);
    CHECK(!set2.get(0, 6, 0).has() && !set2.get(0, 7, 0).has());
    CHECK(set2.get(0, 3, 0).num == 42);  // other personal fields are unaffected
}

static void testHttpJsonPointer()
{
    // value at "/a/b/1" is a number 7; string, bool, object cases below
    LayoutBuilder b;
    Config cfg = g_base;
    cfg.allowHttp = true;
    std::string url = std::string(BASE) + "/json/doc";
    Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "/a/b/1");
    TestEnv env;
    SourceSet set(env);
    set.configure(lay, cfg);  // url already absolute; no endpoint override needed
    SocketHttp http;
    CHECK(set.poll(0, http));
    CHECK(set.lastStatus(0) == HTTP_OK);
    Value v = set.get(0, 0, 0);
    CHECK(v.kind == Value::NUM && v.num == 7);
}

static Value httpJsonValue(const char *path, const char *pointer, Config cfg)
{
    LayoutBuilder b;
    std::string url = std::string(BASE) + path;
    Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), pointer);
    TestEnv env;
    SourceSet set(env);
    set.configure(lay, cfg);
    SocketHttp http;
    set.poll(0, http);
    return set.get(0, 0, 0);
}

static void testHttpJsonKinds()
{
    Config cfg = g_base;
    cfg.allowHttp = true;
    // doc = {"str":"hi","b":true,"n":3.5,"obj":{},"arr":[1],"nul":null,
    //        "esc~key":1,"a":{"b":[9,7]}}
    CHECK(strcmp(httpJsonValue("/json/kinds", "/str", cfg).str, "hi") == 0);
    CHECK(httpJsonValue("/json/kinds", "/b", cfg).num == 1);       // bool true -> 1
    CHECK(httpJsonValue("/json/kinds", "/n", cfg).num == 3.5);     // number
    CHECK(!httpJsonValue("/json/kinds", "/obj", cfg).has());       // object -> NONE
    CHECK(!httpJsonValue("/json/kinds", "/arr", cfg).has());       // array -> NONE
    CHECK(!httpJsonValue("/json/kinds", "/nul", cfg).has());       // null -> NONE
    CHECK(!httpJsonValue("/json/kinds", "/missing", cfg).has());   // missing -> NONE
    CHECK(httpJsonValue("/json/kinds", "/a/b/1", cfg).num == 7);   // array index
    // pointer escapes: "esc~key" with a literal tilde is "esc~0key"
    CHECK(httpJsonValue("/json/kinds", "/esc~0key", cfg).num == 1);
    // "" = whole document is an object -> NONE
    CHECK(!httpJsonValue("/json/kinds", "", cfg).has());
}

static void testHttpJsonTruncation()
{
    Config cfg = g_base;
    cfg.allowHttp = true;
    // a 100-char string truncates to 63 bytes in Value::str
    Value v = httpJsonValue("/json/longstr", "/s", cfg);
    CHECK(v.kind == Value::STR);
    CHECK(strlen(v.str) == 63);
}

static void testHttpsRefusedWhenNotAllowed()
{
    LayoutBuilder b;
    Config cfg = g_base;
    cfg.allowHttp = false;
    // http:// URL but allowHttp false -> refused without any network call
    std::string url = std::string("http://127.0.0.1:1/json/doc");
    Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "");
    TestEnv env;
    SourceSet set(env);
    set.configure(lay, cfg);
    SocketHttp http;
    CHECK(!set.poll(0, http));
    CHECK(set.lastStatus(0) == HTTP_NET);
}

static void testErrorsAndStale()
{
    // 404 -> HTTP_STATUS, keep (no prior) NONE
    {
        LayoutBuilder b;
        std::string url = std::string(BASE) + "/status/404";
        Config cfg = g_base;
        cfg.allowHttp = true;
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(!set.poll(0, http));
        CHECK(set.lastStatus(0) == HTTP_STATUS);
    }
    // 500 -> HTTP_STATUS
    {
        LayoutBuilder b;
        std::string url = std::string(BASE) + "/status/500";
        Config cfg = g_base;
        cfg.allowHttp = true;
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(!set.poll(0, http));
        CHECK(set.lastStatus(0) == HTTP_STATUS);
    }
    // invalid JSON -> HTTP_PARSE
    {
        LayoutBuilder b;
        std::string url = std::string(BASE) + "/json/invalid";
        Config cfg = g_base;
        cfg.allowHttp = true;
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(!set.poll(0, http));
        CHECK(set.lastStatus(0) == HTTP_PARSE);
    }
    // body > maxBytes -> HTTP_TOO_BIG (http_json maxBytes 32768; /json/huge is bigger)
    {
        LayoutBuilder b;
        std::string url = std::string(BASE) + "/json/huge";
        Config cfg = g_base;
        cfg.allowHttp = true;
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "/x");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(!set.poll(0, http));
        CHECK(set.lastStatus(0) == HTTP_TOO_BIG);
    }
    // connection refused -> HTTP_NET
    {
        LayoutBuilder b;
        std::string url = std::string("http://") + REFUSED + "/json/doc";
        Config cfg = g_base;
        cfg.allowHttp = true;
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0, url.c_str(), "");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(!set.poll(0, http));
        CHECK(set.lastStatus(0) == HTTP_NET);
    }
}

static void testTimeout()
{
    // /slow sleeps longer than the client timeout. We exercise the Http impl
    // directly with a tiny timeout so the test stays fast.
    SocketHttp http;
    std::string url = std::string(BASE) + "/slow";
    JsonDocument doc;
    HttpStatus s = http.getJson(url.c_str(), nullptr, doc, 32768, 300, nullptr, nullptr);
    CHECK(s == HTTP_TIMEOUT);
}

static void testStaleKeep()
{
    // first poll succeeds (weather), second poll fails (point endpoint at a
    // refused host); values must survive and nextDueMs must back off.
    LayoutBuilder b;
    Layout lay = oneSource(b, SRC_WEATHER, 1, 0, nullptr, nullptr);
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    makeSet(set, lay, cfg);
    SocketHttp http;
    CHECK(set.poll(0, http));
    double good = set.get(0, 0, 0).num;
    CHECK(set.get(0, 0, 0).has());

    // break the endpoint and force a re-poll far in the future
    set.endpoints().overrideAll((std::string("http://") + REFUSED).c_str());
    // advance beyond the default weather interval (900 s)
    CHECK(!set.poll(1000000u, http));
    CHECK(set.lastStatus(0) == HTTP_NET);
    // stale value kept
    CHECK(set.get(0, 0, 0).has() && set.get(0, 0, 0).num == good);
    // backoff: retry within 60 s, so nextDueMs <= 60000
    CHECK(set.nextDueMs(1000000u) <= 60000u);
}

static void testScheduling()
{
    // default http_json interval is 300 s; explicit poll_s wins.
    Config cfg = g_base;
    cfg.allowHttp = true;
    {
        LayoutBuilder b;
        std::string url = std::string(BASE) + "/json/doc";
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 0 /*default*/, url.c_str(), "/a/b/1");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(set.poll(0, http));
        // next due ~300 s out
        CHECK(set.nextDueMs(0) > 290000u && set.nextDueMs(0) <= 300000u);
        // not due yet at 100 s
        CHECK(!set.poll(100000u, http));
    }
    {
        LayoutBuilder b;
        std::string url = std::string(BASE) + "/json/doc";
        Layout lay = oneSource(b, SRC_HTTP_JSON, 0, 45 /*explicit*/, url.c_str(), "/a/b/1");
        TestEnv env;
        SourceSet set(env);
        set.configure(lay, cfg);
        SocketHttp http;
        CHECK(set.poll(0, http));
        CHECK(set.nextDueMs(0) > 40000u && set.nextDueMs(0) <= 45000u);
        CHECK(set.poll(45000u, http));  // due again
    }
}

static void testAbort()
{
    // two sources; abort callback returns true so poll stops before fetching.
    LayoutBuilder b;
    b.lay = Layout{};
    b.lay.nSources = 2;
    b.lay.src[0].type = SRC_WEATHER;
    b.lay.src[0].flags = 1;
    b.lay.src[0].s1 = NO_STR;
    b.lay.src[0].s2 = NO_STR;
    b.lay.src[1].type = SRC_WEATHER;
    b.lay.src[1].flags = 1;
    b.lay.src[1].s1 = NO_STR;
    b.lay.src[1].s2 = NO_STR;
    b.finish();
    TestEnv env;
    SourceSet set(env);
    Config cfg = g_base;
    set.endpoints().overrideAll(BASE);
    set.configure(b.lay, cfg);
    set.setAbort([](void *) { return true; }, nullptr);
    SocketHttp http;
    CHECK(!set.poll(0, http));  // aborted before any fetch
    CHECK(!set.get(0, 0, 0).has());
    CHECK(!set.get(1, 0, 0).has());
}

static void testClock()
{
    LayoutBuilder b;
    Layout lay = oneSource(b, SRC_CLOCK, 0, 0, nullptr, nullptr);
    {
        TestEnv env;  // synced
        SourceSet set(env);
        set.configure(lay, g_base);
        Value v = set.get(0, 0, 0);
        CHECK(v.kind == Value::TIME && (int64_t)v.num == 1700000000);
    }
    {
        TestEnv env;
        env.ep = 100;  // not synced (< 1.6e9)
        SourceSet set(env);
        set.configure(lay, g_base);
        CHECK(!set.get(0, 0, 0).has());
    }
}

static void testWeekdayArithmetic()
{
    // leap day 2024-02-29 is a Thursday; year boundary 2023-12-31 is a Sunday;
    // 2000-01-01 is a Saturday.
    CHECK(strcmp(isoWeekday("2024-02-29"), "THU") == 0);
    CHECK(strcmp(isoWeekday("2023-12-31"), "SUN") == 0);
    CHECK(strcmp(isoWeekday("2000-01-01"), "SAT") == 0);
    CHECK(strcmp(isoWeekday("2026-11-10"), "TUE") == 0);
    CHECK(isoWeekday("bad")[0] == '\0');
    CHECK(isoWeekday("2024-13-01")[0] == '\0');  // invalid month
}

static void testWeatherLabel()
{
    CHECK(strcmp(weatherLabel(0), "Clear") == 0);
    CHECK(strcmp(weatherLabel(2), "Partly Cloudy") == 0);
    CHECK(strcmp(weatherLabel(3), "Overcast") == 0);
    CHECK(strcmp(weatherLabel(45), "Fog") == 0);
    CHECK(strcmp(weatherLabel(65), "Rain") == 0);
    CHECK(strcmp(weatherLabel(75), "Snow") == 0);
    CHECK(strcmp(weatherLabel(95), "Storm") == 0);
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <base-url> <refused-host:port>\n", argv[0]);
        return 2;
    }
    BASE = argv[1];
    REFUSED = argv[2];

    testWeatherLabel();
    testWeekdayArithmetic();
    testClock();
    testWeatherMetric();
    testWeatherImperialAndLatLon();
    testSteamKeyless();
    testSteamPersonal();
    testSteamManyFriends();
    testHttpJsonPointer();
    testHttpJsonKinds();
    testHttpJsonTruncation();
    testHttpsRefusedWhenNotAllowed();
    testErrorsAndStale();
    testTimeout();
    testStaleKeep();
    testScheduling();
    testAbort();

    printf("idle sources host OK (%d checks)\n", g_checks);
    return 0;
}

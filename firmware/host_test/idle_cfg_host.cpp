// Host test for idle_cfg.h: layering of defaults < legacy NVS JSON < store config, and the legacy key names.
// Build: g++ -std=c++17 -Wall -Wextra -I../src/ee05 -I<ArduinoJson>/src idle_cfg_host.cpp ../src/ee05/idle_{store,layout}.cpp ../src/ee05/usbproto.cpp
#include <assert.h>
#include <stdio.h>

#include "idle_cfg.h"

using namespace idle;

static bool legacy(RuntimeCfg &c, const char *json)
{
    JsonDocument d;
    if (deserializeJson(d, json)) return false;
    return c.applyLegacyJson(d);
}

int main()
{
    RuntimeCfg c;
    c.ssid = "default-net"; c.pass = "default-pw"; c.tz = "CET-1CEST,M3.5.0,M10.5.0/3";
    // the exact keys the BLE-era Qt app wrote (src/panel.cpp Panel::writeConfig)
    assert(legacy(c, R"({"wifiSsid":"home","wifiPass":"hunter2","lat":51.5,"lon":-0.125,"tz":"GMT0BST,M3.5.0/1,M10.5.0",
        "useMetric":false,"steamApiKey":"KEY","steamId64":"765","steamAppid":570,"use24h":true,"idleTimeoutMs":300000})"));
    assert(c.ssid == "home" && c.pass == "hunter2" && c.lat == "51.5000" && c.lon == "-0.1250");
    assert(c.tz == "GMT0BST,M3.5.0/1,M10.5.0" && c.imperial && c.steamKey == "KEY" && c.steamId == "765" && c.appId == 570);

    // partial blob: only named keys change; wrong types are ignored
    assert(legacy(c, R"({"wifiSsid":"other","wifiPass":5,"useMetric":"yes","steamAppid":-3,"lat":"x"})"));
    assert(c.ssid == "other" && c.pass == "hunter2" && c.imperial && c.appId == 570 && c.lat == "51.5000");
    // not an object / empty
    JsonDocument arr;
    deserializeJson(arr, "[1,2]");
    assert(!c.applyLegacyJson(arr));
    assert(legacy(c, "{}") && c.ssid == "other");

    // the store config wins over the legacy blob, field by field
    StoreConfig s;
    strcpy(s.wifiSsid, "usb-net"); s.have |= 1u << (CT_WIFI_SSID - 1);
    s.units = 0; s.have |= 1u << (CT_UNITS - 1);
    s.steamAppId = 730; s.have |= 1u << (CT_STEAM_APPID - 1);
    c.applyStore(s);
    assert(c.ssid == "usb-net" && c.pass == "hunter2" && !c.imperial && c.appId == 730 && c.steamKey == "KEY");
    StoreConfig empty;
    c.applyStore(empty);  // nothing present: nothing changes
    assert(c.ssid == "usb-net");

    Config sc = c.sourceConfig();
    assert(std::string(sc.tz) == "GMT0BST,M3.5.0/1,M10.5.0" && std::string(sc.lat) == "51.5000" && !sc.imperial &&
           std::string(sc.steamKey) == "KEY" && sc.steamAppId == 730 && !sc.allowHttp);
    puts("idle_cfg host tests OK");
    return 0;
}

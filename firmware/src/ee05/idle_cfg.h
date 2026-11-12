// Runtime configuration of the idle screens (Wi-Fi, zone, location, Steam), layered:
//   idle_config.h defaults  <  legacy NVS JSON blob (BLE-era app)  <  store CONFIG section (USB)
// Pure C++11 + ArduinoJson, host tested (firmware/host_test/idle_cfg_host.cpp).
// SECURITY: Wi-Fi password and Steam key are plain text (docs/idle-mode.md "Security and known limits").
#pragma once

#include <ArduinoJson.h>
#include <string>

#include "idle_data.h"
#include "idle_store.h"

namespace idle {

struct RuntimeCfg {
    std::string ssid, pass, tz = "UTC0", lat = "52.3676", lon = "4.9041", steamKey, steamId;
    bool imperial = false;
    uint32_t appId = 730;

    // The JSON object the BLE-era app wrote to NVS ("idlecfg"). Only named keys change anything;
    // wrong-typed keys are ignored. false = not an object.
    bool applyLegacyJson(JsonDocument &doc)
    {
        if (!doc.is<JsonObject>()) return false;
        JsonObject o = doc.as<JsonObject>();
        if (o["wifiSsid"].is<const char *>()) ssid = o["wifiSsid"].as<const char *>();
        if (o["wifiPass"].is<const char *>()) pass = o["wifiPass"].as<const char *>();
        if (o["tz"].is<const char *>()) tz = o["tz"].as<const char *>();
        if (o["lat"].is<float>()) lat = num(o["lat"].as<double>());
        if (o["lon"].is<float>()) lon = num(o["lon"].as<double>());
        if (o["useMetric"].is<bool>()) imperial = !o["useMetric"].as<bool>();
        if (o["steamApiKey"].is<const char *>()) steamKey = o["steamApiKey"].as<const char *>();
        if (o["steamId64"].is<const char *>()) steamId = o["steamId64"].as<const char *>();
        if (o["steamAppid"].is<long>() && o["steamAppid"].as<long>() > 0) appId = (uint32_t)o["steamAppid"].as<long>();
        return true;
    }

    void applyStore(const StoreConfig &c)
    {
        if (c.has(CT_WIFI_SSID)) ssid = c.wifiSsid;
        if (c.has(CT_WIFI_PASS)) pass = c.wifiPass;
        if (c.has(CT_TZ)) tz = c.tz;
        if (c.has(CT_LAT)) lat = c.lat;
        if (c.has(CT_LON)) lon = c.lon;
        if (c.has(CT_UNITS)) imperial = c.units == 1;
        if (c.has(CT_STEAM_KEY)) steamKey = c.steamKey;
        if (c.has(CT_STEAM_ID)) steamId = c.steamId;
        if (c.has(CT_STEAM_APPID)) appId = c.steamAppId;
    }

    // Views into this object: valid while it lives and is not modified.
    Config sourceConfig() const
    {
        Config c;
        c.tz = tz.c_str();
        c.lat = lat.c_str();
        c.lon = lon.c_str();
        c.imperial = imperial;
        c.steamKey = steamKey.c_str();
        c.steamId = steamId.c_str();
        c.steamAppId = appId;
        return c;
    }

  private:
    static std::string num(double v)
    {
        char b[32];
        snprintf(b, sizeof b, "%.4f", v);
        return b;
    }
};

}  // namespace idle

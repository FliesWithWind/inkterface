import { useState } from "react";
import { Settings } from "../api";
import { useAction } from "../hooks";
import { SteamSection } from "./SteamSection";
import { WeatherSection } from "./WeatherSection";
import { WifiSection } from "./WifiSection";

type Act = ReturnType<typeof useAction>;

/** Settings, "Connections": everything the panel needs to reach the outside world, one card each: its Wi-Fi, the Steam account, the weather location.
 *  A card starts open when its connection is not set and closed when it is. After that only the user opens and closes it (saving does not fold it away mid-setup). */
export function Connections({ s, act }: { s: Settings; act: Act }) {
  const [open, setOpen] = useState(() => ({ wifi: s.wifi_ssid === "", steam: s.steamid64 === "", weather: s.weather_lat === null }));
  const toggle = (k: keyof typeof open) => () => setOpen((o) => ({ ...o, [k]: !o[k] }));
  // a saved value changes what the fields below start from: remount them
  const sig = JSON.stringify([s.weather_lat, s.weather_lon, s.weather_place, s.steamid64, s.steam_api_key, s.wifi_ssid]);
  return (
    <>
      <WifiSection key={`w${sig}`} s={s} act={act} open={open.wifi} onToggle={toggle("wifi")} />
      <SteamSection key={`s${sig}`} s={s} act={act} open={open.steam} onToggle={toggle("steam")} />
      <WeatherSection key={`l${sig}`} s={s} act={act} open={open.weather} onToggle={toggle("weather")} />
    </>
  );
}

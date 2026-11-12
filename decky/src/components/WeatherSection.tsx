import { ButtonItem, DropdownItem, PanelSectionRow, TextField, ToggleField } from "@decky/ui";
import { useState } from "react";
import { FaCloudSun } from "react-icons/fa";
import { Place, Settings, locateByIp, searchPlaces, setSettings } from "../api";
import { languageOf, systemLocale, unitsForLocale } from "../locale";
import { useAction } from "../hooks";
import { ErrorLine, Note } from "./common";
import { CardState, ConnectionCard, PrimaryButton } from "./ConnectionCard";

type Act = ReturnType<typeof useAction>;

export const IP_PRIVACY = "Asks ipwho.is where this internet connection is. Only its IP address is sent to them, and only when you press the button. The answer is a town at best, not an address.";

const UNIT_LABEL = { metric: "metric", imperial: "imperial" } as const;

/** What the saved location is, in words: the place the user picked, raw coordinates, or off. */
export function weatherStatus(s: Pick<Settings, "weather_lat" | "weather_lon" | "weather_place" | "units">): CardState {
  if (s.weather_lat === null || s.weather_lon === null) return { tone: "muted", label: "Not set", detail: "Weather is off. Search for your town to switch it on." };
  const where = s.weather_place || `custom coordinates ${s.weather_lat}, ${s.weather_lon}`;
  return { tone: s.weather_place ? "ok" : "info", label: "Set", detail: `${where}, ${UNIT_LABEL[s.units]}` };
}

/** Settings, Connections, Weather: search a place by name and pick it (no coordinates to type); coordinates stay as an advanced option. */
export function WeatherSection({ s, act, open, onToggle }: { s: Settings; act: Act; open: boolean; onToggle: () => void }) {
  const [query, setQuery] = useState("");
  const [choices, setChoices] = useState<{ intro: string; places: Place[] } | null>(null);
  const [looking, setLooking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(s.weather_lat !== null && s.weather_place === "");
  const [lat, setLat] = useState(s.weather_lat === null ? "" : String(s.weather_lat));
  const [lon, setLon] = useState(s.weather_lon === null ? "" : String(s.weather_lon));
  const busy = act.busy || looking;

  const look = async (p: Promise<{ ok: boolean }>, show: (r: never) => { intro: string; places: Place[] }) => {
    setLooking(true);
    setError(null);
    setChoices(null);
    const r = await p;
    setLooking(false);
    if (!r.ok) setError((r as unknown as { error: string }).error);
    else setChoices(show(r as never));
  };
  const search = () => look(searchPlaces(query.trim(), languageOf(systemLocale())), (r: { results: Place[] }) => ({ intro: "Choose your place:", places: r.results }));
  const byIp = () => look(locateByIp(), (r: { name: string; lat: number; lon: number; country_code: string | null; provider: string }) => ({
    intro: `Approximate location from ${r.provider}. Choose it to use it:`,
    places: [{ label: `${r.name} (approximate)`, name: r.name, lat: r.lat, lon: r.lon, country_code: r.country_code }],
  }));
  const pick = async (p: Place) => {
    // the first place ever chosen also sets the units from the system language; later picks leave the user's choice alone
    const first = s.weather_lat === null;
    const r = await act.run(setSettings({ weather_lat: p.lat, weather_lon: p.lon, weather_place: p.name, ...(first ? { units: unitsForLocale(systemLocale()) } : {}) }));
    if (r.ok) {
      setChoices(null);
      setQuery("");
      setManual(false);
    }
  };
  const saveCoords = () => {
    const clear = lat.trim() === "" && lon.trim() === "";
    return act.run(setSettings({ weather_lat: clear ? null : Number(lat), weather_lon: clear ? null : Number(lon) }));
  };
  const coordsChanged = lat !== (s.weather_lat === null ? "" : String(s.weather_lat)) || lon !== (s.weather_lon === null ? "" : String(s.weather_lon));

  return (
    <ConnectionCard title="Weather" icon={<FaCloudSun />} state={weatherStatus(s)} open={open} onToggle={onToggle}>
      <Note>Used by screens that show weather, on the panel and while the machine is off.</Note>
      <PanelSectionRow><TextField label="Town or place name" value={query} onChange={(e) => setQuery(e.target.value)} /></PanelSectionRow>
      {s.weather_lat === null ? (
        <PrimaryButton disabled={busy || query.trim().length < 2} onClick={search}>Search</PrimaryButton>
      ) : (
        <ButtonItem layout="below" disabled={busy || query.trim().length < 2} onClick={search}>Search</ButtonItem>
      )}
      <ButtonItem layout="below" disabled={busy} onClick={byIp}>Use approximate location</ButtonItem>
      <Note>{IP_PRIVACY}</Note>
      <ErrorLine error={error} />
      {choices && (
        <>
          <Note>{choices.intro}</Note>
          {choices.places.map((p) => (
            <ButtonItem key={`${p.lat},${p.lon},${p.label}`} layout="below" disabled={act.busy} onClick={() => pick(p)}>{p.label}</ButtonItem>
          ))}
        </>
      )}
      {s.weather_lat !== null && (
        <ButtonItem layout="below" disabled={busy} onClick={() => act.run(setSettings({ weather_lat: null, weather_lon: null }))}>Remove location</ButtonItem>
      )}
      <DropdownItem
        label="Units"
        rgOptions={[{ data: "metric", label: "Metric (°C, km/h)" }, { data: "imperial", label: "Imperial (°F, mph)" }]}
        selectedOption={s.units}
        disabled={act.busy}
        onChange={(o: { data: "metric" | "imperial" }) => act.run(setSettings({ units: o.data }))}
      />
      <ToggleField label="Advanced: type coordinates" description="Latitude and longitude in decimal degrees, for a place the search does not know." checked={manual} onChange={setManual} />
      {manual && (
        <>
          <PanelSectionRow><TextField label="Latitude" mustBeNumeric value={lat} onChange={(e) => setLat(e.target.value)} /></PanelSectionRow>
          <PanelSectionRow><TextField label="Longitude" mustBeNumeric value={lon} onChange={(e) => setLon(e.target.value)} /></PanelSectionRow>
          <ButtonItem layout="below" disabled={act.busy || !coordsChanged} onClick={saveCoords}>Save location</ButtonItem>
        </>
      )}
    </ConnectionCard>
  );
}

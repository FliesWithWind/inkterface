import { ButtonItem, PanelSectionRow, TextField } from "@decky/ui";
import { useState } from "react";
import { FaWifi } from "react-icons/fa";
import { Settings, setSettings, wifiCurrent } from "../api";
import { useAction } from "../hooks";
import { ErrorLine, Note } from "./common";
import { SecretField } from "./SecretField";
import { CardState, ConnectionCard, PrimaryButton } from "./ConnectionCard";
import { Tone } from "./ui";

/** What the panel can do with the saved Wi-Fi: this is what standby screens that fetch weather or Steam data depend on (the Standby page shows it). */
export function wifiStatus(s: Pick<Settings, "wifi_ssid" | "wifi_pass">): { tone: Tone; title: string; detail: string } {
  if (!s.wifi_ssid) return { tone: "warn", title: "No Wi-Fi network set", detail: "Without one the panel cannot fetch weather or Steam data while the machine is off." };
  if (s.wifi_pass === "set") return { tone: "ok", title: `Wi-Fi: ${s.wifi_ssid}`, detail: "Password saved. The panel joins this network when the machine is off." };
  return { tone: "info", title: `Wi-Fi: ${s.wifi_ssid} (no password)`, detail: "Right for an open network. Add a password if yours has one." };
}

/** The same facts as the header of the Wi-Fi card in Settings. */
export function wifiCardState(s: Pick<Settings, "wifi_ssid" | "wifi_pass">): CardState {
  if (!s.wifi_ssid) return { tone: "muted", label: "Not set", detail: "Without a network the panel cannot fetch weather or Steam data while the machine is off." };
  if (s.wifi_pass === "set") return { tone: "ok", label: "Set", detail: `${s.wifi_ssid}, password saved` };
  return { tone: "info", label: "Set", detail: `${s.wifi_ssid}, no password (right for an open network)` };
}

export const WIFI_PASSWORD_NOTE = "The password cannot be copied: this machine keeps it private, so type it below. The panel only joins 2.4 GHz networks.";

const tooLong = (v: string) => (new TextEncoder().encode(v).length > 63 ? "A Wi-Fi password is at most 63 bytes." : null);

/** The panel's own Wi-Fi: network name, and the password as a write-only secret. */
export function WifiSection({ s, act, open, onToggle }: { s: Settings; act: ReturnType<typeof useAction>; open: boolean; onToggle: () => void }) {
  const [ssid, setSsid] = useState(s.wifi_ssid);
  const [reading, setReading] = useState(false);
  const [saved, setSaved] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fill = (name: string) => {
    setSsid(name);
    setSaved([]);
    setNote(`Filled in “${name}”. Save the network name, then type its password below.`);
  };
  const useMachine = async () => {
    setReading(true);
    setError(null);
    setNote(null);
    setSaved([]);
    const r = await wifiCurrent();
    setReading(false);
    if (!r.ok) setError(r.error);
    else if (r.ssid) fill(r.ssid);
    else {
      setSaved(r.saved);
      setNote("This machine is on a cable right now. It remembers these Wi-Fi networks, pick one:");
    }
  };
  const machine = <>{reading ? "Reading the network…" : "Use the Steam Machine's network"}</>;
  return (
    <ConnectionCard title="Panel Wi-Fi" icon={<FaWifi />} state={wifiCardState(s)} open={open} onToggle={onToggle}>
      <Note>The panel uses its own Wi-Fi to fetch weather and Steam data for standby screens while the machine is off.</Note>
      {s.wifi_ssid === "" ? (
        <PrimaryButton disabled={reading || act.busy} onClick={useMachine}>{machine}</PrimaryButton>
      ) : (
        <ButtonItem layout="below" disabled={reading || act.busy} onClick={useMachine}>{machine}</ButtonItem>
      )}
      {note && <Note>{note}</Note>}
      <ErrorLine error={error} />
      {saved.map((n) => <ButtonItem key={n} layout="below" onClick={() => fill(n)}>{n}</ButtonItem>)}
      <PanelSectionRow><TextField label="Network name (SSID)" value={ssid} onChange={(e) => setSsid(e.target.value)} /></PanelSectionRow>
      <ButtonItem layout="below" disabled={act.busy || ssid === s.wifi_ssid} onClick={() => act.run(setSettings({ wifi_ssid: ssid }))}>Save network name</ButtonItem>
      <Note>{WIFI_PASSWORD_NOTE}</Note>
      <SecretField
        label="Wi-Fi password"
        isSet={s.wifi_pass === "set"}
        hint="Stored on this device and sent to the panel. It is never shown again."
        invalid={tooLong}
        onSave={(v) => setSettings({ wifi_pass: v })}
        onRemove={() => setSettings({ wifi_pass: "" })}
      />
      <Note>An open network needs no password.</Note>
    </ConnectionCard>
  );
}

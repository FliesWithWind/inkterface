import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, TextField, ToggleField } from "@decky/ui";
import { useState } from "react";
import { getSettings, setSettings } from "../api";
import { Connections } from "../components/Connections";
import { ErrorLine, Note } from "../components/common";
import { Group, Styles } from "../components/ui";
import { useAction, useLoad } from "../hooks";
import { CATALOG_URL_NOTE, DEV_MODE_WARNING, STANDBY_TIMEOUT_DESC, STANDBY_TIMEOUT_LABEL } from "../names";

/** The saved catalog URL and its default (placeholder). Save / Reset to default; the backend has the final say. */
function CatalogUrl({ saved, def, busy, onSave }: { saved: string; def: string; busy: boolean; onSave: (url: string) => void }) {
  const [url, setUrl] = useState(saved);
  const trimmed = url.trim();
  const changed = trimmed !== saved;
  const hint = trimmed !== "" && !/^https:\/\//i.test(trimmed) ? "A catalog URL must start with https://." : null;
  return (
    <Group card title="Screen catalog">
      <PanelSection>
        <Note>{CATALOG_URL_NOTE}</Note>
        <PanelSectionRow>
          <TextField label="Catalog URL" value={url} onChange={(e) => setUrl(e.target.value)} />
        </PanelSectionRow>
        <Note>Default: {def}</Note>
        {hint && <ErrorLine error={hint} />}
        <ButtonItem layout="below" disabled={busy || !changed || hint !== null} onClick={() => onSave(trimmed)}>
          Save catalog URL
        </ButtonItem>
        <ButtonItem layout="below" disabled={busy || saved === ""} onClick={() => { setUrl(""); onSave(""); }}>
          Reset to default
        </ButtonItem>
      </PanelSection>
    </Group>
  );
}

const GHOST_MINUTES = [0, 5, 10, 15, 30, 60, 120];
const ghostLabel = (m: number) =>
  m === 0 ? "Never" : m === 10 ? "Every 10 min (default)" : m % 60 === 0 ? `Every ${m / 60 === 1 ? "hour" : `${m / 60} hours`}` : `Every ${m} min`;
// a value set by hand in settings.json that is not in the list still shows as the selection
const ghostOptions = (current: number) =>
  (GHOST_MINUTES.includes(current) ? GHOST_MINUTES : [...GHOST_MINUTES, current].sort((a, b) => a - b)).map((m) => ({ data: m, label: ghostLabel(m) }));

export function SettingsPage() {
  const settings = useLoad(getSettings, ["settings_changed"]);
  const act = useAction();
  if (!settings.data) {
    return (
      <PanelSection title="Settings">
        <Styles />
        {settings.error ? (
          <>
            <ErrorLine error={`Cannot load settings: ${settings.error}`} />
            <ButtonItem layout="below" onClick={settings.reload}>Try again</ButtonItem>
          </>
        ) : <Note>Loading…</Note>}
      </PanelSection>
    );
  }
  const s = settings.data;
  const set = (patch: Parameters<typeof setSettings>[0]) => act.run(setSettings(patch));
  return (
    <>
      <Styles />
      {s.errors.map((e) => <ErrorLine key={e} error={e} />)}
      <ErrorLine error={act.error} />
      <Group title="Connections" intro="How the panel reaches Wi-Fi, your Steam account and the weather. Secrets are saved on this device and never shown again.">
        <Connections s={s} act={act} />
      </Group>
      <Group card title="Behaviour" intro="When the panel changes screen, and how it keeps the picture clean.">
        <PanelSection>
          <SliderField label={STANDBY_TIMEOUT_LABEL} description={STANDBY_TIMEOUT_DESC}
            value={s.idle_timeout_s} min={15} max={3600} step={5} showValue onChange={(v) => set({ idle_timeout_s: v })} />
          <DropdownItem
            label="Full refresh to clear ghosting"
            description="One 4 s flash. It waits for a quiet moment, so a screen that keeps changing can take up to 1.5× longer."
            rgOptions={ghostOptions(s.ghost_clear_minutes)}
            selectedOption={s.ghost_clear_minutes}
            onChange={(o: { data: number }) => set({ ghost_clear_minutes: o.data })}
          />
          <ToggleField label="Switch to Now Playing when a game starts" checked={s.auto_now_playing}
            onChange={(v) => set({ auto_now_playing: v })} />
        </PanelSection>
      </Group>
      <Group card title="Developer" intro="For people who build or test their own screens.">
        <PanelSection>
          <ToggleField label="Developer mode" description={DEV_MODE_WARNING} checked={s.developer_mode}
            onChange={(v) => set({ developer_mode: v })} />
        </PanelSection>
      </Group>
      <CatalogUrl key={s.catalog_url} saved={s.catalog_url} def={s.catalog_url_default} busy={act.busy} onSave={(url) => set({ catalog_url: url })} />
      <Group card title="Data and uninstall" apart>
        <PanelSection>
          <ToggleField label="Delete my settings and packages when the plugin is uninstalled"
            description="Includes the saved Wi-Fi password and Steam API key. Off keeps them for a reinstall."
            checked={s.purge_on_uninstall} onChange={(v) => set({ purge_on_uninstall: v })} />
        </PanelSection>
      </Group>
    </>
  );
}

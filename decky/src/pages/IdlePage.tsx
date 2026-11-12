import { ButtonItem, DropdownItem, Navigation, PanelSection, ToggleField } from "@decky/ui";
import { useState } from "react";
import { IdleMode, IdleStatus, ScreenInfo, Status, getSettings, getStatus, listScreens, pushIdleLayouts, setSettings } from "../api";
import { AvailableList, OrderedList, Slots, StatusCard } from "../components/ScreenList";
import { wifiStatus } from "../components/WifiSection";
import { ErrorLine, Note } from "../components/common";
import { Badge, Group, ScreenThumb, StatusRow, Styles, Tone, noIdlePreviewReason } from "../components/ui";
import { useAction, useLoad } from "../hooks";
import {
  STANDBY_FW_CANNOT, STANDBY_NOT_SENT, STANDBY_OFFLINE, STANDBY_ON_PANEL, STANDBY_READY, STANDBY_SENDING, STANDBY_UNSUPPORTED,
} from "../names";
import { SETTINGS_ROUTE } from "../routes";

export const IDLE_PREVIEW_NOTE =
  "The pictures are drawn by the same renderer the panel uses. The clock is live. Weather and Steam values are sample data, because the panel fetches the real ones itself.";
export const MODE_BADGE: Record<IdleMode, string> = { mono: "Black and white", gray4: "4 grays" };

const ROTATE_CHOICES: [number, string][] = [
  [0, "Stay on the first screen"], [10, "10 seconds"], [30, "30 seconds"], [60, "1 minute"],
  [300, "5 minutes"], [900, "15 minutes"], [3600, "1 hour"],
];

const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`;
const CAPACITY = 49152;

/** One line about the upload: what is on the panel, what is happening, or why it failed. */
export function uploadLine(idle: IdleStatus, names: Record<string, string>, chosen: number): { text: string; error: boolean } | null {
  const list = (ids: string[]) => ids.map((i) => names[i] ?? i).join(", ");
  switch (idle.state) {
    case "error":
      return { text: `Not sent to the panel: ${idle.error ?? "unknown error"}`, error: true };
    case "uploading":
      return { text: STANDBY_SENDING, error: false };
    case "ok":
      return { text: `On the panel: ${list(idle.on_panel)} (${kb(idle.bytes)} of ${kb(idle.free ?? CAPACITY)})`, error: false };
    case "pending":
      return chosen > 0 ? { text: idle.on_panel.length ? `On the panel now: ${list(idle.on_panel)}. Changes are sent when it connects.` : "Waiting for the panel to connect…", error: false } : null;
    default:
      return null;
  }
}

const UPLOAD_TITLE: Partial<Record<IdleStatus["state"], [Tone, string]>> = {
  ok: ["ok", STANDBY_ON_PANEL],
  uploading: ["info", "Sending standby screens"],
  pending: ["info", "Waiting to send"],
};

/** Connection and firmware, as the standby screens care about them. */
function panelInfo(st: Status | null, unsupported: boolean, offline: boolean): { tone: Tone; title: string; detail: string } {
  if (!st) return { tone: "muted", title: "Panel status unknown", detail: "Controls stay locked until the panel's firmware is known." };
  if (offline) return { tone: "warn", title: "Panel not connected", detail: STANDBY_OFFLINE };
  if (unsupported) return { tone: "warn", title: STANDBY_FW_CANNOT, detail: STANDBY_UNSUPPORTED };
  return { tone: "ok", title: "Panel connected", detail: st.fw_version ? `Firmware ${st.fw_version}` : STANDBY_READY };
}

/** The sheet body of a standby screen: the 4-gray switch. Loads its own data (a modal is outside the page's tree). */
function StandbySettings({ id }: { id: string }) {
  const screens = useLoad(listScreens, ["screens_changed", "settings_changed"]);
  const settings = useLoad(getSettings, ["settings_changed"]);
  const status = useLoad(getStatus, ["status"]);
  const act = useAction();
  const x = screens.data?.find((y) => y.id === id);
  if (!x || !settings.data) return <Note>Loading…</Note>;
  const mode = settings.data.idle_modes[x.id] ?? x.idle_mode ?? x.idle_modes?.[0] ?? "mono";
  return (
    <>
      <ErrorLine error={act.error} />
      <ToggleField
        label="4-gray rendering"
        description="Smoother pictures, slower refresh. Off keeps black and white, which refreshes fast."
        checked={mode === "gray4"}
        disabled={!status.data || act.busy}
        onChange={(v) => act.run(setSettings({ idle_modes: { ...settings.data!.idle_modes, [x.id]: v ? "gray4" : "mono" } }))}
      />
    </>
  );
}

export function IdlePage() {
  const status = useLoad(getStatus, ["status"]);
  const screens = useLoad(listScreens, ["screens_changed", "settings_changed"]);
  const settings = useLoad(getSettings, ["settings_changed"]);
  const act = useAction();
  const [pushed, setPushed] = useState<string | null>(null);
  if (!screens.data || !settings.data) {
    const err = screens.error ?? settings.error;
    return (
      <PanelSection>
        <Styles />
        {err ? (
          <>
            <ErrorLine error={`Cannot load screens: ${err}`} />
            <Note>Check that the Inkterface plugin is running, then try again.</Note>
            <ButtonItem layout="below" onClick={() => { screens.reload(); settings.reload(); status.reload(); }}>Try again</ButtonItem>
          </>
        ) : <Note>Loading…</Note>}
      </PanelSection>
    );
  }
  const st = status.data;
  const s = settings.data;
  const unsupported = !!st && st.connected && !st.idle_supported;
  const offline = !!st && !st.connected;
  const locked = !st; // an unreadable status means an unknown firmware: change nothing

  const byId: Record<string, ScreenInfo> = Object.fromEntries(screens.data.filter((x) => x.has_idle).map((x) => [x.id, x]));
  const names = Object.fromEntries(Object.values(byId).map((x) => [x.id, x.name]));
  const chosenIds = s.idle_screens.filter((id) => byId[id]);
  const chosen = chosenIds.map((id) => byId[id]);
  const others = Object.values(byId).filter((x) => !chosenIds.includes(x.id));
  const set = (patch: Parameters<typeof setSettings>[0]) => act.run(setSettings(patch));
  const push = async () => {
    setPushed(null);
    const r = await act.run(pushIdleLayouts());
    if (r.ok) setPushed(STANDBY_SENDING);
  };
  const modeOf = (x: ScreenInfo): IdleMode => s.idle_modes[x.id] ?? x.idle_mode ?? x.idle_modes?.[0] ?? "mono";
  const upload = st ? uploadLine(st.idle, names, chosenIds.length) : null;
  const rotate = ROTATE_CHOICES.some(([v]) => v === s.idle_rotate_s) ? ROTATE_CHOICES : [...ROTATE_CHOICES, [s.idle_rotate_s, `${s.idle_rotate_s} seconds`] as [number, string]];
  const every = ROTATE_CHOICES.find(([v]) => v === s.idle_rotate_s)?.[1] ?? `${s.idle_rotate_s} seconds`;
  const wifi = wifiStatus(s);
  const used = st ? Math.min(100, Math.round((st.idle.bytes / (st.idle.free ?? CAPACITY)) * 100)) : 0;
  const [uTone, uTitle] = (st && UPLOAD_TITLE[st.idle.state]) || ["muted", ""];
  const onPanel = st && st.idle.state === "ok" ? st.idle.on_panel : [];
  const blocked = locked || act.busy;

  const slots: Slots = {
    // the picture follows the render mode the screen would be uploaded in
    thumb: (x) => <ScreenThumb id={x.id} name={x.name} refreshKey={`${modeOf(x)}`} reason={noIdlePreviewReason(x)} idle={{ mode: modeOf(x) }} />,
    badges: (x) => (
      <>
        <Badge>{MODE_BADGE[modeOf(x)]}</Badge>
        {onPanel.includes(x.id) && <Badge tone="ok">On panel</Badge>}
        {x.error && <Badge tone="error">Error</Badge>}
      </>
    ),
    sheet: (x) => ((x.idle_modes ?? []).length > 1 ? <StandbySettings id={x.id} /> : null),
    addBlocked: (x) => (x.needs_approval ? "Needs approval" : null),
  };
  const info = panelInfo(st, unsupported, offline);
  const first = chosen[0];

  return (
    <>
      <Styles />
      <ErrorLine error={status.error ? `Cannot read panel status: ${status.error}` : null} />
      <StatusCard
        label="Overview"
        tone={info.tone}
        title={info.title}
        sub={info.detail}
        art={first ? <ScreenThumb id={first.id} name={first.name} refreshKey={`${modeOf(first)}`} reason={noIdlePreviewReason(first)} idle={{ mode: modeOf(first) }} /> : undefined}
        facts={[
          { label: "Wi-Fi", value: s.wifi_ssid || "Not set" },
          ...(st && st.idle.state === "ok" ? [{ label: "Storage used", value: `${used}%` }] : []),
          ...(chosen.length > 1 ? [{ label: "Changes", value: s.idle_rotate_s === 0 ? "Never" : `every ${every.toLowerCase()}` }] : []),
        ]}
      >
        {upload?.error && <StatusRow tone="error" alert title={STANDBY_NOT_SENT}>{st?.idle.error ?? "unknown error"}</StatusRow>}
        {upload && !upload.error && (
          <StatusRow tone={uTone} title={uTitle}>
            {upload.text}
            {st?.idle.state === "ok" && <div className="ink-meter" role="progressbar" aria-label="Panel storage used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={used}><div style={{ width: `${used}%` }} /></div>}
          </StatusRow>
        )}
        <StatusRow tone={wifi.tone} title={wifi.title}>{wifi.detail}</StatusRow>
        <ButtonItem layout="below" onClick={() => Navigation.Navigate(SETTINGS_ROUTE)}>Open Wi-Fi settings</ButtonItem>
      </StatusCard>

      <Group title="In rotation" intro="Shown by the panel when the machine is off or the plugin is not running. The first one is shown first.">
        {Object.keys(byId).length === 0 && <Note>No installed screen has a standby layout.</Note>}
        {chosen.length > 1 && <Note>{`Order: ${chosen.map((x) => x.name).join(" → ")}. ${s.idle_rotate_s === 0 ? "It stays on the first one." : `It changes every ${every.toLowerCase()}.`}`}</Note>}
        {Object.keys(byId).length > 0 && (
          <OrderedList
            items={chosen}
            slots={slots}
            disabled={locked}
            empty="Nothing selected yet. Add a screen from the list below."
            removeLabel="Remove from standby screens"
            outOf="standby screens"
            onChange={(ids) => set({ idle_screens: ids })}
          />
        )}
        {chosen.length > 1 && (
          <DropdownItem
            label="Change screen every"
            rgOptions={rotate.map(([v, label]) => ({ data: v, label }))}
            selectedOption={s.idle_rotate_s}
            disabled={blocked}
            onChange={(o: { data: number }) => set({ idle_rotate_s: o.data })}
          />
        )}
        <ErrorLine error={act.error} />
        {pushed && st?.idle.state !== "error" && !act.error && <Note>{pushed}</Note>}
        <ButtonItem layout="below" disabled={locked || unsupported || offline || act.busy || chosen.length === 0} onClick={push}>
          Send standby screens to the panel
        </ButtonItem>
        {Object.keys(byId).length > 0 && <Note>{IDLE_PREVIEW_NOTE}</Note>}
      </Group>

      {others.length > 0 && (
        <Group title="Available">
          <AvailableList items={others} slots={slots} disabled={blocked} onAdd={(id) => set({ idle_screens: [...chosenIds, id] })} />
        </Group>
      )}
    </>
  );
}

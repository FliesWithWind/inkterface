import { ButtonItem, PanelSection, SliderField, ToggleField } from "@decky/ui";
import { useState } from "react";
import { FaPlay, FaThumbtack } from "react-icons/fa";
import { Profile, ScreenInfo, Settings, Status, getSettings, getStatus, listScreens, pinScreen, setRotation, setScreenEnabled, setScreenSettings, setSettings, unpinScreen } from "../api";
import { AvailableList, OrderedList, Slots, StatusCard } from "../components/ScreenList";
import { SettingForm } from "../components/SettingForm";
import { ErrorLine, Note, statusText } from "../components/common";
import { Badge, Group, InUse, PageLink, ScreenThumb, StatusRow, Styles, Tabs, noPreviewReason } from "../components/ui";
import { useAction, useLoad } from "../hooks";
import { LIVE_EMPTY, LIVE_STANDBY_NOTE, NAMES } from "../names";
import { ROUTE } from "../routes";

const EVENTS = ["screens_changed", "settings_changed"] as const;

/** "45 s", "2 min 30 s", "1 h". */
export function duration(s: number): string {
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

/** What a screen is doing right now, as badges. Text always says it; the colour only supports it. */
export function ScreenBadges({ s }: { s: ScreenInfo }) {
  return (
    <>
      {s.current && <Badge tone="info" icon={<FaPlay size={9} />}>On panel</Badge>}
      {s.pinned && <Badge tone="warn" icon={<FaThumbtack size={9} />}>Pinned</Badge>}
      {s.needs_approval && <Badge tone="warn">Needs approval</Badge>}
      {!s.enabled && !s.needs_approval && <Badge>Off</Badge>}
      {s.error && <Badge tone="error">Error</Badge>}
    </>
  );
}

/** The live panel: its picture, one sentence about what it shows, and a few facts that explain its behaviour. */
function PanelCard({ status, error, screens, settings, cycle }: { status: Status | null; error: string | null; screens: ScreenInfo[]; settings: Settings; cycle: number }) {
  if (!status) return error ? <StatusRow tone="error" title="Panel status unknown">{error}</StatusRow> : null;
  const cur = screens.find((s) => s.id === status.current_screen);
  const showing = status.connected && cur;
  const tone = ({ connected: "ok", connecting: "info", waiting: "warn", firmware_too_old: "error", updating: "info", stopped: "muted" } as const)[status.ui_state] ?? "muted";
  return (
    <StatusCard
      label="Panel"
      tone={tone}
      title={status.ui_state === "connected" ? "Panel connected" : statusText(status, null)}
      sub={showing ? `Showing ${cur.name}${status.pinned ? " (pinned)" : ""}` : status.connected && status.current_screen ? `Showing ${status.current_screen}` : undefined}
      art={showing ? <ScreenThumb id={cur.id} name={cur.name} refreshKey={JSON.stringify(cur.settings)} reason={noPreviewReason(cur)} /> : undefined}
      facts={[
        { label: "Firmware", value: status.fw_version ?? "unknown" },
        { label: "Wi-Fi", value: settings.wifi_ssid || "Not set" },
        { label: "Standby after", value: duration(settings.idle_timeout_s) },
        ...(settings.ingame_enabled ? [{ label: "Using", value: PROFILE_NAME[status.profile ?? "normal"] }] : []),
        ...(cycle > 0 ? [{ label: "Cycle", value: duration(cycle) }] : []),
      ]}
    />
  );
}

/** The sheet body of a live screen: Enabled plus the screen's own settings. A modal is outside the page's tree, so it loads its own data. */
function LiveSettings({ id }: { id: string }) {
  const screens = useLoad(listScreens, [...EVENTS]);
  const act = useAction();
  const s = screens.data?.find((x) => x.id === id);
  if (!s) return <Note>Loading…</Note>;
  return (
    <>
      <ErrorLine error={act.error} />
      <ToggleField
        label="Enabled"
        description="Allow this screen to be shown at all. Off keeps it out of the rotation and the pin."
        checked={s.enabled}
        disabled={act.busy || s.needs_approval}
        onChange={(v) => act.run(setScreenEnabled(s.id, v))}
      />
      {s.settings_schema.length > 0 && <SettingForm screenId={s.id} schema={s.settings_schema} values={s.settings} onSave={setScreenSettings} />}
    </>
  );
}

export const PROFILE_NAME: Record<Profile, string> = { normal: "Not in a game", ingame: "In a game" };

/** Which list the page edits: the plugin's tab strip (same as the Store), the list the panel uses right now marked "In use". */
const ProfileSwitch = ({ value, active, onChange }: { value: Profile; active: Profile; onChange: (p: Profile) => void }) => (
  <Tabs label="Which screens to edit" value={value} onChange={onChange}
    tabs={(["normal", "ingame"] as const).map((p) => ({ id: p, label: PROFILE_NAME[p], extra: active === p ? <InUse /> : undefined }))} />
);

export function ScreensPage() {
  const screens = useLoad(listScreens, [...EVENTS]);
  const settings = useLoad(getSettings, [...EVENTS]);
  const status = useLoad(getStatus, ["status"]);
  const act = useAction();
  const [picked, setPicked] = useState<Profile | null>(null); // the tab the user chose; until then the one in use
  const error = screens.error ?? settings.error;
  if (error && !screens.data) {
    return (
      <PanelSection>
        <Styles />
        <ErrorLine error={`Cannot load screens: ${error}`} />
        <Note>Check that the Inkterface plugin is running, then try again.</Note>
        <ButtonItem layout="below" onClick={() => { screens.reload(); settings.reload(); }}>Try again</ButtonItem>
      </PanelSection>
    );
  }
  if (!screens.data || !settings.data) return <PanelSection><Styles /><Note>Loading…</Note></PanelSection>;

  const st = status.data;
  const ingameOn = settings.data.ingame_enabled;
  const active: Profile = ingameOn ? st?.profile ?? "normal" : "normal";
  const prof: Profile = ingameOn ? picked ?? active : "normal";
  const ig = prof === "ingame" && settings.data.ingame;
  const { rotation, rotation_interval_s: interval, pinned } = ig || settings.data;
  // The normal profile keeps today's calls exactly; the in-game one adds the profile argument.
  const saveRotation = (ids: string[], iv: number | null) => (prof === "ingame" ? setRotation(ids, iv, "ingame") : setRotation(ids, iv));
  const pin = (id: string) => (prof === "ingame" ? pinScreen(id, "ingame") : pinScreen(id));
  const unpin = () => (prof === "ingame" ? unpinScreen("ingame") : unpinScreen());
  // `current` comes from the live status, which changes every frame; list_screens only refreshes on edits. It belongs to the list in use:
  // the other list is not on the panel, so none of its screens is "On panel" (a pinned Telemetry showed "On panel" in the In a game list too).
  // `pinned` is per profile: list_screens reports the normal one, so it is taken from the settings of the list being edited.
  const list = screens.data.map((s) => ({ ...s, pinned: prof === "ingame" ? s.id === pinned : s.pinned,
    ...(st ? { current: prof === active && st.connected && s.id === st.current_screen } : {}) }));
  const byId = new Map(list.map((s) => [s.id, s]));
  const inRotation = rotation.map((id) => byId.get(id)).filter((s): s is ScreenInfo => !!s && s.has_active);
  const inIds = inRotation.map((s) => s.id);
  const available = list.filter((s) => s.has_active && !inIds.includes(s.id));
  const idleOnly = list.filter((s) => !s.has_active).length;
  const cycle = inRotation.length * interval;
  const pinnedName = pinned ? byId.get(pinned)?.name ?? pinned : null;
  const usable = (s: ScreenInfo) => s.enabled && s.has_active && !s.needs_approval;

  const base: Slots = {
    thumb: (s) => <ScreenThumb id={s.id} name={s.name} refreshKey={JSON.stringify(s.settings)} reason={noPreviewReason(s)} />,
    badges: (s) => <ScreenBadges s={s} />,
    off: (s) => !s.enabled,
    addBlocked: (s) => (s.needs_approval ? "Needs approval" : null),
  };
  const inSlots: Slots = {
    ...base,
    sheet: (s) => <LiveSettings id={s.id} />,
    menu: (s) => [{ label: s.pinned ? "Unpin from panel" : "Pin to panel", disabled: act.busy || !usable(s), onSelect: () => { void act.run(s.pinned ? unpin() : pin(s.id)); } }],
  };
  const add = async (id: string) => {
    const s = byId.get(id);
    if (s && !s.enabled) {
      const r = await act.run(setScreenEnabled(id, true)); // the engine only rotates enabled screens
      if (!r.ok) return;
    }
    await act.run(saveRotation([...inIds, id], interval));
  };

  // With two lists the heading names the one being edited, so the tab strip, heading and intro read as one group. Off: exactly as before.
  const rotationGroup = (
    <Group title={ingameOn ? `${PROFILE_NAME[prof]} · in rotation` : "In rotation"} intro={inRotation.length === 0
      ? "The panel is not cycling through any screen."
      : `${prof === "ingame" ? "While a game is running: " : ingameOn ? "When no game is running: " : ""}${inRotation.length} ${inRotation.length === 1 ? "screen" : "screens"}, ${duration(interval)} each. A full cycle takes ${duration(cycle)}. The first one is shown first.`}>
      <ErrorLine error={act.error} />
      <ErrorLine error={error ? `Refresh failed: ${error}` : null} />
      {pinnedName && <Note>{`${pinnedName} is pinned, so the panel stays on it${prof === "ingame" ? " while a game is running" : ingameOn ? " when no game is running" : ""}. The rotation continues when you unpin it.`}</Note>}
      <SliderField
        label="Seconds per screen"
        value={interval}
        min={5}
        max={300}
        step={5}
        showValue
        disabled={act.busy || inRotation.length === 0}
        onChange={(v) => act.run(saveRotation(inIds, v))}
      />
      <OrderedList
        key={prof /* a fresh list per profile: an Undo note must not put a screen back into the other list */}
        items={inRotation}
        slots={inSlots}
        empty="Nothing in the rotation. Add a screen from the list below."
        removeLabel="Remove from rotation"
        outOf="rotation"
        minItems={1}
        minNote="the rotation needs at least one screen"
        onChange={(ids) => act.run(saveRotation(ids, interval))}
      />
    </Group>
  );

  return (
    <>
      <Styles />
      <PanelCard status={st} error={status.error} screens={list} settings={settings.data} cycle={cycle} />
      <p className="ink-sec-d" style={{ margin: "10px 0 22px" }}>
        Standby screens are drawn by the panel itself while the machine is off. Choose them on the <PageLink route={`${ROUTE}/idle`}>{NAMES.standby}</PageLink> page.
      </p>
      <ToggleField
        label="Different screens while a game is running"
        description={ingameOn
          ? "Each list has its own order, timing and pin. While a game runs, the panel uses the In a game list instead of switching to Now Playing."
          : "Off: the panel shows the same screens all the time."}
        checked={ingameOn}
        disabled={act.busy}
        onChange={(v) => { setPicked(null); void act.run(setSettings({ ingame_enabled: v })); }}
      />
      {ingameOn
        ? <div className="ink-prof" role="group" aria-label="Screen lists"><ProfileSwitch value={prof} active={active} onChange={setPicked} />{rotationGroup}</div>
        : rotationGroup}
      <Group title="Available">
        {list.length === 0 && <Note>{LIVE_EMPTY}</Note>}
        {list.length > 0 && available.length === 0 && <Note>Every screen is already in the rotation.</Note>}
        <AvailableList items={available} slots={base} disabled={act.busy} onAdd={add} />
        {idleOnly > 0 && <Note>{LIVE_STANDBY_NOTE(idleOnly)}</Note>}
      </Group>
    </>
  );
}

// Small visual pieces shared by the Live Screens, Standby Screens and Store pages: styles, badges, status rows, the thumbnail.
// Built on Steam's own components (PanelSection, Field, Focusable, DialogButton); this file only adds what they lack.
// Colours use Steam's --gpColor-* variables with a fallback, so they follow the theme when Steam defines them.
import { DialogButton, Focusable, Navigation } from "@decky/ui";
import { ReactNode } from "react";
import { IdleMode, previewIdleScreen } from "../api";
import { usePreview } from "../hooks";
import { STANDBY_DRAWN_BY_PANEL, STANDBY_NO_LAYOUT } from "../names";
import { CSS } from "./styles";

export type Tone = "info" | "ok" | "warn" | "error" | "muted";

export { CSS };

/** Renders the page styles once per page. */
export const Styles = () => <style>{CSS}</style>;

/** A top-level block of a page ("Connections", "Behaviour"). `apart` sets it off with a divider and space above, for what should not be tapped by accident. `card` puts the Steam fields in the same surface the connection cards use. */
export const Group = ({ title, intro, apart, card, children }: { title: string; intro?: ReactNode; apart?: boolean; card?: boolean; children: ReactNode }) => (
  <section className="ink-group" aria-label={title} data-apart={apart ? "true" : undefined}>
    <h2 className="ink-group-t">{title}</h2>
    {intro && <div className="ink-group-d">{intro}</div>}
    {card ? <div className="ink-gbody">{children}</div> : children}
  </section>
);

/** The plugin's one tab strip (Store: Browse / Installed; Live Screens: which list to edit). Equal-width segments in a dark track:
 *  idle = muted text, selected = raised grey + blue underline, focused = Steam's white fill. A nowrap row with flow-children="horizontal",
 *  so D-pad left / right walks the tabs and A selects; long labels wrap inside their segment, never sideways. `extra` sits after a label. */
export function Tabs<T extends string>({ label, tabs, value, onChange }: { label: string; tabs: { id: T; label: string; extra?: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <Focusable className="ink-tabs" flow-children="horizontal" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <DialogButton key={t.id} className="ink-tab" aria-selected={value === t.id} onClick={() => onChange(t.id)} {...({ role: "tab" } as object)}>
          <span>{t.label}</span>
          {t.extra}
        </DialogButton>
      ))}
    </Focusable>
  );
}

/** "In use": which list the panel is using right now (a dot plus the words, so it does not rely on colour). */
export const InUse = () => (
  <span className="ink-inuse">
    <span className="ink-inuse-dot" aria-hidden="true" />
    In use
  </span>
);

export const Badge = ({ tone = "muted", icon, children }: { tone?: Tone; icon?: ReactNode; children: ReactNode }) => (
  <span className={`ink-badge ink-badge-${tone}`}>
    {icon && <span aria-hidden="true" style={{ display: "inline-flex" }}>{icon}</span>}
    {children}
  </span>
);

/** One fact with a coloured dot (`alert` for a failure the user must notice). The tone never carries the meaning alone: the title says it too. */
export const StatusRow = ({ tone, title, alert, children }: { tone: Tone; title: string; alert?: boolean; children?: ReactNode }) => (
  <div className="ink-status" role={alert ? "alert" : "status"} aria-label={alert ? undefined : title}>
    <span className={`ink-dot ink-dot-${tone}`} aria-hidden="true" />
    <div>
      <div className="ink-status-t">{title}</div>
      {children && <div className="ink-status-d">{children}</div>}
    </div>
  </div>
);

/** Why a screen has no picture, or null when it can have one. Declarative screens preview even when off; code only when approved and on. */
export function noPreviewReason(s: { has_active: boolean; has_code: boolean; approved: boolean; enabled: boolean }): string | null {
  if (!s.has_active) return STANDBY_DRAWN_BY_PANEL;
  if (s.has_code && !s.approved) return "Needs your approval";
  if (s.has_code && !s.enabled) return "Switch on to preview";
  return null;
}

/** Why an idle layout has no picture, or null when it can have one. Idle layouts are data, but code packages still wait for approval first. */
export function noIdlePreviewReason(s: { has_idle: boolean; has_code: boolean; approved: boolean }): string | null {
  if (!s.has_idle) return STANDBY_NO_LAYOUT;
  if (s.has_code && !s.approved) return "Needs your approval";
  return null;
}

/** The 648x480 panel picture of a screen, plain, with a 1 px border so it does not bleed into the card. `reason` replaces the picture when none can be rendered.
 *  With `idle` it is the standby layout in that render mode (null = the user's choice) instead of the active look. */
export function ScreenThumb({ id, refreshKey, name, reason, width, detail, idle, render }: {
  id: string;
  refreshKey: string;
  name: string;
  reason: string | null;
  width?: number;
  detail?: boolean;
  idle?: { mode: IdleMode | null };
  /** Where the picture comes from (defaults to the active-look renderer). The Store passes catalogPreview. */
  render?: (id: string) => Promise<{ ok: true; data_url: string } | { ok: false; error: string }>;
}) {
  const preview = usePreview(reason ? null : id, refreshKey, render ?? (idle ? (i) => previewIdleScreen(i, idle.mode) : undefined));
  const loading = !reason && !preview.url && !preview.error;
  const note = reason ?? (preview.error ? (detail ? `No preview: ${preview.error}` : "No preview") : null);
  return (
    <div className="ink-thumb" style={width ? ({ "--ink-thumb-w": `${width}px` } as object) : undefined} title={preview.error ?? undefined}>
      <div className="ink-pic">
        {preview.url && <img src={preview.url} alt={`Preview of ${name}`} />}
        {loading && <div className="ink-skeleton" role="progressbar" aria-label={`Rendering ${name}`} />}
        {!preview.url && note && <div className="ink-thumb-note">{note}</div>}
      </div>
    </div>
  );
}

/** A controller button glyph as Steam draws them: a ring with the letter. The label is spoken, the letter is decoration. */
export const Glyph = ({ k }: { k: string }) => <span className="ink-gl" aria-hidden="true">{k}</span>;

/** One entry of the hint legend: glyph(s) + what the button does. `off` dims a move that cannot happen. */
export const Hint = ({ k, off, children }: { k: string | string[]; off?: boolean; children: ReactNode }) => (
  <span className="ink-hint" data-off={off ? "true" : undefined}>
    {(Array.isArray(k) ? k : [k]).map((x) => <Glyph key={x} k={x} />)}
    {children}
  </span>
);

/** An inline link to another page of the plugin. A real button, so the gamepad reaches it. */
export const PageLink = ({ route, children }: { route: string; children: ReactNode }) => (
  <DialogButton className="ink-link" onClick={() => Navigation.Navigate(route)}>{children}</DialogButton>
);

// The one set of list components behind both the Live Screens and the Standby Screens page, so they cannot drift apart:
//   StatusCard     panel picture + the one fact that matters + a few small facts
//   OrderedList    the screens that are IN (rotation / standby selection): rows with the order track, reorder mode, L1/R1, "..." menu
//   AvailableList  the screens that are not in yet, each with a blue primary Add
// A page supplies `Slots` for what is its own (pin, enabled, badges, the 4-gray switch, the preview source).
//
// Layout rule: buttons are wrapping flex rows INSIDE the row. They are never the children of a
// Steam `Field`: its right column never shrinks, is at least 250 px wide in a page, and pushed the arrows out of view.
// Nothing here uses a fixed width, a negative margin or `justify-content: flex-end`. The order track is a plain flex column.
//
// Ordering: chevrons (touch / mouse / D-pad), L1 / R1 on a focused row, X for Reorder mode
// (D-pad moves, A keeps, B cancels). The moved row keeps focus; rows slide 220 ms unless the user prefers reduced motion.
// Buttons: A only activates the focused control (the row itself maps nothing to A). Y removes the focused row
// from the list with an Undo note; the Menu button opens the "..." menu.
import { DialogButton, Focusable, GamepadButton, Menu, MenuItem, showContextMenu } from "@decky/ui";
import { ReactElement, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FaChevronDown, FaChevronUp, FaCheck, FaCode, FaCog, FaEllipsisH, FaPlus } from "react-icons/fa";
import { ScreenInfo } from "../api";
import { APPROVAL_WAITING } from "../names";
import { Note } from "./common";
import { openSheet } from "./Sheet";
import { Hint } from "./ui";

/** How long the "removed" note (and its Undo) stays, unless Undo has focus or another action comes first. */
export const UNDO_MS = 8000;

export const originLine = (s: ScreenInfo) =>
  [s.builtin ? "Built-in" : `by ${s.author}`, `v${s.version}`, s.has_code ? "runs code" : null].filter(Boolean).join(" · ");

/** `ids` with item `i` swapped with its neighbour; the input is returned unchanged when there is none. */
export function moved<T>(ids: T[], i: number, d: -1 | 1): T[] {
  const j = i + d;
  if (j < 0 || j >= ids.length) return ids;
  const next = [...ids];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/** An entry of a row's "..." menu. */
export interface MenuAction {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
}

/** What a page adds to the shared rows. Every slot is optional except the picture. */
export interface Slots {
  thumb: (s: ScreenInfo) => ReactNode;
  badges?: (s: ScreenInfo) => ReactNode;
  /** Extra lines under the description (warnings, upload state). */
  note?: (s: ScreenInfo) => ReactNode;
  /** Extra "..." menu entries before Move up / Move down / Remove (Live Screens: Pin). */
  menu?: (s: ScreenInfo) => MenuAction[];
  /** The settings sheet body, or null when there is nothing to configure (then the row has no Settings button).
   *  The sheet is a modal outside the page's React tree, so the body must load its own data. */
  sheet?: (s: ScreenInfo) => ReactElement | null;
  /** True when the screen is switched off: its picture is dimmed. */
  off?: (s: ScreenInfo) => boolean;
  /** Why this screen cannot be added right now, or null. */
  addBlocked?: (s: ScreenInfo) => string | null;
}

/** Panel picture + title + sentence + facts. `children` go under the sentence (extra rows, a link). */
export function StatusCard({ label, art, tone, title, sub, facts, children }: {
  label: string;
  art?: ReactNode;
  tone?: "ok" | "warn" | "info" | "error" | "muted";
  title: string;
  sub?: ReactNode;
  facts?: { label: string; value: ReactNode }[];
  children?: ReactNode;
}) {
  return (
    <section className="ink-stat" aria-label={label}>
      <div className="ink-stat-main">
        {art && <div className="ink-stat-art">{art}</div>}
        <div>
          <h2 className="ink-stat-t">
            {tone && <i className={`ink-dot ink-dot-${tone}`} style={{ margin: 0 }} aria-hidden="true" />}
            {title}
          </h2>
          {sub && <div className="ink-stat-s">{sub}</div>}
          {children && <div className="ink-stat-more">{children}</div>}
        </div>
      </div>
      {facts && facts.length > 0 && (
        <dl className="ink-facts">
          {facts.map((f) => (
            <div key={f.label}>
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function openSettings(s: ScreenInfo, slots: Slots) {
  const body = slots.sheet?.(s);
  if (!body) return;
  openSheet({ title: s.name, meta: originLine(s), art: slots.thumb(s), children: body });
}

/** The text part of a row: name, badges, description, origin, warnings. `hints` is the legend that shows while the row has focus. */
function RowBody({ s, slots, hints }: { s: ScreenInfo; slots: Slots; hints?: ReactNode }) {
  return (
    <div className="ink-row-body" data-nohint={hints ? undefined : "true"}>
      <div className="ink-title">
        {s.name}
        {slots.badges?.(s)}
      </div>
      {s.description && <div className="ink-desc">{s.description}</div>}
      <div className="ink-meta">
        {s.has_code && <FaCode size={10} aria-hidden="true" style={{ marginRight: 6 }} />}
        {originLine(s)}
      </div>
      {s.needs_approval && <div className="ink-warn">{APPROVAL_WAITING}</div>}
      {slots.note?.(s)}
      {s.error && <div className="ink-err" role="alert">{s.error}</div>}
      {hints && <div className="ink-hints" aria-hidden="true">{hints}</div>}
    </div>
  );
}

/** Up chevron, position, down chevron: one 44 px wide track. The first row has no up, the last no down (disabled, not focusable). */
function OrderTrack({ name, n, total, locked, onMove }: { name: string; n: number; total: number; locked: boolean; onMove: (d: -1 | 1) => void }) {
  return (
    <div className="ink-ord" role="group" aria-label={`Position ${n} of ${total}`}>
      <DialogButton className="ink-btn ink-btn-ico ink-ord-btn" data-ctl="up" disabled={locked || n === 1} aria-disabled={locked || n === 1} {...skip(locked || n === 1)} aria-label={`Move ${name} up to position ${n - 1}`} onClick={() => onMove(-1)}>
        <FaChevronUp size={16} aria-hidden="true" />
      </DialogButton>
      <span className="ink-ord-n" aria-hidden="true">{n}</span>
      <DialogButton className="ink-btn ink-btn-ico ink-ord-btn" data-ctl="down" disabled={locked || n === total} aria-disabled={locked || n === total} {...skip(locked || n === total)} aria-label={`Move ${name} down to position ${n + 1}`} onClick={() => onMove(1)}>
        <FaChevronDown size={16} aria-hidden="true" />
      </DialogButton>
    </div>
  );
}

/** A control that cannot be used. Steam's DialogButton does not set the `disabled` attribute, only a `Disabled` class (seen on the device). */
const isOff = (el: HTMLElement) => (el as HTMLButtonElement).disabled || el.getAttribute("aria-disabled") === "true" || el.classList.contains("Disabled");

/** Steam still focuses a DialogButton that is only `disabled`; `focusable: false` makes the D-pad step over it. (Not in the typed props.) */
const skip = (off: boolean) => ({ focusable: !off }) as object;

/** A control of a row that holds focus: `ctl` is its `data-ctl` ("up", "down", "settings", "more") or "row". */
interface Held { id: string; ctl: string }
/** Where focus goes when the control it was on is disabled after the move (the first row has no up chevron, the last no down). */
const FALLBACK: Record<string, string[]> = {
  up: ["up", "down", "settings", "more", "row"], down: ["down", "up", "settings", "more", "row"],
  settings: ["settings", "more", "row"], more: ["more", "settings", "row"], row: ["row", "settings", "more", "up", "down"],
};

const reducedMotion = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** What the status line under the list says: a move, a removal (with Undo), or why a removal was refused. */
interface Said { text: string; undo?: { s: ScreenInfo; index: number }; warn?: boolean }

/** The screens that are in, in order. `onChange` gets the new id order after a move or a removal (and may return the backend result: a failed one
 *  rolls the row back). `disabled` locks every control (the standby page while the firmware is unknown).
 *  Y / the "..." menu take a screen out of the list (`outOf` names the list: "rotation"); `minItems` is how many must stay (the rotation needs one). */
export function OrderedList({ items, slots, disabled = false, empty, removeLabel, outOf = "the list", minItems = 0, minNote = "", onChange }: {
  items: ScreenInfo[];
  slots: Slots;
  disabled?: boolean;
  empty: string;
  removeLabel: string;
  outOf?: string;
  minItems?: number;
  minNote?: string;
  onChange: (ids: string[]) => unknown;
}) {
  const key = items.map((x) => x.id).join("\n");
  const [local, setLocal] = useState<string[] | null>(null); // the order the user just chose, until the backend's own arrives
  const [lifted, setLifted] = useState<string | null>(null); // Reorder mode: the row that is lifted
  const [said, setSaid] = useState<Said | null>(null); // "<name> is now 2 of 4", "<name> removed from rotation · Undo"
  const [ghost, setGhost] = useState<ScreenInfo | null>(null); // a screen that Undo just put back, until the page's own list has it again
  const noteRef = useRef<HTMLDivElement>(null);
  const origin = useRef<string[] | null>(null); // the order when Reorder mode started: B restores it
  const list = useRef<HTMLDivElement>(null);
  const keep = useRef<{ focus: Held | null; tops: Map<string, number>; undo?: boolean } | null>(null);
  useEffect(() => { setLocal(null); setGhost(null); }, [key]);

  const byId = new Map([...(ghost ? [[ghost.id, ghost] as const] : []), ...items.map((x) => [x.id, x] as const)]);
  const ids = (local ?? items.map((x) => x.id)).filter((id) => byId.has(id));
  const shown = ids.map((id) => byId.get(id)!);
  const latest = useRef({ ids, onChange, byId });
  latest.current = { ids, onChange, byId };

  // The removed note times out (Undo is for a moment, not for good), but not under the user's thumb: while Undo has focus it waits.
  useEffect(() => {
    if (!said?.undo && !said?.warn) return;
    let t: ReturnType<typeof setTimeout>;
    const expire = () => {
      const n = noteRef.current;
      if (n && (n.querySelector(".gpfocus") || n.contains(document.activeElement))) t = setTimeout(expire, UNDO_MS);
      else setSaid(null);
    };
    t = setTimeout(expire, UNDO_MS);
    return () => clearTimeout(t);
  }, [said]);

  /** The control that has focus, as (row, control). Steam's gamepad focus is the element with the `gpfocus` class; it is NOT always
   *  document.activeElement (on the device the active element stayed on <body> or on another plugin's button). */
  const held = (): Held | null => {
    const root = list.current;
    const el = root?.querySelector<HTMLElement>(".gpfocus") ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const row = el?.closest<HTMLElement>("[data-row-id]");
    if (!root || !el || !row || !root.contains(row)) return null;
    return { id: row.dataset.rowId!, ctl: el.closest<HTMLElement>("[data-ctl]")?.dataset.ctl ?? "row" };
  };
  /** Put focus back on `h` (the same control of the same row; a disabled one falls back to a neighbour). Returns the element. */
  const refocus = (h: Held): HTMLElement | null => {
    const row = Array.from(list.current?.querySelectorAll<HTMLElement>("[data-row-id]") ?? []).find((r) => r.dataset.rowId === h.id);
    if (!row) return null;
    for (const ctl of FALLBACK[h.ctl] ?? [h.ctl, "row"]) {
      const el = ctl === "row" ? row : row.querySelector<HTMLElement>(`[data-ctl="${ctl}"]`);
      if (el && !isOff(el)) {
        if (!el.classList.contains("gpfocus") || document.activeElement !== el) el.focus();
        return el;
      }
    }
    return null;
  };
  const tops = () => {
    const m = new Map<string, number>();
    list.current?.querySelectorAll<HTMLElement>("[data-row-id]").forEach((el) => m.set(el.dataset.rowId!, el.getBoundingClientRect().top));
    return m;
  };
  const apply = (next: string[], onFail?: () => void) => {
    setLocal(next);
    const fail = () => { setLocal(null); setGhost(null); onFail?.(); };
    Promise.resolve(latest.current.onChange(next)).then((r) => (r as { ok?: boolean } | undefined)?.ok === false && fail(), fail);
  };
  const move = (id: string, d: -1 | 1) => {
    const cur = latest.current.ids;
    const i = cur.indexOf(id);
    const next = moved(cur, i, d);
    if (next === cur) return;
    keep.current = { focus: held(), tops: tops() };
    setSaid({ text: `${byId.get(id)?.name ?? id} is now ${i + d + 1} of ${cur.length}` });
    apply(next);
  };
  /** Take a screen out of the list. The row goes at once, focus moves to the next row (the previous one when it was last, the Undo button when
   *  none is left), and the note offers Undo. Refused (with the reason) when the list may not get shorter. */
  const remove = (id: string) => {
    const cur = latest.current.ids;
    const i = cur.indexOf(id);
    const s = latest.current.byId.get(id);
    if (i < 0 || !s || disabled) return;
    if (cur.length <= minItems) {
      setSaid({ text: `${s.name} cannot be removed: ${minNote || "the list needs at least one screen"}.`, warn: true });
      return;
    }
    const near = cur[i + 1] ?? cur[i - 1];
    const h = held();
    keep.current = { focus: near ? { id: near, ctl: h && h.id === id ? h.ctl : "more" } : null, tops: tops(), undo: !near };
    setSaid({ text: `${s.name} removed from ${outOf}`, undo: { s, index: i } });
    if (lifted === id) lift(null);
    apply(cur.filter((x) => x !== id), () => setSaid(null));
  };
  /** Put the removed screen back at the position it had (or the end when the list got shorter meanwhile). */
  const undo = ({ s, index }: NonNullable<Said["undo"]>) => {
    const cur = latest.current.ids;
    if (cur.includes(s.id)) return setSaid(null);
    const next = [...cur];
    next.splice(Math.min(index, cur.length), 0, s.id);
    keep.current = { focus: { id: s.id, ctl: "settings" }, tops: tops() };
    setGhost(s);
    setSaid(null);
    apply(next);
  };
  const lift = (id: string | null) => {
    origin.current = id ? latest.current.ids : null;
    if (id) setSaid(null);
    setLifted(id);
  };
  const cancelLift = () => {
    const back = origin.current;
    if (back && back.join("\n") !== latest.current.ids.join("\n")) {
      keep.current = { focus: held(), tops: tops() };
      apply(back);
    }
    setSaid(null);
    lift(null);
  };

  // After a re-order: the moved row keeps focus and the rows slide to their new slots. React moves the DOM nodes (insertBefore), which blurs
  // the focused one, and Steam then forgets its focus; the next D-pad press lands in the sidebar. So the same control is focused again
  // here (a disabled chevron falls back to its neighbour), once more a frame later if Steam dropped it, and scrolled into view.
  useLayoutEffect(() => {
    const k = keep.current;
    keep.current = null;
    if (!k) return;
    const h = k.focus;
    if (k.undo) noteRef.current?.querySelector<HTMLElement>("[data-ctl=undo]")?.focus();
    const target = h ? refocus(h) : null;
    const reveal = () => {
      const el = h ? refocus(h) ?? target : null;
      if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
    };
    if (h && typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => { if (!list.current?.querySelector(".gpfocus") && !list.current?.contains(document.activeElement)) refocus(h); });
    }
    if (reducedMotion()) { reveal(); return; }
    list.current?.querySelectorAll<HTMLElement>("[data-row-id]").forEach((el) => {
      const was = k.tops.get(el.dataset.rowId!);
      const dy = was === undefined ? 0 : was - el.getBoundingClientRect().top;
      if (dy !== 0 && typeof el.animate === "function") {
        el.style.zIndex = "5";
        const a = el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 220, easing: "cubic-bezier(.16,1,.3,1)" });
        a.onfinish = a.oncancel = () => { el.style.zIndex = ""; if (el.dataset.rowId === h?.id) reveal(); };
      }
    });
    reveal();
  }, [key, local && local.join("\n")]);

  const noteEl = said && (
    <div ref={noteRef} className="ink-moved" role="status" data-tone={said.warn ? "warn" : undefined}>
      <FaCheck size={12} aria-hidden="true" />
      <span>{said.text}</span>
      {said.undo && (
        <>
          {" "}
          <span aria-hidden="true">·</span>
          {" "}
          <DialogButton className="ink-btn ink-undo" data-ctl="undo" onClick={() => undo(said.undo!)}>Undo</DialogButton>
        </>
      )}
    </div>
  );
  if (shown.length === 0) return <>{noteEl}<Note>{empty}</Note></>;
  const n = shown.length;

  const menuFor = (s: ScreenInfo, i: number, parent: EventTarget | undefined) => {
    const acts: MenuAction[] = [
      ...(slots.menu?.(s) ?? []),
      { label: "Move up", disabled: i === 0, onSelect: () => move(s.id, -1) },
      { label: "Move down", disabled: i === n - 1, onSelect: () => move(s.id, 1) },
      { label: removeLabel, danger: true, onSelect: () => remove(s.id) },
    ];
    showContextMenu(
      <Menu label={`${s.name} actions`}>
        {acts.map((a) => (
          <MenuItem key={a.label} disabled={disabled || a.disabled} tone={a.danger ? "destructive" : undefined} onSelected={a.onSelect}>{a.label}</MenuItem>
        ))}
      </Menu>,
      parent,
    );
  };

  return (
    <div ref={list} onFocus={(e) => {
      // focus moved to another row: the lifted row is put down
      const row = (e.target as HTMLElement).closest<HTMLElement>("[data-row-id]");
      if (lifted && row?.dataset.rowId !== lifted) lift(null);
    }}>
      {noteEl}
      {shown.map((s, i) => {
        const up = disabled || i === 0;
        const down = disabled || i === n - 1;
        const isLifted = lifted === s.id;
        const hasSheet = !!slots.sheet?.(s);
        const hints = isLifted ? (
          <>
            <Hint k="D-pad">Move</Hint>
            <Hint k="A">Done</Hint>
            <Hint k="B">Cancel</Hint>
          </>
        ) : (
          <>
            <Hint k="L1" off={up}>Move up</Hint>
            <Hint k="R1" off={down}>Move down</Hint>
            <Hint k="X">Reorder</Hint>
            <Hint k="Y" off={n <= minItems}>Remove</Hint>
            <Hint k="Menu">More</Hint>
          </>
        );
        return (
          <Focusable
            key={s.id}
            className="ink-row"
            role="group"
            aria-label={s.name}
            data-row-id={s.id}
            data-off={slots.off?.(s) ? "true" : undefined}
            data-lift={isLifted ? "true" : undefined}
            flow-children="horizontal"
            // The row is a container, not a stop: D-pad up / down walks rows by their controls, left / right walks the controls of one row.
            // (As a stop it swallowed left / right, so the chevrons, Settings and "..." could not be reached with the pad at all.)
            // Entering a row from above / below keeps the column: Settings stays on Settings, a chevron on a chevron (2 = MAINTAIN_X).
            {...({ focusable: false, navEntryPreferPosition: 2 } as object)}
            focusWithinClassName="ink-row-f"
            // The row maps nothing to A: A only activates the focused control (Settings, "...", a chevron). Only a lifted row uses it, to put the row down.
            onOKActionDescription={isLifted ? "Done" : undefined}
            onSecondaryActionDescription={isLifted ? "Done" : "Reorder"}
            onOptionsActionDescription="Remove"
            onMenuActionDescription="More"
            onCancelActionDescription={isLifted ? "Cancel" : undefined}
            onSecondaryButton={() => !disabled && (isLifted ? lift(null) : lift(s.id))}
            onOptionsButton={(e: { stopPropagation?: () => void }) => { if (!disabled) { remove(s.id); e?.stopPropagation?.(); } }}
            onMenuButton={(e: { currentTarget: EventTarget | null }) => { if (!disabled) menuFor(s, i, e?.currentTarget ?? undefined); }}
            // L1 / R1 move the row while it (or anything in it) has focus. Only claimed while the button can do something.
            onButtonDown={(e: CustomEvent<{ button: number }>) => {
              if (disabled) return;
              const b = e.detail?.button;
              // Reorder mode: A keeps the row where it is, even with a button inside the row focused (that button must not also fire)
              if (isLifted && b === GamepadButton.OK) { lift(null); e.stopPropagation(); return; }
              if (b === GamepadButton.BUMPER_LEFT && !up) { move(s.id, -1); e.stopPropagation(); }
              if (b === GamepadButton.BUMPER_RIGHT && !down) { move(s.id, 1); e.stopPropagation(); }
            }}
            // Reorder mode only: D-pad moves the row instead of the focus, B puts it back. Outside it Steam keeps both.
            onGamepadDirection={isLifted ? (e: CustomEvent<{ button: number }>) => {
              const b = e.detail?.button;
              if (b === GamepadButton.DIR_UP || b === GamepadButton.DIR_DOWN) {
                if (b === GamepadButton.DIR_UP ? !up : !down) move(s.id, b === GamepadButton.DIR_UP ? -1 : 1);
                e.stopPropagation();
              }
            } : undefined}
            onCancel={isLifted ? cancelLift : undefined}
          >
            <OrderTrack name={s.name} n={i + 1} total={n} locked={disabled} onMove={(d) => move(s.id, d)} />
            <div className="ink-row-art">{slots.thumb(s)}</div>
            <RowBody s={s} slots={slots} hints={hints} />
            <div className="ink-row-act">
              {/* always there, so every row has the same buttons in the same places; disabled (not focusable) when the screen has nothing to configure */}
              <DialogButton className="ink-btn" data-ctl="settings" disabled={!hasSheet} aria-disabled={!hasSheet} {...skip(!hasSheet)} onClick={() => openSettings(s, slots)}>
                <FaCog aria-hidden="true" />
                Settings <span className="ink-sr">for {s.name}{hasSheet ? "" : " (this screen has no settings)"}</span>
              </DialogButton>
              <DialogButton
                className="ink-btn ink-btn-ico"
                data-ctl="more"
                aria-haspopup="menu"
                aria-label={`More for ${s.name}`}
                disabled={disabled}
                onClick={(e: { currentTarget: EventTarget | null }) => menuFor(s, i, e?.currentTarget ?? undefined)}
              >
                <FaEllipsisH size={14} aria-hidden="true" />
              </DialogButton>
            </div>
          </Focusable>
        );
      })}
    </div>
  );
}

/** The screens that are not in yet, each with a blue primary Add. A screen that cannot be added says why instead of failing. */
export function AvailableList({ items, slots, disabled = false, empty, onAdd }: {
  items: ScreenInfo[];
  slots: Slots;
  disabled?: boolean;
  empty?: string;
  onAdd: (id: string) => void;
}) {
  if (items.length === 0) return empty ? <Note>{empty}</Note> : null;
  return (
    <>
      {items.map((s) => {
        const blocked = slots.addBlocked?.(s) ?? null;
        return (
          <Focusable
            key={s.id}
            className="ink-row"
            role="group"
            aria-label={s.name}
            data-off={slots.off?.(s) ? "true" : undefined}
            flow-children="horizontal"
            onOKActionDescription={blocked ? undefined : "Add"}
            onActivate={() => !disabled && !blocked && onAdd(s.id)}
          >
            <div className="ink-row-art">{slots.thumb(s)}</div>
            <RowBody s={s} slots={slots} />
            <div className="ink-row-act">
              <DialogButton className="ink-btn ink-btn-primary" disabled={disabled || blocked !== null} onClick={() => onAdd(s.id)}>
                <FaPlus size={12} aria-hidden="true" />
                Add <span className="ink-sr">{s.name}</span>
              </DialogButton>
            </div>
          </Focusable>
        );
      })}
    </>
  );
}

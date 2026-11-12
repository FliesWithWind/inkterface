// Stand-in for @decky/ui: plain HTML elements with accessible names, so tests read like a user would.
import { ReactElement, ReactNode, cloneElement, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

export const staticClasses = { Title: "title" };

export const PanelSection = ({ title, children }: { title?: string; children?: ReactNode }) => (
  <section aria-label={title}>
    {title && <h3>{title}</h3>}
    {children}
  </section>
);
export const PanelSectionRow = ({ children }: { children?: ReactNode }) => <div>{children}</div>;

export const ButtonItem = ({ children, onClick, disabled }: { children?: ReactNode; onClick?: (e: unknown) => void; disabled?: boolean; layout?: string }) => (
  <button disabled={disabled} onClick={(e) => onClick?.(e)}>{children}</button>
);

/** Same values as the real enum, so pages import GamepadButton from "@decky/ui" and tests can press them. */
export enum GamepadButton {
  INVALID = 0, OK = 1, CANCEL = 2, SECONDARY = 3, OPTIONS = 4, BUMPER_LEFT = 5, BUMPER_RIGHT = 6, TRIGGER_LEFT = 7, TRIGGER_RIGHT = 8,
  DIR_UP = 9, DIR_DOWN = 10, DIR_LEFT = 11, DIR_RIGHT = 12, SELECT = 13, START = 14,
}

/** Keyboard stand-ins for the controller, used by the Focusable below and by tests (fireEvent.keyDown(el, { key })). */
export const PAD: Record<string, GamepadButton> = {
  PageUp: GamepadButton.BUMPER_LEFT, PageDown: GamepadButton.BUMPER_RIGHT, x: GamepadButton.SECONDARY, y: GamepadButton.OPTIONS, m: GamepadButton.START,
  ArrowUp: GamepadButton.DIR_UP, ArrowDown: GamepadButton.DIR_DOWN, Enter: GamepadButton.OK, Escape: GamepadButton.CANCEL,
};

/** A focusable div. Gamepad: Enter = A, Escape = B, x = X, y = Y, PageUp/PageDown = L1/R1, arrows = D-pad. A handler runs for the focused
 *  element and every ancestor (events bubble, like Steam's), until one calls stopPropagation. */
export const Focusable = ({ children, onActivate, onCancel, onCancelButton, focusClassName, focusWithinClassName, noFocusRing, "flow-children": flow,
  onOKActionDescription, onCancelActionDescription, onSecondaryActionDescription, onOptionsActionDescription, onMenuActionDescription, onSecondaryButton, onOptionsButton, onMenuButton, preferredFocus,
  onButtonDown, onGamepadDirection, onClick, onKeyDown, focusable, navEntryPreferPosition, ...rest }: Record<string, any>) => {
  void [focusClassName, focusWithinClassName, noFocusRing, preferredFocus, navEntryPreferPosition];
  return (
    <div tabIndex={0} data-focusable={focusable === undefined ? undefined : String(focusable)} data-flow={flow} data-ok={onOKActionDescription} data-cancel={onCancelActionDescription} data-secondary={onSecondaryActionDescription}
      data-options={onOptionsActionDescription} data-menu={onMenuActionDescription} {...rest}
      onClick={(e) => { onClick?.(e); if (e.target === e.currentTarget) onActivate?.(e); }}
      onKeyDown={(e) => {
        onKeyDown?.(e);
        const button = PAD[e.key];
        if (button === undefined) return;
        const evt = { detail: { button, source: 0 }, stopPropagation: () => e.stopPropagation(), preventDefault: () => e.preventDefault() };
        onButtonDown?.(evt);
        if (e.isPropagationStopped()) return;
        if (button === GamepadButton.DIR_UP || button === GamepadButton.DIR_DOWN) onGamepadDirection?.(evt);
        // Steam: A reaches an ancestor's onActivate unless the focused control handled it itself (a live button does; a disabled one does not)
        const handledByChild = e.target !== e.currentTarget && e.target instanceof HTMLButtonElement && !e.target.disabled;
        if (button === GamepadButton.OK && !handledByChild) onActivate?.(e);
        if (button === GamepadButton.CANCEL) { onCancel?.(evt); onCancelButton?.(evt); }
        if (button === GamepadButton.SECONDARY) onSecondaryButton?.(evt);
        if (button === GamepadButton.OPTIONS) onOptionsButton?.(evt);
        if (button === GamepadButton.START) onMenuButton?.(evt);
      }}>
      {children}
    </div>
  );
};

export const DialogButton = ({ children, onClick, disabled, className, style, focusable, ...rest }: Record<string, any>) => (
  <button disabled={disabled} className={className} style={style} data-focusable={focusable === undefined ? undefined : String(focusable)} {...rest} onClick={(e) => onClick?.(e)}>{children}</button>
);

export const ToggleField = ({ label, checked, onChange, disabled, description }: { label?: ReactNode; checked: boolean; onChange?: (v: boolean) => void; disabled?: boolean; description?: ReactNode }) => (
  <label>
    <input type="checkbox" role="switch" aria-label={String(label)} checked={checked} disabled={disabled} onChange={(e) => onChange?.(e.target.checked)} />
    {label}
    {description && <small>{description}</small>}
  </label>
);

export const SliderField = ({ label, value, min, max, step, onChange, disabled }: { label?: ReactNode; value: number; min?: number; max?: number; step?: number; onChange?: (v: number) => void; disabled?: boolean; showValue?: boolean; description?: ReactNode }) => (
  <label>
    {label}
    <input type="range" aria-label={String(label)} value={value} min={min} max={max} step={step} disabled={disabled} onChange={(e) => onChange?.(Number(e.target.value))} />
  </label>
);

export const DropdownItem = ({ label, rgOptions, selectedOption, onChange, disabled }: { label?: ReactNode; rgOptions: { data: any; label: ReactNode }[]; selectedOption: any; onChange?: (o: any) => void; disabled?: boolean }) => (
  <label>
    {label}
    <select aria-label={String(label)} value={String(selectedOption)} disabled={disabled} onChange={(e) => onChange?.(rgOptions.find((o) => String(o.data) === e.target.value))}>
      {rgOptions.map((o) => (
        <option key={String(o.data)} value={String(o.data)}>{o.label}</option>
      ))}
    </select>
  </label>
);

export const TextField = ({ label, value, onChange, bIsPassword }: { label?: ReactNode; value?: string; onChange?: (e: any) => void; bIsPassword?: boolean; mustBeNumeric?: boolean }) => (
  <label>
    {label}
    <input aria-label={String(label)} type={bIsPassword ? "password" : "text"} value={value ?? ""} onChange={(e) => onChange?.(e)} />
  </label>
);

export const Field = ({ label, children, description }: { label?: ReactNode; children?: ReactNode; description?: ReactNode }) => (
  <div role="group" aria-label={String(label)}>
    <span>{label}</span>
    {description && <small>{description}</small>}
    <div>{children}</div>
  </div>
);

export const ProgressBarWithInfo = ({ label, nProgress, indeterminate, sOperationText, description }: { label?: ReactNode; nProgress?: number; indeterminate?: boolean; sOperationText?: ReactNode; description?: ReactNode }) => (
  <div role="progressbar" aria-label={String(label)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate ? undefined : nProgress} aria-busy={indeterminate || undefined}>
    {sOperationText}
    {description}
  </div>
);

export const ConfirmModal = (p: { strTitle?: ReactNode; strDescription?: ReactNode; strOKButtonText?: ReactNode; strCancelButtonText?: ReactNode; onOK?: () => void; onCancel?: () => void; closeModal?: () => void }) => (
  <div role="dialog" aria-label={String(p.strTitle)}>
    <h2>{p.strTitle}</h2>
    <div>{p.strDescription}</div>
    <button onClick={() => { p.onOK?.(); p.closeModal?.(); }}>{p.strOKButtonText ?? "OK"}</button>
    <button onClick={() => { p.onCancel?.(); p.closeModal?.(); }}>{p.strCancelButtonText ?? "Cancel"}</button>
  </div>
);

const modals: { close: () => void }[] = [];
/** Renders the modal into document.body (like Decky) and hands it a closeModal prop. */
export const showModal = (node: ReactElement) => {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el);
  const close = () => {
    flushSync(() => root.unmount());
    el.remove();
  };
  modals.push({ close });
  flushSync(() => root.render(cloneElement(node, { closeModal: close } as object)));
  return { Close: close, Update: () => undefined };
};

export const ModalRoot = ({ children, onCancel }: { children?: ReactNode; onCancel?: () => void; closeModal?: () => void }) => (
  <div onKeyDown={(e) => { if (e.key === "Escape") onCancel?.(); }}>{children}</div>
);

let closeMenu: (() => void) | null = null;
/** A context menu rendered into document.body; choosing an item closes it (like Steam's). */
export const showContextMenu = (node: ReactNode) => {
  closeMenu?.();
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el);
  const close = () => {
    closeMenu = null;
    flushSync(() => root.unmount());
    el.remove();
  };
  closeMenu = close;
  modals.push({ close });
  flushSync(() => root.render(<>{node}</>));
  return { Hide: close, Show: () => undefined };
};
export const Menu = ({ label, children, onCancel }: { label: string; children?: ReactNode; onCancel?: () => void }) => (
  <div role="menu" aria-label={label} onKeyDown={(e) => { if (e.key === "Escape") { onCancel?.(); closeMenu?.(); } }}>{children}</div>
);
export const MenuItem = ({ children, onSelected, onClick, disabled, tone }: { children?: ReactNode; onSelected?: (e: unknown) => void; onClick?: (e: unknown) => void; disabled?: boolean; tone?: string }) => (
  <button role="menuitem" disabled={disabled} data-tone={tone} onClick={(e) => { onSelected?.(e); onClick?.(e); closeMenu?.(); }}>{children}</button>
);

export const SidebarNavigation = ({ pages }: { pages: ({ title: ReactNode; content: ReactNode } | "separator")[] }) => {
  const real = pages.filter((p): p is { title: ReactNode; content: ReactNode } => p !== "separator");
  const [i, setI] = useState(0);
  return (
    <div>
      <nav>
        {real.map((p, n) => (
          <button key={n} role="tab" aria-selected={n === i} onClick={() => setI(n)}>{p.title}</button>
        ))}
      </nav>
      <main>{real[i].content}</main>
    </div>
  );
};

export const Navigation = { Navigate: vi.fn(), NavigateToExternalWeb: vi.fn() };
export const Router: { CloseSideMenus: ReturnType<typeof vi.fn>; MainRunningApp: unknown } = { CloseSideMenus: vi.fn(), MainRunningApp: undefined };

export function resetUi() {
  closeMenu = null;
  modals.splice(0).forEach((m) => { try { m.close(); } catch { /* already closed */ } });
  document.body.innerHTML = "";
  Navigation.Navigate.mockReset();
  Navigation.NavigateToExternalWeb.mockReset();
  Router.CloseSideMenus.mockReset();
  Router.MainRunningApp = undefined;
}

// A settings / details sheet: a Steam modal with a header (picture, title, meta), a body, and the sheet's own actions.
// There is no Done button: B (and a tap outside) closes a Steam modal, and Steam's footer already shows B BACK. The sheet's
// real action (Install, Update, ...) is the one primary button, and gamepad focus lands on it when the sheet opens; on close
// focus goes back to the control that opened the sheet (the card or the Settings button).
// showModal renders it outside the page's React tree, so the body must load its own data (useLoad) to stay live.
import { ModalRoot, showModal } from "@decky/ui";
import { ReactNode, useEffect, useRef } from "react";
import { Styles } from "./ui";

export interface SheetProps {
  title: string;
  meta?: ReactNode;
  art?: ReactNode;
  /** The sheet's actions (a wrapping .ink-bar of DialogButtons). Mark the main one `ink-btn-primary`; it gets focus first. */
  footer?: ReactNode;
  children?: ReactNode;
  closeModal?: () => void;
}

/** The first enabled primary button in the sheet, else its first enabled button: where focus starts. */
export const firstFocus = (root: HTMLElement): HTMLElement | null =>
  root.querySelector<HTMLElement>(".ink-sheet-f .ink-btn-primary:not(:disabled)") ??
  root.querySelector<HTMLElement>("button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex='-1'])");

export function Sheet({ title, meta, art, footer, children, closeModal }: SheetProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    if (ref.current) firstFocus(ref.current)?.focus();
    // Back to the opener on close, if it is still on the page (an uninstalled card is gone: Steam then picks).
    return () => { if (opener?.isConnected) opener.focus(); };
  }, []);
  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <Styles />
      <div className="ink-sheet" role="dialog" aria-label={title} ref={ref}>
        <header className="ink-sheet-h">
          {art && <div className="ink-sheet-art">{art}</div>}
          <div>
            <h2 className="ink-sheet-t">{title}</h2>
            {meta && <div className="ink-sheet-m">{meta}</div>}
          </div>
        </header>
        {children}
        {footer && <div className="ink-sheet-f">{footer}</div>}
      </div>
    </ModalRoot>
  );
}

export const openSheet = (props: Omit<SheetProps, "closeModal">) => showModal(<Sheet {...props} />);

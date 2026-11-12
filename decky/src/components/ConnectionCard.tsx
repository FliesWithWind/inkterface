// One card per connection (Panel Wi-Fi, Steam, Weather): a status header that is also the button that opens or closes the setup controls,
// then the controls. The header says in one glance what is set: icon (or the account's avatar), name, a state badge and one line of detail.
// Closed when the connection is set, open when it is not, so a finished setup stays short and a missing one asks for attention.
import { DialogButton, PanelSection, PanelSectionRow } from "@decky/ui";
import { ReactNode } from "react";
import { FaChevronDown } from "react-icons/fa";
import { Badge, Tone } from "./ui";

export interface CardState {
  tone: Tone;
  label: "Connected" | "Set" | "Not set" | "Error";
  detail: string;
}

export function ConnectionCard({ title, icon, avatar, state, open, onToggle, children }: {
  title: string;
  icon: ReactNode;
  avatar?: string | null;
  state: CardState;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="ink-conn" aria-label={title} data-state={state.label}>
      <DialogButton className="ink-conn-h" aria-expanded={open} onClick={onToggle}>
        {avatar ? <img className="ink-conn-ic ink-conn-av" src={avatar} alt="" /> : <span className="ink-conn-ic" aria-hidden="true">{icon}</span>}
        <span className="ink-conn-t">
          <span className="ink-conn-n">{title}<Badge tone={state.tone}>{state.label}</Badge></span>
          <span className="ink-conn-d">{state.detail}</span>
        </span>
        <FaChevronDown className="ink-conn-ch" aria-hidden="true" />
      </DialogButton>
      {open && <div className="ink-conn-b"><PanelSection>{children}</PanelSection></div>}
    </section>
  );
}

/** The one blue button of a card: the action that moves the setup forward. */
export const PrimaryButton = ({ children, disabled, onClick }: { children: ReactNode; disabled?: boolean; onClick: () => void }) => (
  <PanelSectionRow>
    <DialogButton className="ink-btn ink-btn-primary ink-btn-block" disabled={disabled} onClick={onClick}>{children}</DialogButton>
  </PanelSectionRow>
);

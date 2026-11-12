// The action bar of a screen's details sheet, shared by the Store's Browse sheet and its Installed sheet so the same
// installed screen shows the same buttons in both: same order, labels, primary choice and confirm dialogs.
//   not installed: Install (primary; a code screen goes on to the approval dialog)
//   installed:     Update to X (when the catalog has a newer one), Review and approve (when needed),
//                  Add to Live / Add to Standby (when it has that layout and is not there yet), then Uninstall.
// The first non-danger action is the primary (blue) one; Uninstall is last and never primary. Uninstall asks first, and the
// question says when the screen is in Live / Standby. It loads its own settings (the sheet renders outside the page).
import { ConfirmModal, DialogButton, Focusable, showModal } from "@decky/ui";
import { ReactNode } from "react";
import { FaCode, FaDownload, FaPlus, FaSyncAlt, FaTrash } from "react-icons/fa";
import { CatalogEntry, ScreenInfo, catalogInstall, getSettings, setRotation, setSettings, uninstallPackage } from "../api";
import { useAction, useLoad } from "../hooks";
import { STORE_REINSTALL_NOTE } from "../names";
import { ApprovalDialog } from "./ApprovalDialog";

type Act = ReturnType<typeof useAction>;

/** Where an installed screen is used, as one short phrase ("Live and Standby", "Live", "Standby", "Not used"). */
export function usedIn(s: Pick<ScreenInfo, "in_rotation" | "in_idle">): string {
  if (s.in_rotation && s.in_idle) return "Live and Standby";
  if (s.in_rotation) return "Live";
  if (s.in_idle) return "Standby";
  return "Not used";
}

/** The confirm text: says when the screen is in use and that uninstalling takes it out of there. */
export function uninstallText(s: ScreenInfo): string {
  const used = usedIn(s);
  if (used === "Not used") return STORE_REINSTALL_NOTE;
  const where = used === "Live and Standby" ? "your Live rotation and your Standby screens" : used === "Live" ? "your Live rotation" : "your Standby screens";
  return `${s.name} is in ${where}. Uninstalling takes it out of there too. ${STORE_REINSTALL_NOTE}`;
}

export interface PackageActionsProps {
  /** The catalog entry, if the catalog has this screen. Its update_available decides Update. */
  entry?: CatalogEntry;
  /** The installed screen (list_screens), if installed. */
  screen?: ScreenInfo;
  /** The sheet's action state: busy disables every button, its error shows in the sheet body. */
  act: Act;
  setInfo: (s: string | null) => void;
  approve: (pkg: ScreenInfo, act: Act) => Promise<void>;
  /** Called after an install / update / uninstall went through (the page may reload its list). */
  onChanged?: () => void;
  closeModal?: () => void;
}

export function PackageActions({ entry, screen: s, act, setInfo, approve, onChanged, closeModal }: PackageActionsProps) {
  const settings = useLoad(getSettings, ["settings_changed"]);
  const cfg = settings.data;
  const name = s?.name ?? entry?.name ?? "";
  const id = s?.id ?? entry?.id ?? "";
  const installed = !!s || !!entry?.installed;
  const needsApproval = s ? s.needs_approval : !!entry?.needs_approval;

  const install = async (e: CatalogEntry) => {
    setInfo(null);
    const r = await act.run(catalogInstall(e.id, e.version));
    if (!r.ok) return;
    onChanged?.();
    if (r.needs_approval) showModal(<ApprovalDialog pkg={r.package} onApprove={(p) => approve(p, act)} />);
    else {
      setInfo(installed ? `${e.name} updated to ${e.version}.` : `${e.name} ${e.version} installed.`);
      closeModal?.();
    }
  };
  const addLive = async (x: ScreenInfo) => {
    if (!cfg) return;
    const r = await act.run(setRotation([...cfg.rotation.filter((i) => i !== x.id), x.id], cfg.rotation_interval_s));
    if (r.ok) setInfo(`${x.name} added to Live Screens.`);
  };
  const addStandby = async (x: ScreenInfo) => {
    if (!cfg) return;
    const r = await act.run(setSettings({ idle_screens: [...cfg.idle_screens.filter((i) => i !== x.id), x.id] }));
    if (r.ok) setInfo(`${x.name} added to Standby Screens.`);
  };
  const uninstall = () =>
    showModal(
      <ConfirmModal
        strTitle={`Uninstall “${name}”?`}
        strDescription={s ? uninstallText(s) : STORE_REINSTALL_NOTE}
        strOKButtonText="Uninstall"
        bDestructiveWarning
        onOK={async () => {
          setInfo(null);
          const r = await act.run(uninstallPackage(id));
          if (r.ok) { setInfo(`${name} uninstalled.`); closeModal?.(); }
          onChanged?.();
        }}
      />,
    );

  // Every action but Uninstall, in order; the first one is the primary.
  const actions: { key: string; label: string; icon: ReactNode; run: () => void; disabled?: boolean }[] = [];
  if (!installed && entry) actions.push({ key: "install", label: "Install", icon: <FaDownload aria-hidden="true" />, run: () => install(entry), disabled: !entry.compatible });
  if (installed && entry?.update_available) actions.push({ key: "update", label: `Update to ${entry.version}`, icon: <FaSyncAlt aria-hidden="true" />, run: () => install(entry), disabled: !entry.compatible });
  if (installed && needsApproval) actions.push({ key: "approve", label: "Review and approve", icon: <FaCode aria-hidden="true" />, run: () => s && showModal(<ApprovalDialog pkg={s} onApprove={(p) => approve(p, act)} />), disabled: !s });
  if (s && !s.needs_approval && s.has_active && !s.in_rotation) actions.push({ key: "live", label: "Add to Live", icon: <FaPlus aria-hidden="true" />, run: () => addLive(s) });
  if (s && !s.needs_approval && s.has_idle && !s.in_idle) actions.push({ key: "standby", label: "Add to Standby", icon: <FaPlus aria-hidden="true" />, run: () => addStandby(s) });

  return (
    <Focusable className="ink-bar" flow-children="horizontal">
      {actions.map((a, i) => (
        <DialogButton key={a.key} className={i === 0 ? "ink-btn ink-btn-primary" : "ink-btn"} disabled={act.busy || a.disabled} onClick={a.run}>
          {a.icon} {a.label} <span className="ink-sr">{name}</span>
        </DialogButton>
      ))}
      {installed && (
        <DialogButton className="ink-btn" data-danger="true" disabled={act.busy} onClick={uninstall}>
          <FaTrash aria-hidden="true" /> Uninstall <span className="ink-sr">{name}</span>
        </DialogButton>
      )}
    </Focusable>
  );
}

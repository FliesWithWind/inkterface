// The Screen Store's "Installed" tab, in the Browse tab's look: one capsule card per installed non-built-in package
// (picture, name, author and version, badges for code / declarative, where it is used, update available, approval). A / click
// opens the card's details sheet, which holds every action (Update, Review and approve, Add to Live / Standby, Uninstall).
// Above the grid one compact line: with Developer mode off, a note and a small Open Settings button; with it on, the zip/URL
// import field. Backend contracts kept: approve_package(id, version, sha256), import_package, uninstall with a confirm (which
// says when the screen is in use), catalog_install for updates, set_rotation / set_settings(idle_screens) for adding.
import { DialogButton, Focusable, Navigation, TextField, showModal } from "@decky/ui";
import { useState } from "react";
import { FaBoxOpen, FaCode, FaDownload } from "react-icons/fa";
import {
  CatalogEntry,
  ScreenInfo,
  approvePackage,
  catalogList,
  getSettings,
  importPackage,
  listScreens,
} from "../api";
import { useAction, useLoad } from "../hooks";
import { NAMES } from "../names";
import { SETTINGS_ROUTE } from "../routes";
import { ApprovalDialog, CODE_WARNING } from "./ApprovalDialog";
import { ErrorLine } from "./common";
import { PackageActions, usedIn } from "./PackageActions";
import { Sheet } from "./Sheet";
import { Badge, Hint, ScreenThumb, noIdlePreviewReason, noPreviewReason } from "./ui";

type Act = ReturnType<typeof useAction>;

export { uninstallText, usedIn } from "./PackageActions";

/** The catalog cache only (no network): which installed ids have a newer catalog version. A missing catalog means no updates. */
const loadUpdates = async (): Promise<Map<string, CatalogEntry>> => {
  const r = await catalogList();
  return new Map(r.ok ? r.entries.filter((e) => e.update_available).map((e) => [e.id, e]) : []);
};

/** The picture: the active look, or the standby layout for a standby-only screen. */
const Thumb = ({ s, detail }: { s: ScreenInfo; detail?: boolean }) =>
  s.has_active || !s.has_idle ? (
    <ScreenThumb id={s.id} name={s.name} refreshKey={s.version} reason={noPreviewReason(s)} detail={detail} />
  ) : (
    <ScreenThumb id={s.id} name={s.name} refreshKey={s.version} reason={noIdlePreviewReason(s)} idle={{ mode: null }} detail={detail} />
  );

function PackageBadges({ s, update }: { s: ScreenInfo; update?: CatalogEntry }) {
  const used = usedIn(s);
  return (
    <>
      {s.has_code ? <Badge tone="warn" icon={<FaCode aria-hidden="true" />}>Runs code</Badge> : <Badge tone="muted">Declarative</Badge>}
      <Badge tone={used === "Not used" ? "muted" : "ok"}>{used === "Not used" ? used : `In ${used}`}</Badge>
      {update && <Badge tone="info">Update {update.version}</Badge>}
      {s.needs_approval && <Badge tone="warn">Needs approval</Badge>}
      {!s.needs_approval && !s.enabled && <Badge tone="muted">Disabled</Badge>}
    </>
  );
}

export function InstalledPackages({ onBrowse }: { onBrowse?: () => void }) {
  const screens = useLoad(listScreens, ["screens_changed"]);
  const settings = useLoad(getSettings, ["settings_changed"]);
  const updates = useLoad(loadUpdates, ["screens_changed"]);
  const act = useAction();
  const [source, setSource] = useState("");
  const [info, setInfo] = useState<string | null>(null);
  const devMode = settings.data?.developer_mode ?? false;

  const approve = async (pkg: ScreenInfo, a: Act = act) => {
    const r = await a.run(approvePackage(pkg.id, pkg.version, pkg.code_sha256 ?? ""));
    if (r.ok) setInfo(`${pkg.name} approved and enabled.`);
    screens.reload();
  };

  const doImport = async () => {
    setInfo(null);
    const r = await act.run(importPackage(source.trim()));
    if (!r.ok) return;
    setSource("");
    screens.reload();
    if (r.needs_approval) showModal(<ApprovalDialog pkg={r.package} onApprove={(p) => approve(p)} />);
    else setInfo(`${r.package.name} ${r.package.version} installed.`);
  };

  const open = (s: ScreenInfo) =>
    showModal(<PackageSheet id={s.id} initial={s} update={updates.data?.get(s.id)} setInfo={setInfo} approve={approve} reload={screens.reload} />);

  const packages = (screens.data ?? []).filter((s) => !s.builtin);

  return (
    <>
      {devMode ? (
        <section className="ink-inst-dev" aria-label="Import a screen package">
          <Focusable className="ink-bar ink-inst-imp" flow-children="horizontal">
            <div className="ink-inst-field">
              <TextField label="Path or https URL" value={source} onChange={(e) => setSource(e.target.value)} />
            </div>
            <DialogButton className="ink-btn" disabled={act.busy || source.trim() === ""} onClick={doImport}>
              <FaDownload aria-hidden="true" /> Import
            </DialogButton>
          </Focusable>
          <div className="ink-inst-note">A .zip on this device or an https:// link. Screens with code ask for your approval first.</div>
        </section>
      ) : (
        <Focusable className="ink-bar ink-inst-line" flow-children="horizontal">
          <span className="ink-inst-note">Importing a .zip or a link needs Developer mode.</span>
          <DialogButton className="ink-btn ink-btn-sm" onClick={() => Navigation.Navigate(SETTINGS_ROUTE)}>
            Open Settings
          </DialogButton>
        </Focusable>
      )}
      <ErrorLine error={screens.error ? `Cannot load packages: ${screens.error}` : null} />
      <ErrorLine error={act.error} />
      {info && <div className="ink-moved" style={{ marginBottom: 12 }}>{info}</div>}
      {screens.data && packages.length === 0 ? (
        <section className="ink-empty" aria-label="No screens installed">
          <div className="ink-empty-ico" aria-hidden="true"><FaBoxOpen /></div>
          <h2 className="ink-empty-t">No screens installed yet</h2>
          <p className="ink-empty-d">Community screens you install from the {NAMES.store} show up here.</p>
          {onBrowse && (
            <Focusable className="ink-bar" flow-children="horizontal" style={{ justifyContent: "center" }}>
              <DialogButton className="ink-btn ink-btn-primary" onClick={onBrowse}>Browse screens</DialogButton>
            </Focusable>
          )}
        </section>
      ) : (
        packages.length > 0 && (
          <>
            <Focusable className="ink-grid" flow-children="horizontal">
              {packages.map((s) => (
                <Focusable
                  key={s.id}
                  className="ink-cap"
                  flow-children="vertical"
                  role="group"
                  aria-label={s.name}
                  onActivate={() => open(s)}
                  onOKActionDescription="Details"
                >
                  <Thumb s={s} />
                  <div className="ink-cap-t">{s.name}</div>
                  <div className="ink-cap-m">by {s.author} · v{s.version}</div>
                  <div className="ink-cap-b"><PackageBadges s={s} update={updates.data?.get(s.id)} /></div>
                </Focusable>
              ))}
            </Focusable>
            <div className="ink-keys" aria-hidden="true">
              <Hint k="A">Details</Hint>
              <Hint k="B">Back</Hint>
            </div>
          </>
        )
      )}
    </>
  );
}

/** The details sheet of one installed package. It loads its own screens and settings (showModal renders outside the page). */
function PackageSheet({ id, initial, update, setInfo, approve, reload, closeModal }: {
  id: string;
  initial: ScreenInfo;
  update?: CatalogEntry;
  setInfo: (s: string | null) => void;
  approve: (pkg: ScreenInfo, a: Act) => Promise<void>;
  reload: () => void;
  closeModal?: () => void;
}) {
  const act = useAction();
  const screens = useLoad(listScreens, ["screens_changed"]);
  const s = screens.data?.find((x) => x.id === id) ?? initial;

  const footer = <PackageActions entry={update} screen={s} act={act} setInfo={setInfo} approve={approve} onChanged={reload} closeModal={closeModal} />;

  return (
    <Sheet title={s.name} meta={`by ${s.author} · v${s.version}`} art={<Thumb s={s} detail />} footer={footer} closeModal={closeModal}>
      <div className="ink-cap-b"><PackageBadges s={s} update={update} /></div>
      {s.description && <div className="ink-desc" style={{ WebkitLineClamp: "unset" as unknown as number, margin: "8px 0" }}>{s.description}</div>}
      <dl className="ink-facts" style={{ margin: "8px 0" }}>
        <div><dt>Type</dt><dd>{s.has_code ? "Runs code" : "Declarative"}</dd></div>
        <div><dt>Used in</dt><dd>{usedIn(s)}</dd></div>
        <div><dt>Id</dt><dd>{s.id}</dd></div>
      </dl>
      {s.needs_approval && <div className="ink-warn">{CODE_WARNING}</div>}
      {update && !update.compatible && update.incompatible_reason && <div className="ink-warn">{update.incompatible_reason}</div>}
      {act.error && <div className="ink-err" role="alert">{act.error}</div>}
    </Sheet>
  );
}

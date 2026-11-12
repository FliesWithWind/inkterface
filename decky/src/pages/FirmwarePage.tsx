import { ButtonItem, Field, Navigation, PanelSection, ProgressBarWithInfo, showModal } from "@decky/ui";
import { useState } from "react";
import { FirmwareStatus, cancelFirmwareInstall, checkFirmware, errText, installFirmware } from "../api";
import { InstallFirmwareDialog } from "../components/InstallFirmwareDialog";
import { ErrorLine, Note } from "../components/common";
import { useAction, useFirmware } from "../hooks";

export const NO_KEY_TITLE = "No signing key yet";
const BUSY = ["downloading", "preparing", "sending", "confirming"];

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

/** "GitHub allows 60 … used them up. Checks work again at 14:05 (in about 12 min)." */
export function limitLine(c: FirmwareStatus["check"], now = Date.now()): string {
  const at = c.retry_at;
  const mins = at ? Math.max(1, Math.ceil((at * 1000 - now) / 60000)) : 0;
  const t = at ? new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
  return `${c.limit_message} ${at ? `Checks work again at ${t} (in about ${mins} min).` : "Try again in about an hour."}`;
}

function Progress({ fw, onCancel, cancelling }: { fw: FirmwareStatus; onCancel: () => void; cancelling: boolean }) {
  const i = fw.install;
  return (
    <PanelSection title={`Installing firmware ${i.version ?? ""}`}>
      <ProgressBarWithInfo
        label="Firmware update"
        nProgress={i.percent}
        indeterminate={i.state !== "sending"}
        sOperationText={i.state === "sending" ? `${i.percent}%` : undefined}
        description={i.message ?? undefined}
      />
      {/* ProgressBarWithInfo renders no accessible text in every Steam build: keep one plain status line */}
      <div role="status" style={{ fontSize: 13 }}>{i.message}</div>
      <ButtonItem layout="below" disabled={!i.cancellable || cancelling} onClick={onCancel}>
        Cancel update
      </ButtonItem>
      {!i.cancellable && i.state === "confirming" && (
        <Note>The image is on the panel. It is restarting and will confirm the new firmware; this cannot be cancelled.</Note>
      )}
    </PanelSection>
  );
}

function Result({ fw }: { fw: FirmwareStatus }) {
  const i = fw.install;
  if (i.state === "done") return <Note>{i.message}</Note>;
  if (i.state === "cancelled") return <Note>{i.message}</Note>;
  if (i.state === "failed") {
    return (
      <>
        <ErrorLine error={`Firmware update failed: ${i.error}`} />
        <Note>{i.message}</Note>
      </>
    );
  }
  return null;
}

export function FirmwarePage() {
  const { fw, error, set } = useFirmware();
  const act = useAction();
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  if (!fw) {
    return (
      <PanelSection title="Firmware">
        {error ? <ErrorLine error={`Cannot reach the backend: ${error}`} /> : <Note>Loading…</Note>}
      </PanelSection>
    );
  }

  const busy = BUSY.includes(fw.install.state);
  const noKey = fw.trust.state === "no_key";
  const latest = fw.latest;

  const check = async () => {
    setChecking(true);
    setCheckError(null);
    try {
      set(await checkFirmware());
    } catch (e) {
      setCheckError(errText(e));
    } finally {
      setChecking(false);
    }
  };
  const install = () => {
    if (!latest) return;
    const version = latest.version;
    showModal(
      <InstallFirmwareDialog from={fw.installed.version} to={version} onInstall={() => act.run(installFirmware(version))} />,
    );
  };
  const open = (url: string) => {
    try {
      Navigation.NavigateToExternalWeb(url);
    } catch {
      /* no browser available */
    }
  };

  const canInstall = fw.update_available && !noKey && !busy && fw.installed.seen && !act.busy;

  return (
    <>
      <PanelSection title="Firmware">
        <ErrorLine error={error ? `Cannot reach the backend: ${error}` : null} />
        {noKey && (
          <div role="alert" style={{ color: "#ffb347", fontSize: 13, padding: "4px 0" }}>
            <b>{NO_KEY_TITLE}.</b> {fw.trust.message} Updates stay switched off until a plugin release ships the key.
          </div>
        )}
        {fw.protocol_mismatch && (
          <div role="alert" style={{ color: "#ffb347", fontSize: 13, padding: "4px 0" }}>{fw.mismatch_message}</div>
        )}
        <Field label="Installed">
          {fw.installed.version
            ? `${fw.installed.version}${fw.installed.dev_build ? " (development build)" : ""}${fw.installed.connected ? "" : " (panel not connected)"}`
            : "panel not connected"}
        </Field>
        <Field label="Latest">{latest ? latest.version : fw.check.ok === false ? "unknown" : "no release published"}</Field>
        <Field label="Last checked">{when(fw.check.at)}</Field>
        {fw.check.limit_message ? (
          fw.check.ok ? <Note>{limitLine(fw.check)}</Note> : <ErrorLine error={`Could not check for updates. ${limitLine(fw.check)}`} />
        ) : (
          <ErrorLine error={fw.check.error && !noKey ? `Could not check for updates: ${fw.check.error}` : null} />
        )}
        <ErrorLine error={checkError ? `Could not check for updates: ${checkError}` : null} />
        {fw.note && <Note>{fw.note}</Note>}
        <ButtonItem layout="below" disabled={checking || fw.check.checking || busy || noKey} onClick={check}>
          {checking || fw.check.checking ? "Checking…" : "Check for updates"}
        </ButtonItem>
      </PanelSection>

      {latest && fw.update_available && !busy && (
        <PanelSection title={`Firmware ${latest.version} is available`}>
          {latest.changelog ? <Note>{latest.changelog}</Note> : <Note>No changelog was published.</Note>}
          {latest.page_url && (
            <ButtonItem layout="below" onClick={() => open(latest.page_url)}>View release on GitHub</ButtonItem>
          )}
          <ButtonItem layout="below" disabled={!canInstall} onClick={install}>
            Install firmware {latest.version}
          </ButtonItem>
          {!fw.installed.seen && !noKey && <Note>Plug in the panel to install this update.</Note>}
          <ErrorLine error={act.error} />
        </PanelSection>
      )}
      {latest && !fw.update_available && !busy && !fw.note && fw.installed.version && fw.install.state === "idle" && (
        <Note>The panel firmware is up to date.</Note>
      )}

      {busy && <Progress fw={fw} cancelling={act.busy} onCancel={() => act.run(cancelFirmwareInstall())} />}
      {!busy && (
        <PanelSection>
          <Result fw={fw} />
        </PanelSection>
      )}
      {busy && <ErrorLine error={act.error} />}
    </>
  );
}

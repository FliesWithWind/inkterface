import { ConfirmModal } from "@decky/ui";

/** Asked before every firmware install: the panel restarts and shows nothing for a while. */
export function InstallFirmwareDialog({ from, to, onInstall, closeModal }: {
  from: string | null;
  to: string;
  onInstall: () => void;
  closeModal?: () => void;
}) {
  return (
    <ConfirmModal
      strTitle={`Install firmware ${to}?`}
      strOKButtonText="Install"
      strCancelButtonText="Cancel"
      onOK={onInstall}
      onCancel={() => undefined}
      closeModal={closeModal}
      strDescription={
        <div style={{ fontSize: 13, lineHeight: 1.4 }}>
          <div>Panel firmware: <b>{from ?? "unknown"}</b> → <b>{to}</b></div>
          <div style={{ marginTop: 8 }}>
            The firmware is sent over the USB cable and takes about a minute. The panel shows nothing while it restarts.
            Keep the cable plugged in and do not shut the Steam Machine down.
          </div>
          <div style={{ marginTop: 8 }}>
            If the new firmware does not start, the panel returns to the previous version by itself after about 3 minutes.
          </div>
        </div>
      }
    />
  );
}

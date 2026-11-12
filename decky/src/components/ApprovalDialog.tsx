import { ConfirmModal } from "@decky/ui";
import { ScreenInfo } from "../api";

export const CODE_WARNING =
  "This package runs Python code on your device with your user's permissions. It is NOT sandboxed: " +
  "it can read your files and use the network. Only approve code you trust, from an author you trust.";

/** Shown for a code package before it may run. Approval is bound to exactly this id, version and sha256 of screen.py. */
export function ApprovalDialog({ pkg, onApprove, closeModal }: {
  pkg: ScreenInfo;
  onApprove: (pkg: ScreenInfo) => void;
  closeModal?: () => void;
}) {
  return (
    <ConfirmModal
      strTitle={`Approve “${pkg.name}”?`}
      strOKButtonText="Approve and enable"
      strCancelButtonText="Cancel"
      bDestructiveWarning
      onOK={() => onApprove(pkg)}
      onCancel={() => undefined}
      closeModal={closeModal}
      strDescription={
        <div style={{ fontSize: 13, lineHeight: 1.4 }}>
          <div role="alert" style={{ color: "#ffb347", marginBottom: 8 }}>{CODE_WARNING}</div>
          <div>Author: <b>{pkg.author}</b></div>
          <div>Id: <b>{pkg.id}</b></div>
          <div>Version: <b>{pkg.version}</b></div>
          <div>SHA-256 of screen.py:</div>
          <div style={{ fontFamily: "monospace", wordBreak: "break-all" }}>{pkg.code_sha256}</div>
        </div>
      }
    />
  );
}

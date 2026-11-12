import { PanelSectionRow } from "@decky/ui";
import { ReactNode } from "react";
import { Status } from "../api";

export const ErrorLine = ({ error }: { error: string | null | undefined }) =>
  error ? (
    <PanelSectionRow>
      <div role="alert" style={{ color: "#ff6b6b", whiteSpace: "pre-wrap", fontSize: 13 }}>
        {error}
      </div>
    </PanelSectionRow>
  ) : null;

export const Note = ({ children }: { children: ReactNode }) => (
  <PanelSectionRow>
    <div style={{ fontSize: 13, opacity: 0.8, whiteSpace: "pre-wrap" }}>{children}</div>
  </PanelSectionRow>
);

export function statusText(s: Status | null, loadError: string | null): string {
  if (loadError) return `Cannot reach the Inkterface backend: ${loadError}`;
  if (!s) return "Loading…";
  switch (s.ui_state) {
    case "connected":
      return `Connected${s.fw_version ? ` · firmware ${s.fw_version}` : ""}`;
    case "connecting":
      return "Connecting to the panel…";
    case "waiting":
      return "Waiting for panel · plug in the Inkterface USB cable";
    case "firmware_too_old":
      return "Firmware too old · open Inkterface → Firmware to update the panel";
    case "updating":
      return "Updating the panel firmware… keep the cable plugged in";
    case "stopped":
      return "Stopped";
    default:
      return String(s.ui_state);
  }
}

/** The 648x480 panel image, or why there is none. */
export const PreviewImage = ({ url, error, alt }: { url: string | null; error: string | null; alt: string }) =>
  url ? (
    <img src={url} alt={alt} style={{ width: "100%", imageRendering: "pixelated", background: "#fff", borderRadius: 2 }} />
  ) : (
    <div style={{ fontSize: 13, opacity: 0.7 }}>{error ? `No preview: ${error}` : "No preview yet"}</div>
  );

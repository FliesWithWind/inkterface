import { ButtonItem, Field, Navigation, PanelSection } from "@decky/ui";
import { getStatus } from "../api";
import { ErrorLine } from "../components/common";
import { useLoad } from "../hooks";

export const LINKS = [
  { label: "Source code and issues", url: "https://github.com/FliesWithWind/inkterface" },
  { label: "Screen format and package docs", url: "https://github.com/FliesWithWind/inkterface/tree/main/docs" },
];

export function AboutPage() {
  const status = useLoad(getStatus, ["status"]);
  const s = status.data;
  const open = (url: string) => {
    try {
      Navigation.NavigateToExternalWeb(url);
    } catch {
      /* no browser available */
    }
  };
  return (
    <PanelSection title="About Inkterface">
      <ErrorLine error={status.error ? `Cannot reach the backend: ${status.error}` : null} />
      <Field label="Plugin version">{s?.plugin_version ?? "unknown"}</Field>
      <Field label="Panel firmware">{s?.fw_version ?? "not connected"}</Field>
      <Field label="Protocol version">{s?.protocol_version ?? "unknown"}</Field>
      <Field label="Panel size">{s?.panel_size ? `${s.panel_size[0]}×${s.panel_size[1]}` : "unknown"}</Field>
      {LINKS.map((l) => (
        <ButtonItem key={l.url} layout="below" onClick={() => open(l.url)}>{l.label}</ButtonItem>
      ))}
    </PanelSection>
  );
}

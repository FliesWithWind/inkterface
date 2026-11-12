import { ButtonItem, DropdownItem, Navigation, PanelSection, PanelSectionRow, Router } from "@decky/ui";
import { listScreens, pinScreen, unpinScreen } from "./api";
import { ErrorLine, PreviewImage, statusText } from "./components/common";
import { useAction, useLoad, usePreview, useStatus } from "./hooks";
import { ROUTE } from "./routes";

export { ROUTE };
const AUTO = "__auto__";

/** Quick-access-menu panel: connection, what is on the panel now, quick pin. */
export function QuickPanel() {
  const { status, error } = useStatus();
  const screens = useLoad(listScreens, ["screens_changed", "settings_changed"]);
  const current = status?.current_screen ?? null;
  const preview = usePreview(status?.connected ? current : null, status?.last_frame_at ?? current);
  const pin = useAction();

  const options = [
    { data: AUTO, label: "Auto (rotation)" },
    ...(screens.data ?? []).filter((s) => s.enabled && s.has_active).map((s) => ({ data: s.id, label: s.name })),
  ];
  const choose = (o: { data: string }) => pin.run(o.data === AUTO ? unpinScreen() : pinScreen(o.data));
  const open = (route: string = ROUTE) => {
    try {
      Router.CloseSideMenus();
    } catch {
      /* not available in this Steam build */
    }
    Navigation.Navigate(route);
  };

  return (
    <PanelSection title="Inkterface">
      <PanelSectionRow>
        <div role="status" data-testid="status-line">{statusText(status, error)}</div>
      </PanelSectionRow>
      {status?.ui_state === "firmware_too_old" && status.last_error && <ErrorLine error={status.last_error} />}
      {status?.ui_state === "firmware_too_old" && (
        <ButtonItem layout="below" onClick={() => open(`${ROUTE}/firmware`)}>
          Update panel firmware
        </ButtonItem>
      )}
      {status?.connected && (
        <>
          <PanelSectionRow>
            <PreviewImage url={preview.url} error={preview.error} alt={`Current screen: ${current ?? "none"}`} />
          </PanelSectionRow>
          <PanelSectionRow>
            <div style={{ fontSize: 13 }}>
              Showing: <b>{screens.data?.find((s) => s.id === current)?.name ?? current ?? "nothing yet"}</b>
              {status.pinned ? " (pinned)" : ""}
            </div>
          </PanelSectionRow>
          <DropdownItem
            label="Pin screen"
            rgOptions={options}
            selectedOption={status.pinned ?? AUTO}
            disabled={pin.busy || screens.data === null}
            onChange={choose}
          />
          <ErrorLine error={pin.error} />
        </>
      )}
      <ButtonItem layout="below" onClick={() => open()}>
        Open Inkterface settings
      </ButtonItem>
    </PanelSection>
  );
}

import { SidebarNavigation } from "@decky/ui";
import { ReactNode } from "react";
import { PAGE_END } from "./components/styles";
import { NAMES } from "./names";
import { AboutPage } from "./pages/AboutPage";
import { FirmwarePage } from "./pages/FirmwarePage";
import { IdlePage } from "./pages/IdlePage";
import { ScreensPage } from "./pages/ScreensPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StorePage } from "./pages/StorePage";
import { ROUTE, STORE_ROUTE } from "./routes";

/** Every page ends with room for Steam's footer bar, so the last line and the last row scroll fully into view. */
export const Page = ({ children }: { children: ReactNode }) => <div className="ink-page" style={{ paddingBottom: PAGE_END }}>{children}</div>;

// Routes keep their old names (/inkterface/screens, /idle): deep links and the quick panel use them, nobody reads them.
export function MainPage() {
  return (
    <SidebarNavigation
      title="Inkterface"
      showTitle
      pages={[
        { title: NAMES.live, content: <Page><ScreensPage /></Page>, route: `${ROUTE}/screens` },
        { title: NAMES.standby, content: <Page><IdlePage /></Page>, route: `${ROUTE}/idle` },
        { title: NAMES.store, content: <Page><StorePage /></Page>, route: STORE_ROUTE },
        { title: NAMES.firmware, content: <Page><FirmwarePage /></Page>, route: `${ROUTE}/firmware` },
        { title: NAMES.settings, content: <Page><SettingsPage /></Page>, route: `${ROUTE}/settings` },
        { title: NAMES.about, content: <Page><AboutPage /></Page>, route: `${ROUTE}/about` },
      ]}
    />
  );
}

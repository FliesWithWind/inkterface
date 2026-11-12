import { definePlugin, routerHook } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { MainPage } from "./MainPage";
import { QuickPanel, ROUTE } from "./QuickPanel";
import { setNowPlaying } from "./api";
import { installNowPlaying } from "./steam";
// The Inkterface icon from Valve's original (48x48 pixel art, assets/valve/README.md). rollup emits it to dist/assets/, which Decky serves.
import iconUrl from "../../assets/valve/icon.png";

export default definePlugin(() => {
  const stopNowPlaying = installNowPlaying(setNowPlaying);
  routerHook.addRoute(ROUTE, MainPage, { exact: false });
  return {
    name: "Inkterface",
    titleView: <div className={staticClasses.Title}>Inkterface</div>,
    content: <QuickPanel />,
    icon: <img src={iconUrl} alt="" style={{ height: "1em", width: "1em", imageRendering: "pixelated" }} />,
    onDismount() {
      stopNowPlaying();
      routerHook.removeRoute(ROUTE);
    },
  };
});

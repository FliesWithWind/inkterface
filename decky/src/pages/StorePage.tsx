// The Screen Store: one page, two tabs. "Browse" is the signed community catalog as a grid of capsule cards; opening a card
// (A / click) shows a details sheet with the same actions (components/PackageActions) the Installed sheet has. "Installed" lists the
// installed packages (the old Packages page) and, with Developer mode on, the zip/URL import form.
//
// "Steam Library" look: capsule cards in .ink-grid, Steam-blue focus glow + lift
// (styles.ts), a bottom button-hint bar. showModal renders the sheet OUTSIDE the page tree, so the sheet body loads
// its own data. Every backend contract of the old Store/Packages pages is kept; this only re-skins and re-homes them.
import { DialogButton, Focusable, Navigation, PanelSection, showModal } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { FaCode, FaStore, FaSyncAlt } from "react-icons/fa";
import {
  CatalogEntry,
  ScreenInfo,
  approvePackage,
  catalogList,
  catalogPreview,
  catalogRefresh,
  listScreens,
  onEvent,
} from "../api";
import { CODE_WARNING } from "../components/ApprovalDialog";
import { InstalledPackages } from "../components/InstalledPackages";
import { Note } from "../components/common";
import { PackageActions } from "../components/PackageActions";
import { Sheet } from "../components/Sheet";
import { Badge, Hint, ScreenThumb, Styles, Tabs } from "../components/ui";
import { useAction, useLoad } from "../hooks";
import { NAMES, STORE_NOT_SET_UP_BODY, STORE_NOT_SET_UP_TITLE } from "../names";
import { SETTINGS_ROUTE } from "../routes";

/** The catalog is "not set up" (no signing key bundled) rather than broken; make that look intentional. */
const NOT_SET_UP = "no catalog signing key is bundled";
const isNotSetUp = (error: string | null): boolean => !!error && error.includes(NOT_SET_UP);

/** "2 min ago", "3 h ago", or a date for anything older. */
export function savedAgo(fetchedAt: number, now = Date.now()): string {
  const s = Math.max(0, Math.round(now / 1000 - fetchedAt));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  return new Date(fetchedAt * 1000).toLocaleDateString();
}

interface Loaded {
  entries: CatalogEntry[];
  fetched_at: number | null;
  error: string | null; // set when the last refresh failed; the cache (if any) is still shown
  loading: boolean;
}

const originLine = (e: CatalogEntry) =>
  [`by ${e.author}`, `v${e.version}`, e.has_code ? "runs code" : null].filter(Boolean).join(" · ");

/** The badges a catalog entry carries, shared by the card and the sheet. */
function EntryBadges({ e }: { e: CatalogEntry }) {
  return (
    <>
      {e.installed && !e.update_available && <Badge tone="ok">Installed</Badge>}
      {e.update_available && <Badge tone="info">Update available</Badge>}
      {e.has_code && <Badge tone="warn">Runs code</Badge>}
      {e.needs_approval && <Badge tone="warn">Needs approval</Badge>}
      {!e.compatible && <Badge tone="error">Incompatible</Badge>}
    </>
  );
}

/** "4 KB", "2.0 MB" — the zip size the catalog reports. */
function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The body of the details sheet: badges, description, facts, warnings and the action's error. */
function SheetBody({ e, act }: { e: CatalogEntry; act: ReturnType<typeof useAction> }) {
  return (
    <>
      <div className="ink-cap-b">
        <EntryBadges e={e} />
      </div>
      {e.description && <div className="ink-desc" style={{ WebkitLineClamp: "unset" as unknown as number, margin: "8px 0" }}>{e.description}</div>}
      <dl className="ink-facts" style={{ margin: "8px 0" }}>
        <div><dt>Author</dt><dd>{e.author}</dd></div>
        <div><dt>Version</dt><dd>{e.version}</dd></div>
        <div><dt>Size</dt><dd>{sizeText(e.size)}</dd></div>
      </dl>
      {e.has_code && <div className="ink-warn">{CODE_WARNING}</div>}
      {!e.compatible && e.incompatible_reason && <div className="ink-warn">{e.incompatible_reason}</div>}
      {act.error && <div className="ink-err" role="alert">{act.error}</div>}
    </>
  );
}

export function StorePage() {
  const [tab, setTab] = useState<"browse" | "installed">("browse");
  return (
    <section aria-label={NAMES.store}>
      <Styles />
      <Tabs label={NAMES.store} value={tab} onChange={setTab} tabs={[{ id: "browse", label: "Browse" }, { id: "installed", label: "Installed" }]} />
      {tab === "browse" ? <BrowseTab /> : <InstalledPackages onBrowse={() => setTab("browse")} />}
    </section>
  );
}

function BrowseTab() {
  const [state, setState] = useState<Loaded>({ entries: [], fetched_at: null, error: null, loading: true });
  const [screens, setScreens] = useState<ScreenInfo[]>([]);
  const [info, setInfo] = useState<string | null>(null);
  const act = useAction();
  const alive = useRef(true);

  const loadScreens = () => listScreens().then((xs) => alive.current && setScreens(xs), () => undefined);

  // Read the cache, show it, then refresh from the network once and show the result.
  const read = async (): Promise<void> => {
    const r = await catalogList();
    if (!alive.current) return;
    if (r.ok) setState((s) => ({ entries: r.entries, fetched_at: r.fetched_at, error: s.error, loading: false }));
    else setState((s) => ({ ...s, error: r.error, loading: false }));
  };
  const refresh = async (): Promise<void> => {
    const r = await catalogRefresh();
    if (!alive.current) return;
    if (r.ok) {
      setState((s) => ({ ...s, error: null }));
      await read();
    } else {
      setState((s) => ({ ...s, error: r.error, loading: false }));
      await read(); // the cache may still have entries to show under the warning
    }
  };

  const reload = () => {
    setState((s) => ({ ...s, loading: true }));
    read().then(refresh);
  };

  useEffect(() => {
    alive.current = true;
    loadScreens();
    read().then(refresh);
    const off = onEvent("screens_changed", () => {
      loadScreens();
      read();
    });
    return () => {
      alive.current = false;
      off();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const approve = async (pkg: ScreenInfo, sheetAct: ReturnType<typeof useAction>) => {
    const r = await sheetAct.run(approvePackage(pkg.id, pkg.version, pkg.code_sha256 ?? ""));
    if (r.ok) setInfo(`${pkg.name} approved and enabled.`);
  };

  // The sheet renders outside the page tree with its own useAction so its errors/busy are local.
  const openDetails = (e: CatalogEntry) => {
    const screen = byId.get(e.id);
    showModal(<DetailsSheet e={e} screen={screen} setInfo={setInfo} approve={approve} />);
  };

  const byId = new Map(screens.map((s) => [s.id, s]));

  if (state.loading) {
    return <PanelSection><Note>Loading…</Note></PanelSection>;
  }

  if (isNotSetUp(state.error) && state.entries.length === 0) {
    return <NotSetUp />;
  }

  const saved = state.fetched_at !== null;
  return (
    <>
      {state.error && (
        <div style={{ margin: "0 0 12px" }}>
          <div role="alert" style={{ color: "#ffb938", whiteSpace: "pre-wrap", fontSize: 13 }}>
            {saved ? `Showing the saved catalog from ${savedAgo(state.fetched_at!)}: ${state.error}` : state.error}
          </div>
          <Focusable className="ink-bar" flow-children="horizontal" style={{ marginTop: 8 }}>
            <DialogButton className="ink-btn" disabled={act.busy} onClick={reload}>
              <FaSyncAlt aria-hidden="true" /> Retry
            </DialogButton>
          </Focusable>
        </div>
      )}
      {info && <div className="ink-moved" style={{ marginBottom: 12 }}>{info}</div>}
      {state.entries.length === 0 && !state.error ? (
        <Note>The catalog has no screens yet.</Note>
      ) : (
        <Focusable
          className="ink-grid"
          flow-children="horizontal"
          onOptionsButton={reload}
          onOptionsActionDescription="Refresh"
        >
          {state.entries.map((e) => (
            <StoreCard key={e.id} e={e} onOpen={openDetails} />
          ))}
        </Focusable>
      )}
      <div className="ink-keys" aria-hidden="true">
        <Hint k="A">Details</Hint>
        <Hint k="B">Back</Hint>
        <Hint k="Y">Refresh</Hint>
      </div>
    </>
  );
}

/** One capsule card: a Focusable the whole card. A / click opens the details sheet. */
function StoreCard({ e, onOpen }: { e: CatalogEntry; onOpen: (e: CatalogEntry) => void }) {
  return (
    <Focusable
      className="ink-cap"
      flow-children="vertical"
      role="group"
      aria-label={e.name}
      onActivate={() => onOpen(e)}
      onOKActionDescription="Details"
    >
      <ScreenThumb id={e.id} name={e.name} refreshKey={e.version} reason={null} render={catalogPreview} width={undefined} />
      <div className="ink-cap-t">
        {e.name}
        <EntryBadges e={e} />
      </div>
      <div className="ink-cap-m">
        {e.has_code && <FaCode size={10} aria-hidden="true" style={{ marginRight: 6 }} />}
        {originLine(e)}
      </div>
    </Focusable>
  );
}

/** The details sheet for one entry. Its own useAction keeps busy/error local to the modal; it reloads its own screen. */
function DetailsSheet({ e, screen, setInfo, approve, closeModal }: {
  e: CatalogEntry;
  screen: ScreenInfo | undefined;
  setInfo: (s: string | null) => void;
  approve: (pkg: ScreenInfo, act: ReturnType<typeof useAction>) => Promise<void>;
  closeModal?: () => void;
}) {
  const act = useAction();
  // Live, so the buttons follow an approval or an Add to Live made from this sheet; the opener's copy until it loads.
  const screens = useLoad(listScreens, ["screens_changed"]);
  // The catalog's own installed flag decides (a built-in with the same id is not this package).
  const s = !e.installed ? undefined : screens.data ? screens.data.find((x) => x.id === e.id) : screen;
  const footer = <PackageActions entry={e} screen={s} act={act} setInfo={setInfo} approve={approve} closeModal={closeModal} />;

  return (
    <Sheet
      title={e.name}
      meta={originLine(e)}
      art={<ScreenThumb id={e.id} name={e.name} refreshKey={e.version} reason={null} render={catalogPreview} detail />}
      footer={footer}
      closeModal={closeModal}
    >
      <SheetBody e={e} act={act} />
    </Sheet>
  );
}

/** The friendly "not set up" state for a key-less build. A panel, not a red error, not role=alert. */
function NotSetUp() {
  return (
    <section className="ink-empty" aria-label={STORE_NOT_SET_UP_TITLE}>
      <div className="ink-empty-ico" aria-hidden="true"><FaStore /></div>
      <h2 className="ink-empty-t">{STORE_NOT_SET_UP_TITLE}</h2>
      <p className="ink-empty-d">{STORE_NOT_SET_UP_BODY}</p>
      <Focusable className="ink-bar" flow-children="horizontal" style={{ justifyContent: "center" }}>
        <DialogButton className="ink-btn ink-btn-primary" onClick={() => Navigation.Navigate(SETTINGS_ROUTE)}>
          Open Settings
        </DialogButton>
      </Focusable>
    </section>
  );
}

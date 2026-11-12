// The plugin's stylesheet. Steam's own components (PanelSection, ToggleField, DialogButton, ...) bring their look; this adds
// the pieces they lack: the panel bezel, status card, list rows with the order track, store cards, badges, the sheet.
// Colours use Steam's --gpColor-* variables with the Steam value as fallback, so they follow the theme when it defines them.
const BLUE = "var(--gpColor-Blue, #1a9fff)";
const EASE = "cubic-bezier(.16,1,.3,1)";
// One focus look for every button: Steam's own focused DialogButton (white fill, dark text, measured on the Steam Machine) plus a white ring.
// Buttons that carry their own background (.ink-btn-primary, the selected tab, .ink-link) would hide Steam's fill, so they get it back here.
const FOCUS_FILL = "background:#fff !important;color:#23262e !important";

/** Space under the last thing on a page. Steam's footer button bar (about 5 % of the screen height, measured 42 px of 844) floats over the bottom of the
 *  scroll area, so without this the last line and the last row cannot scroll clear of it. */
export const PAGE_END = "calc(48px + 6vh)";

export const CSS = `
/* custom properties live on :root so the modal sheets (rendered outside the page) see them too */
:root{--ink-blue:${BLUE};--ink-blue-2:#6cc0ff;--ink-surface:rgba(255,255,255,.055);--ink-surface-2:rgba(255,255,255,.1);--ink-line:rgba(255,255,255,.09);
  --ink-tx2:#a9b0b8;--ink-tx3:#8b929a;--ink-ease:${EASE}}
.ink-group{margin:0 0 28px}
.ink-group-t{margin:0 0 4px;font-size:22px;font-weight:700;line-height:1.2;color:#fff}
.ink-group-d{margin:0 0 10px;font-size:13px;color:#b8bcbf}
.ink-group[data-apart="true"]{margin-top:40px;padding-top:16px;border-top:1px solid var(--ink-line)}
.ink-page-t{margin:0 0 14px;font-size:28px;font-weight:700;line-height:1.1;color:#fff}
.ink-sec{margin:26px 0 2px;font-size:22px;font-weight:700;line-height:1.2;color:#fff}
.ink-sec-d{margin:0 0 12px;font-size:13px;color:var(--ink-tx2)}
.ink-me{display:flex;align-items:center;gap:12px;min-width:0}
.ink-me-av{flex:none;box-sizing:border-box;width:40px;height:40px;border-radius:4px;background:var(--ink-surface-2);object-fit:cover}
.ink-qr{display:flex;flex-direction:column;align-items:center;gap:6px;padding:8px 0}
.ink-qr svg{display:block;border-radius:4px;max-width:100%}
.ink-qr-u{font-size:12px;color:var(--ink-tx2);overflow-wrap:anywhere;text-align:center}
.ink-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

/* Settings: a card per connection. The header is a button (open / close); it keeps the plugin's one focus look (white fill, dark text). */
.ink-conn{margin:0 0 12px;border-radius:6px;background:var(--ink-surface);box-shadow:inset 0 0 0 1px var(--ink-line);overflow:hidden}
.ink-conn-h{display:flex !important;align-items:center;gap:14px;box-sizing:border-box;width:100% !important;min-width:0 !important;max-width:100%;padding:14px 16px !important;
  background:transparent !important;border-radius:0 !important;text-align:left !important;color:#fff}
.ink-conn-ic{flex:none;display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:40px;height:40px;border-radius:6px;background:var(--ink-surface-2);font-size:19px}
.ink-conn-av{object-fit:cover}
.ink-conn-t{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:flex-start;gap:3px;text-align:left !important}
.ink-conn-n{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;font-size:18px;font-weight:600;line-height:1.25}
.ink-conn-d{font-size:13px;line-height:1.35;color:var(--ink-tx2);overflow-wrap:anywhere}
.ink-conn-ch{flex:none;font-size:13px;opacity:.7;transition:transform .15s ${EASE}}
.ink-conn-h[aria-expanded="true"] .ink-conn-ch{transform:rotate(180deg)}
.ink-conn-h:focus,.ink-conn-h.gpfocus,.ink-conn-h:focus-visible{${FOCUS_FILL}}
.ink-conn-h:focus .ink-conn-d,.ink-conn-h.gpfocus .ink-conn-d,.ink-conn-h:focus-visible .ink-conn-d{color:#454b55}
.ink-conn-b{border-top:1px solid var(--ink-line);padding:2px 12px 10px}
.ink-btn.ink-btn-block{display:flex !important;justify-content:center;width:100% !important;max-width:100%}
.ink-gbody{border-radius:6px;background:var(--ink-surface);box-shadow:inset 0 0 0 1px var(--ink-line);padding:4px 12px}
@media (prefers-reduced-motion:reduce){.ink-conn-ch{transition:none}}

/* Panel picture: the plain 648x480 image, 1 px subtle border so it does not bleed into the card, crisp scaling. No bezel. */
.ink-thumb{flex:none;position:relative;width:var(--ink-thumb-w,156px);min-width:0}
.ink-pic{box-sizing:border-box;position:relative;width:100%;aspect-ratio:648/480;border:1px solid rgba(255,255,255,.22);background:#fff;overflow:hidden;
  display:flex;align-items:center;justify-content:center}
.ink-pic img{display:block;width:100%;height:100%;image-rendering:-webkit-optimize-contrast;image-rendering:pixelated}
.ink-thumb-note{font-size:11px;color:#555;text-align:center;padding:4px}
.ink-skeleton{position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.04),rgba(0,0,0,.14),rgba(0,0,0,.04));
  background-size:200% 100%;animation:ink-shimmer 1.4s linear infinite}
@keyframes ink-shimmer{from{background-position:200% 0}to{background-position:-200% 0}}
[data-off="true"] .ink-thumb{opacity:.5}

/* Status card on top of a page */
.ink-stat{display:flex;flex-wrap:wrap;justify-content:space-between;gap:16px 28px;box-sizing:border-box;padding:16px 20px;margin:0 0 6px;border-radius:6px;
  background:var(--ink-surface-2);box-shadow:inset 0 0 0 1px var(--ink-line)}
.ink-stat-main{display:flex;gap:18px;align-items:flex-start;min-width:0;flex:1 1 320px}
.ink-stat-main>div:not(.ink-stat-art){flex:1 1 200px;min-width:0}
.ink-stat-art{flex:none;width:156px}
.ink-stat-t{display:flex;align-items:center;gap:10px;margin:0;font-size:22px;font-weight:700;color:#fff;line-height:1.15}
.ink-stat-s{margin-top:6px;color:var(--ink-tx2);font-size:14px}
.ink-stat-more{margin-top:8px}
.ink-facts{display:flex;flex-wrap:wrap;gap:10px 28px;margin:0;align-self:center}
.ink-facts dt{font-size:12px;color:var(--ink-tx3)}
.ink-facts dd{margin:2px 0 0;font-size:17px;font-weight:600;color:#fff}

/* A list row: [order track] [bezel picture] [text] [buttons] */
/* flex-wrap:nowrap is load-bearing for the gamepad: Steam derives a container's D-pad layout from its CSS (row + nowrap = "horizontal": left / right walk the
   controls, up / down leave the row). A wrapping flex row is read as a 2-D grid and left / right then did nothing. Narrow windows wrap (below). */
.ink-row{position:relative;display:flex;flex-wrap:nowrap;align-items:center;gap:10px 16px;box-sizing:border-box;width:100%;max-width:100%;min-width:0;
  margin:0 0 8px;padding:12px 16px 12px 12px;border-radius:4px;background:var(--ink-surface);
  transition:background .15s ${EASE},box-shadow .15s ${EASE}}
.ink-row:focus-within,.ink-row.gpfocuswithin,.ink-row.ink-row-f{background:#2c3644;box-shadow:0 0 0 3px #fff,0 12px 24px rgba(0,0,0,.45);z-index:3}
.ink-row[data-lift="true"]{background:#27374a;box-shadow:0 0 0 3px var(--ink-blue-2),0 18px 32px rgba(0,0,0,.55);z-index:4}
.ink-row-art{flex:none;width:156px}
.ink-row-body{position:relative;flex:1 1 0;min-width:0;align-self:stretch;display:flex;flex-direction:column;justify-content:flex-start;gap:3px;padding-bottom:44px}
.ink-row-body[data-nohint="true"]{padding-bottom:0}
.ink-title{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:18px;font-weight:600;line-height:1.25;color:#fff}
.ink-meta{font-size:12px;color:var(--ink-tx3)}
.ink-desc{font-size:13px;color:#cfd3d7;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ink-warn{font-size:13px;color:#ffb938}
.ink-err{font-size:13px;color:#ff8a8a;white-space:pre-wrap}
.ink-row-act{flex:none;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:flex-start;min-width:0;max-width:100%}
.ink-bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-start;gap:8px;box-sizing:border-box;min-width:0;max-width:100%}
.ink-btn{position:relative;flex:0 1 auto !important;width:auto !important;min-width:0 !important;max-width:100%;padding:9px 16px !important;
  display:inline-flex !important;align-items:center;justify-content:center;gap:8px;white-space:nowrap}
/* Disabled: Steam's own DialogButton.Disabled look (opacity .4, measured on the device); nothing of ours on top. */
.ink-btn svg{flex:none}
.ink-btn.gpfocus,.ink-btn:focus-visible{outline:3px solid #fff;outline-offset:2px}
.ink-btn-ico{padding:9px 13px !important}
.ink-btn-primary{background:${BLUE} !important;color:#fff !important;font-weight:600}
.ink-btn[data-danger="true"]{color:#ff9a9a}
.ink-link{display:inline !important;flex:none !important;width:auto !important;min-width:0 !important;padding:0 !important;background:none !important;color:var(--ink-blue-2) !important;
  text-decoration:underline;text-underline-offset:3px;font:inherit}

/* One focus look for every control: white fill + dark text (Steam's own), white ring on cards and rows. Overrides only where a class sets its own background. */
.ink-btn-primary.gpfocus,.ink-btn-primary:focus-visible,.ink-tab.gpfocus,.ink-tab:focus-visible,.ink-tab[aria-selected="true"].gpfocus,.ink-tab[aria-selected="true"]:focus-visible,
.ink-link.gpfocus,.ink-link:focus-visible{${FOCUS_FILL}}

/* The order track: up chevron, position, down chevron. The chevrons are ordinary .ink-btn DialogButtons (same hover / focus look as Settings and "..."),
   only sized to a 44 x 40 px hit area. */
.ink-ord{flex:none;display:flex;flex-direction:column;align-items:center;width:44px;border-radius:6px;background:rgba(0,0,0,.22);box-shadow:inset 0 0 0 1px var(--ink-line)}
.ink-ord-n{display:flex;align-items:center;justify-content:center;height:24px;font-size:15px;font-weight:700;color:#fff}
.ink-ord-btn{flex:none !important;width:44px !important;height:40px !important;padding:0 !important;margin:0 !important}
.ink-row[data-lift="true"] .ink-ord{background:${BLUE};box-shadow:none}

/* Footer hint legend, drawn inside the focused row's text column (Steam's own footer legend shows the same actions) */
.ink-hints{position:absolute;left:0;right:0;bottom:0;display:none;flex-wrap:wrap;gap:2px 12px;align-items:center;font-size:13px;color:var(--ink-tx2)}
.ink-row:focus-within .ink-hints,.ink-row.gpfocuswithin .ink-hints,.ink-row.ink-row-f .ink-hints,.ink-row[data-lift="true"] .ink-hints{display:flex}
.ink-hint{display:inline-flex;align-items:center;gap:6px}
.ink-hint[data-off="true"]{opacity:.35}
.ink-gl{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;padding:0 4px;box-sizing:border-box;border-radius:10px;
  border:2px solid currentColor;font-size:11px;font-weight:700;line-height:1}
.ink-moved{display:inline-flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 10px;padding:4px 10px;border-radius:3px;background:rgba(89,191,64,.16);color:#9be07f;font-size:13px;font-weight:600}
.ink-moved[data-tone="warn"]{background:rgba(255,170,0,.16);color:#ffc04d}
.ink-undo{padding:3px 12px !important;font-size:13px}

/* Settings sheet (modal) */
.ink-sheet{box-sizing:border-box;width:min(640px,calc(100vw - 48px));max-width:100%;padding:24px 26px;border-radius:8px;background:#171d25;color:#e4e7ea}
.ink-sheet-h{display:flex;align-items:center;gap:16px;margin:0 0 14px}
.ink-sheet-art{flex:none;width:110px}
.ink-sheet-art .ink-thumb{width:100%} /* the picture is as wide as its box, never wider: it used to run over the title (device) */
.ink-sheet-h>div:not(.ink-sheet-art){flex:1 1 0;min-width:0;overflow-wrap:anywhere}
.ink-sheet-t{margin:0;font-size:24px;font-weight:700;line-height:1.15;color:#fff}
.ink-sheet-m{margin-top:3px;font-size:12px;color:var(--ink-tx3)}
.ink-sheet-f{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}

/* Store "Installed" tab: one compact line above the grid (dev-mode note + small Settings button, or the import field) */
.ink-inst-line{margin:0 0 12px;gap:6px 12px}
.ink-inst-note{font-size:13px;line-height:1.4;color:var(--ink-tx2);min-width:0}
.ink-btn.ink-btn-sm{padding:5px 12px !important;font-size:13px}
.ink-inst-dev{margin:0 0 12px}
.ink-inst-imp{flex-wrap:wrap;align-items:flex-end}
.ink-inst-field{flex:1 1 260px;min-width:0;max-width:100%}

/* Badges and status rows */
.ink-badge{display:inline-flex;align-items:center;gap:4px;padding:1px 7px;border-radius:2px;font-size:11px;font-weight:700;
  letter-spacing:.04em;text-transform:uppercase;line-height:1.5;white-space:nowrap}
.ink-badge-info{background:rgba(26,159,255,.22);color:#6cc0ff}
.ink-badge-ok{background:rgba(89,191,64,.22);color:#8bdc72}
.ink-badge-warn{background:rgba(255,170,0,.22);color:#ffc04d}
.ink-badge-error{background:rgba(255,90,90,.22);color:#ff9a9a}
.ink-badge-muted{background:rgba(255,255,255,.12);color:#c6cacd}
.ink-status{display:flex;align-items:flex-start;gap:12px;padding:8px 0}
.ink-dot{flex:none;width:10px;height:10px;margin-top:5px;border-radius:50%}
.ink-dot-info{background:${BLUE}}.ink-dot-ok{background:#59bf40}.ink-dot-warn{background:#ffaa00}.ink-dot-error{background:#ff5a5a}.ink-dot-muted{background:#8b929a}
.ink-status-t{font-size:14px;font-weight:600}.ink-status-d{font-size:13px;color:#b8bcbf;white-space:pre-wrap}
.ink-meter{height:6px;border-radius:3px;background:rgba(255,255,255,.14);overflow:hidden;margin-top:6px}
.ink-meter>div{height:100%;background:${BLUE}}

/* The one tab strip (Store, Live Screens lists): equal segments in a dark track. Three states that never look alike:
   idle = muted text on the track; selected = raised Steam grey, white text, blue underline (blue is not focus); focused = Steam's white fill (rule above).
   nowrap + flow-children="horizontal" keeps D-pad left / right on the tabs; a long label wraps inside its segment instead of the strip scrolling. */
.ink-tabs{display:flex;flex-wrap:nowrap;gap:4px;box-sizing:border-box;width:100%;max-width:560px;min-width:0;margin:0 0 16px;padding:4px;border-radius:6px;
  background:rgba(0,0,0,.24);box-shadow:inset 0 0 0 1px var(--ink-line)}
.ink-tab{flex:1 1 0 !important;width:auto !important;min-width:0 !important;max-width:none !important;margin:0 !important;padding:10px 14px !important;
  display:inline-flex !important;flex-wrap:wrap;align-items:center;justify-content:center;gap:2px 10px;border-radius:4px !important;
  background:transparent !important;color:var(--ink-tx2) !important;font-size:15px;font-weight:600;line-height:1.25;white-space:normal;text-align:center;
  transition:background .15s ${EASE},color .15s ${EASE}}
.ink-tab:hover{background:rgba(255,255,255,.06) !important;color:#fff !important}
.ink-tab[aria-selected="true"]{background:#3d4450 !important;color:#fff !important;box-shadow:inset 0 -3px 0 ${BLUE}}
.ink-inuse{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:#8bdc72;white-space:nowrap}
.ink-inuse-dot{flex:none;width:8px;height:8px;border-radius:50%;background:#59bf40}
.ink-tab.gpfocus .ink-inuse,.ink-tab:focus-visible .ink-inuse{color:#2e7d1c}
/* Live Screens with two lists: the strip sits with the list it controls, set apart from the toggle above */
.ink-prof{margin-top:26px}
.ink-prof .ink-tabs{margin-bottom:14px}
@media (prefers-reduced-motion:reduce){.ink-tab{transition:none}}

/* Store: capsule cards in a grid, button-hint bar */
.ink-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px;margin:0 0 16px;padding:6px 4px 10px}
.ink-cap{position:relative;display:flex;flex-direction:column;gap:8px;box-sizing:border-box;min-width:0;padding:10px 10px 12px;border-radius:6px;
  background:#1b222c;box-shadow:inset 0 0 0 1px var(--ink-line);cursor:pointer;
  transition:transform .15s ${EASE},box-shadow .15s ${EASE},background .15s ${EASE}}
.ink-cap:hover,.ink-cap:focus-visible,.ink-cap:focus-within,.ink-cap.gpfocus{transform:translateY(-3px);background:#222c39;
  box-shadow:0 0 0 3px #fff,0 12px 22px rgba(0,0,0,.5);z-index:2}
.ink-cap .ink-thumb{width:100%}
.ink-cap-t{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:16px;font-weight:600;line-height:1.25;color:#fff}
.ink-cap-m{font-size:12px;color:var(--ink-tx3)}
.ink-cap-b{display:flex;flex-wrap:wrap;gap:4px}
.ink-keys{display:flex;flex-wrap:wrap;gap:6px 18px;padding:8px 2px;font-size:13px;color:var(--ink-tx2)}
/* Store "not set up yet" empty state: a calm panel, never a red error */
.ink-empty{display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px;box-sizing:border-box;max-width:560px;margin:18px auto;
  padding:32px 28px;border-radius:8px;background:var(--ink-surface);box-shadow:inset 0 0 0 1px var(--ink-line)}
.ink-empty-ico{display:flex;align-items:center;justify-content:center;width:56px;height:56px;border-radius:50%;background:rgba(26,159,255,.16);color:var(--ink-blue-2);font-size:26px}
.ink-empty-t{margin:4px 0 0;font-size:22px;font-weight:700;line-height:1.2;color:#fff}
.ink-empty-d{margin:0 0 6px;font-size:14px;line-height:1.5;color:var(--ink-tx2)}

.ink-card{display:flex;flex-direction:column;gap:10px;box-sizing:border-box;width:100%;max-width:100%;min-width:0;padding:12px 16px;margin:0 0 8px;
  border-radius:4px;background:var(--ink-surface)}
.ink-top{display:flex;flex-wrap:wrap;align-items:flex-start;gap:12px 16px;min-width:0}
.ink-body{flex:1 1 180px;min-width:0;display:flex;flex-direction:column;gap:4px}
.ink-settings{display:flex;flex-direction:column;min-width:0;max-width:100%;padding-top:8px;border-top:1px solid rgba(255,255,255,.1)}

@media (max-width:760px){.ink-row{flex-wrap:wrap}.ink-row-body{flex:1 1 200px}}
@media (prefers-reduced-motion:reduce){
  .ink-skeleton{animation:none}
  .ink-row,.ink-cap,.ink-ord-btn{transition:none}
  .ink-cap:hover,.ink-cap:focus-visible,.ink-cap:focus-within,.ink-cap.gpfocus{transform:none}
}
`;

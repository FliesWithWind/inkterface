---
inclusion: fileMatch
fileMatchPattern: ["decky/src/**/*.ts", "decky/src/**/*.tsx", "decky/test/**"]
---

# Decky UI layout rules

The plugin UI runs inside Steam's Gaming Mode, driven by a controller on a narrow page. Use `@decky/ui` components and
Steam's own palette, fonts and focus. Page-by-page description: #[[file:docs/plugin-ui.md]]

## Layout

- **Do not put a row of buttons inside a Steam `Field`.** The Field's control column does not shrink and, on a page,
  has a minimum width of about 250 px. On a narrow page the non-wrapping line overflows, scrolls sideways, and
  focusing a button slides the page off the left edge.
- Lay out button groups as a **wrapping bar**: a flex container with `flex-wrap: wrap`, `min-width: 0`,
  `max-width: 100%` and `justify-content: flex-start`, holding `DialogButton`s with `flex: 0 1 auto`, `min-width: 0`
  and `width: auto`. No negative margins and no fixed widths.
- Every page leaves room for Steam's footer bar.

## Controller

- Every action is reachable with the controller. Rows that can be reordered support L1/R1 to move the focused row and X for Reorder mode.
- Steam derives the D-pad layout of a container from its CSS: a row with `flex-wrap: nowrap` is "horizontal". Keep
  that in mind before changing the wrapping of a focusable row (see the comment in `decky/src/components/styles.ts`).

## State and errors

- Show backend errors in the UI. Do not swallow them.
- Secrets (Wi-Fi password, Steam API key) are set or removed through `SecretField` and never read back: the backend
  only reports "set" or "unset".

## Tests

Components stay testable with the mocks in `decky/test/mocks/` (`@decky/api`, `@decky/ui`). A layout rule that broke once gets a test.

// Defaults taken from the system locale (navigator.language), so setup needs no typing.

/** Countries that show temperatures in °F and speeds in mph. */
const IMPERIAL = new Set(["US", "LR", "BS", "BZ", "KY", "PW"]);

/** "en-US" -> "imperial"; "pl-PL", "de" and anything unreadable -> "metric". */
export function unitsForLocale(locale: string | undefined | null): "metric" | "imperial" {
  const region = /^[A-Za-z]{2,3}[-_]([A-Za-z]{2})\b/.exec(locale ?? "")?.[1]?.toUpperCase();
  return region && IMPERIAL.has(region) ? "imperial" : "metric";
}

/** "pl-PL" -> "pl". Falls back to "en"; this is only a hint for the place-name search. */
export function languageOf(locale: string | undefined | null): string {
  const l = /^([A-Za-z]{2,3})(?:[-_]|$)/.exec(locale ?? "")?.[1]?.toLowerCase();
  return l ?? "en";
}

export const systemLocale = (): string | undefined => (globalThis as { navigator?: { language?: string } }).navigator?.language;

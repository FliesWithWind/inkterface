import { describe, expect, it } from "vitest";
import { languageOf, unitsForLocale } from "../src/locale";

describe("units from the system locale", () => {
  it.each([["en-US", "imperial"], ["en_US", "imperial"], ["es-US", "imperial"], ["en-LR", "imperial"], ["en-GB", "metric"], ["pl-PL", "metric"], ["de", "metric"],
    ["en", "metric"], ["", "metric"], [undefined, "metric"], [null, "metric"], ["garbage", "metric"], ["en-us", "imperial"]])("%s -> %s", (l, u) => {
    expect(unitsForLocale(l as string)).toBe(u);
  });
});

describe("search language from the system locale", () => {
  it.each([["pl-PL", "pl"], ["en-US", "en"], ["DE", "de"], ["zh_CN", "zh"], ["", "en"], [undefined, "en"], ["1234", "en"]])("%s -> %s", (l, lang) => {
    expect(languageOf(l as string)).toBe(lang);
  });
});

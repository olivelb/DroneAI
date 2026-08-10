import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOCALE,
  EN_MESSAGES,
  formatMessage,
  FR_MESSAGES,
  SUPPORTED_LOCALES,
} from "./catalog";

describe("translation catalog", () => {
  it("defaults to English and exposes only supported locales", () => {
    expect(DEFAULT_LOCALE).toBe("en");
    expect(SUPPORTED_LOCALES).toEqual(["en", "fr"]);
  });

  it("keeps every catalog complete and non-empty", () => {
    expect(Object.keys(FR_MESSAGES).sort()).toEqual(
      Object.keys(EN_MESSAGES).sort(),
    );
    expect(Object.values(EN_MESSAGES).every(Boolean)).toBe(true);
    expect(Object.values(FR_MESSAGES).every(Boolean)).toBe(true);
  });

  it("formats variables in both locales", () => {
    expect(formatMessage("en", "launch.queued", { mission: "P4" })).toContain(
      "Mission P4 queued",
    );
    expect(formatMessage("fr", "search.results", { count: 3 })).toBe(
      "3 résultat(s)",
    );
    expect(
      formatMessage("en", "map.unavailable", { error: "missing" }),
    ).toBe("Map unavailable: missing");
    expect(
      formatMessage("fr", "map.unavailable", { error: "absente" }),
    ).toBe("Carte indisponible : absente");
  });
});

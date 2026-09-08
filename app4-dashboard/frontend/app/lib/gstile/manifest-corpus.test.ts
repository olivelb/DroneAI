import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeGsTileManifest } from "./contracts";

const directory = resolve(process.cwd(), "../../tests/fixtures/gstile/manifest-contracts");
describe("shared manifest corpus", () => {
  for (const name of readdirSync(directory).filter(name => name.endsWith(".json"))) {
    it(name, () => {
      const decode = () => decodeGsTileManifest(JSON.parse(readFileSync(resolve(directory, name), "utf8")));
      if (name.endsWith("-invalid.json")) expect(decode).toThrow();
      else expect(decode).not.toThrow();
    });
  }
});

import { cp } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
for (let index = 0; index < args.length; index += 2) {
  if (args[index] === "--hostname") process.env.HOSTNAME = args[index + 1];
  else if (args[index] === "--port") process.env.PORT = args[index + 1];
  else throw new Error(`Unsupported start option: ${args[index]}`);
}
process.env.HOSTNAME ||= "0.0.0.0";
await cp("public", ".next/standalone/public", { recursive: true });
await cp(".next/static", ".next/standalone/.next/static", { recursive: true });
await import(pathToFileURL(resolve(".next/standalone/server.js")).href);

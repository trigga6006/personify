import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";

const envPath = ".env";
const examplePath = ".env.example";
const force = process.argv.includes("--force");

if (existsSync(envPath) && !force) {
  console.log(".env already exists; leaving it unchanged. Use -- --force to regenerate.");
  process.exit(0);
}

if (!existsSync(examplePath)) {
  console.error("Missing .env.example; run this from the repo root.");
  process.exit(1);
}

const password = randomBytes(24).toString("base64url");
let content = readFileSync(examplePath, "utf8");

content = content.replace(
  /^PERSONIFY_DB_PASSWORD=.*$/m,
  `PERSONIFY_DB_PASSWORD=${password}`,
);
content = content.replace(
  /^PERSONIFY_DB_URL=.*$/m,
  `PERSONIFY_DB_URL=postgresql+psycopg://personify:${password}@127.0.0.1:5544/personify`,
);

writeFileSync(envPath, content);
console.log("Wrote .env with a generated local database password.");

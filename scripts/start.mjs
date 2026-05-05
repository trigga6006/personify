import { existsSync } from "node:fs";
import { run, venvPython } from "./lib.mjs";

run("node", ["scripts/setup-env.mjs"]);

if (!existsSync(venvPython)) {
  console.error("Missing .venv. Run `npm run setup` first.");
  process.exit(1);
}

if (!existsSync("frontend/node_modules")) {
  run("npm", ["--prefix", "frontend", "install"]);
}

run(venvPython, ["-m", "personify.cli", "dev"]);

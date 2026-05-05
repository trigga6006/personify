import { existsSync } from "node:fs";
import { run, ensureVenv, venvPython } from "./lib.mjs";

run("node", ["scripts/setup-env.mjs"]);
ensureVenv();

run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"]);
run(venvPython, ["-m", "pip", "install", "-e", ".[dev]"]);

if (!existsSync("frontend/node_modules")) {
  run("npm", ["--prefix", "frontend", "install"]);
}

run("docker", ["compose", "up", "-d"]);
run(venvPython, ["-m", "personify.cli", "init"]);

console.log("");
console.log("Setup complete. Run `npm start` to open the local app.");

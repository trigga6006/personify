import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

export const isWindows = process.platform === "win32";
export const venvPython = isWindows ? ".venv\\Scripts\\python.exe" : ".venv/bin/python";

export function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: isWindows,
    ...options,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

export function ensureVenv() {
  if (!existsSync(venvPython)) {
    const [command, args] = resolvePython();
    run(command, [...args, "-m", "venv", ".venv"]);
  }
}

export function resolvePython() {
  const candidates = isWindows
    ? [
        ["py", ["-3"]],
        ["python", []],
      ]
    : [
        ["python3", []],
        ["python", []],
      ];

  for (const [command, args] of candidates) {
    const result = spawnSync(command, [...args, "--version"], {
      stdio: "ignore",
      shell: isWindows,
    });
    if (result.status === 0) {
      return [command, args];
    }
  }

  console.error("Python 3.11+ is required but was not found on PATH.");
  process.exit(1);
}

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

export function ensureDockerRunning() {
  const result = spawnSync("docker", ["info"], {
    encoding: "utf8",
    shell: isWindows,
    stdio: "pipe",
  });

  if (result.status === 0) {
    return;
  }

  const output = `${result.stdout ?? ""}\n${result.stderr ?? ""}`.toLowerCase();
  if (result.error?.code === "ENOENT") {
    console.error("Docker was not found. Install Docker Desktop, start it, then run this command again.");
  } else if (
    output.includes("dockerdesktop") ||
    output.includes("cannot connect to the docker daemon") ||
    output.includes("docker daemon is not running") ||
    output.includes("pipe/docker")
  ) {
    console.error("Docker Desktop is not running. Start Docker Desktop and wait until the engine is running, then run this command again.");
  } else {
    console.error("Docker is not ready. Start Docker Desktop and try again.");
    const details = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim();
    if (details) {
      console.error(details);
    }
  }
  process.exit(1);
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

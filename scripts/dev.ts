import { existsSync } from "node:fs";
import { resolve } from "node:path";

const ROOT = resolve(import.meta.dir, "..");

function detectPythonPath(): string | null {
  const candidates = [
    process.env.PYTHON_PATH,
    resolve(ROOT, ".venv/Scripts/python.exe"),
    resolve(ROOT, ".venv/Scripts/python"),
    resolve(ROOT, ".venv/bin/python"),
  ].filter((value): value is string => Boolean(value));

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }

  return null;
}

const env = {
  ...process.env,
  HOST: process.env.HOST ?? "0.0.0.0",
  PORT: process.env.PORT ?? "5050",
  AUTO_OPEN_BROWSER: process.env.AUTO_OPEN_BROWSER ?? "0",
};

const pythonPath = detectPythonPath();
if (pythonPath) {
  env.PYTHON_PATH = pythonPath;
  console.log(`[frag-demo] Using Python: ${pythonPath}`);
} else {
  console.warn("[frag-demo] No local .venv Python found; falling back to PATH.");
}

const server = Bun.spawn(["bun", "--watch", "server/index.ts"], {
  cwd: ROOT,
  env,
  stdout: "inherit",
  stderr: "inherit",
  stdin: "inherit",
});

const client = Bun.spawn(
  ["bunx", "--bun", "vite", "--host", env.HOST, "--port", "5000", "--strictPort"],
  {
    cwd: ROOT,
    env,
    stdout: "inherit",
    stderr: "inherit",
    stdin: "inherit",
  },
);

let shuttingDown = false;

function terminate(signal: NodeJS.Signals | number): void {
  if (shuttingDown) return;
  shuttingDown = true;
  server.kill(signal);
  client.kill(signal);
}

process.on("SIGINT", () => terminate("SIGINT"));
process.on("SIGTERM", () => terminate("SIGTERM"));

const [serverExit, clientExit] = await Promise.all([server.exited, client.exited]);
terminate("SIGTERM");

if (serverExit !== 0) process.exit(serverExit);
if (clientExit !== 0) process.exit(clientExit);

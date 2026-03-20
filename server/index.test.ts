// @vitest-environment node

import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

type RunningServer = {
  baseUrl: string;
  logs: string[];
  stop: () => Promise<void>;
};

const ROOT = fileURLToPath(new URL("..", import.meta.url));

async function getFreePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Failed to determine free port"));
        return;
      }
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
    server.on("error", reject);
  });
}

async function waitForServer(baseUrl: string, child: ReturnType<typeof spawn>, logs: string[]): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`Server exited early.\n${logs.join("")}`);
    }

    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) {
        return;
      }
      lastError = new Error(`Unexpected status: ${response.status}`);
    } catch (error) {
      lastError = error;
    }

    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error(`Server did not become ready: ${String(lastError)}\n${logs.join("")}`);
}

async function startServer(): Promise<RunningServer> {
  const port = await getFreePort();
  const stateDir = await mkdtemp(join(tmpdir(), "frag-demo-state-"));
  const logs: string[] = [];
  const child = spawn(process.env.BUN_BIN ?? "bun", ["server/index.ts"], {
    cwd: ROOT,
    env: {
      ...process.env,
      AUTO_OPEN_BROWSER: "0",
      PORT: String(port),
      FRAG_DEMO_ROOT: ROOT,
      FRAG_DEMO_STATE_DIR: stateDir,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout.on("data", (chunk) => {
    logs.push(String(chunk));
  });
  child.stderr.on("data", (chunk) => {
    logs.push(String(chunk));
  });

  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForServer(baseUrl, child, logs);

  return {
    baseUrl,
    logs,
    stop: async () => {
      child.kill("SIGTERM");
      await new Promise((resolve) => setTimeout(resolve, 250));
      if (child.exitCode === null) {
        child.kill("SIGKILL");
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      await rm(stateDir, { recursive: true, force: true });
    },
  };
}

describe("Bun server integration routes", () => {
  it("reports health and persists safe config payloads", async () => {
    const running = await startServer();
    try {
    const health = await fetch(`${running.baseUrl}/api/health`);
    expect(health.status).toBe(200);
    await expect(health.json()).resolves.toMatchObject({ status: "ok" });

    const save = await fetch(`${running.baseUrl}/api/config`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        server_url: "http://127.0.0.1:3000",
        api_key: "supersecretkey",
        watch_dir: "/tmp/demos",
        auto_upload: true,
        default_event_id: 9,
      }),
    });
    expect(save.status).toBe(200);
    await expect(save.json()).resolves.toMatchObject({
      ok: true,
      config: {
        server_url: "http://127.0.0.1:3000",
        api_key: "supersec...",
        api_key_set: true,
        watch_dir: "/tmp/demos",
        auto_upload: true,
        default_event_id: 9,
      },
    });

    const config = await fetch(`${running.baseUrl}/api/config`);
    expect(config.status).toBe(200);
    await expect(config.json()).resolves.toMatchObject({
      server_url: "http://127.0.0.1:3000",
      api_key: "supersec...",
      api_key_set: true,
      watch_dir: "/tmp/demos",
      auto_upload: true,
      default_event_id: 9,
    });
    } finally {
      await running.stop();
    }
  }, 20_000);

  it("discovers watched demos and persists the selected demo path", async () => {
    const running = await startServer();
    const watchDir = await mkdtemp(join(tmpdir(), "frag-demo-watch-"));
    try {
      const nestedDir = join(watchDir, "nested");
      await mkdir(nestedDir, { recursive: true });

      const oldDemo = join(watchDir, "old.dem");
      const newDemo = join(nestedDir, "new.dem");
      await writeFile(oldDemo, "");
      await writeFile(newDemo, "");
      await utimes(oldDemo, 1_700_000_000, 1_700_000_000);
      await utimes(newDemo, 1_700_000_100, 1_700_000_100);

      const addFolder = await fetch(`${running.baseUrl}/api/library/watch/add`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ folder_path: watchDir }),
      });
      expect(addFolder.status).toBe(200);

      const selectDemo = await fetch(`${running.baseUrl}/api/library/select`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ demo_path: oldDemo }),
      });
      expect(selectDemo.status).toBe(200);

      const library = await fetch(`${running.baseUrl}/api/library`);
      expect(library.status).toBe(200);
      await expect(library.json()).resolves.toMatchObject({
        ok: true,
        selected_demo_path: oldDemo,
        watched_folders: [{ path: watchDir, recursive: true, exists: true }],
        discovered_demos: [{ path: newDemo }, { path: oldDemo }],
      });
    } finally {
      await rm(watchDir, { recursive: true, force: true });
      await running.stop();
    }
  }, 20_000);
});

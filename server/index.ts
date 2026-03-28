import { Elysia } from "elysia";
import { cors } from "@elysiajs/cors";
import chokidar, { type FSWatcher } from "chokidar";
import { existsSync, readdirSync, statSync } from "fs";
import { mkdir, readFile, writeFile } from "fs/promises";
import { basename, dirname, extname, join, normalize, resolve } from "path";

type JsonRecord = Record<string, unknown>;
type KillRecord = Record<string, unknown>;

type UiState = {
  watched_folders: Array<{ path: string; recursive: boolean }>;
  recent_demos: Array<{ path: string; last_loaded_at: string }>;
  selected_demo_path: string | null;
};

type Config = {
  server_url: string;
  api_key: string;
  watch_dir: string;
  auto_upload: boolean;
  default_event_id: number;
};

type LoadedDemo = {
  demoPath: string;
  header: JsonRecord;
  kills: KillRecord[];
  playerSlots: Record<string, number>;
  players: string[];
  weapons: string[];
  rounds: number[];
  totalKills: number;
};

type AutoEncodeState = {
  running: boolean;
  eventId: number;
  lastResult: JsonRecord | null;
};

const ROOT = resolve(process.env.FRAG_DEMO_ROOT ?? resolve(import.meta.dir, ".."));
const STATE_DIR = resolve(process.env.FRAG_DEMO_STATE_DIR ?? resolve(ROOT, ".frag-demo"));
const UI_STATE_PATH = resolve(STATE_DIR, "ui_state.json");
const CONFIG_PATH = resolve(STATE_DIR, "config.json");
const DIST_DIR = resolve(process.env.FRAG_DEMO_DIST_DIR ?? resolve(ROOT, "dist/client"));
const DIST_INDEX_PATH = resolve(DIST_DIR, "index.html");
const MAX_RECENT_DEMOS = 50;
const MAX_DISCOVERED_DEMOS = 1000;

const runtime = {
  loadedDemo: null as LoadedDemo | null,
  watcher: null as FSWatcher | null,
  watcherLastFile: null as string | null,
  lastUpload: null as JsonRecord | null,
  cs2JobRunning: false,
  autoEncode: {
    running: false,
    eventId: 0,
    lastResult: null,
  } as AutoEncodeState,
};

function defaultUiState(): UiState {
  return {
    watched_folders: [],
    recent_demos: [],
    selected_demo_path: null,
  };
}

function defaultConfig(): Config {
  return {
    server_url: "",
    api_key: "",
    watch_dir: "",
    auto_upload: false,
    default_event_id: 1,
  };
}

function pathKey(input: string): string {
  return normalize(input).replaceAll("\\", "/").toLowerCase();
}

function utcIsoNow(): string {
  return new Date().toISOString();
}

function parseBoolean(value: unknown, fallback: boolean | null = false): boolean | null {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && (value === 0 || value === 1)) return Boolean(value);
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "on"].includes(normalized)) return true;
    if (["false", "0", "no", "off"].includes(normalized)) return false;
  }
  throw new Error("must be a boolean");
}

function parseInteger(value: unknown, fallback: number): number {
  const parsed = Number.parseInt(String(value ?? fallback), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseFloatValue(value: unknown, fallback: number): number {
  const parsed = Number.parseFloat(String(value ?? fallback));
  return Number.isFinite(parsed) ? parsed : fallback;
}

function sanitizeForJson(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.map((item) => sanitizeForJson(item));
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key,
      sanitizeForJson(item),
    ]);
    return Object.fromEntries(entries);
  }
  return String(value);
}

async function ensureStateDir(): Promise<void> {
  await mkdir(STATE_DIR, { recursive: true });
}

async function loadUiState(): Promise<UiState> {
  try {
    const raw = await readFile(UI_STATE_PATH, "utf-8");
    const loaded = JSON.parse(raw) as Partial<UiState>;
    const watched = Array.isArray(loaded.watched_folders)
      ? loaded.watched_folders
          .filter((entry) => Boolean(entry && typeof entry.path === "string"))
          .map((entry) => ({ path: entry.path, recursive: entry.recursive !== false }))
      : [];
    const recent = Array.isArray(loaded.recent_demos)
      ? loaded.recent_demos
          .filter((entry) => Boolean(entry && typeof entry.path === "string"))
          .map((entry) => ({
            path: entry.path,
            last_loaded_at: typeof entry.last_loaded_at === "string" ? entry.last_loaded_at : utcIsoNow(),
          }))
      : [];
    return {
      watched_folders: watched,
      recent_demos: recent,
      selected_demo_path: typeof loaded.selected_demo_path === "string" ? loaded.selected_demo_path : null,
    };
  } catch {
    return defaultUiState();
  }
}

async function saveUiState(state: UiState): Promise<void> {
  await ensureStateDir();
  await writeFile(UI_STATE_PATH, JSON.stringify(state, null, 2), "utf-8");
}

async function loadConfig(): Promise<Config> {
  try {
    const raw = await readFile(CONFIG_PATH, "utf-8");
    const loaded = JSON.parse(raw) as Partial<Config>;
    return {
      server_url: typeof loaded.server_url === "string" ? loaded.server_url.trim() : "",
      api_key: typeof loaded.api_key === "string" ? loaded.api_key.trim() : "",
      watch_dir: typeof loaded.watch_dir === "string" ? loaded.watch_dir.trim() : "",
      auto_upload: Boolean(parseBoolean(loaded.auto_upload, false)),
      default_event_id: Math.max(1, parseInteger(loaded.default_event_id, 1)),
    };
  } catch {
    return defaultConfig();
  }
}

async function saveConfig(config: Config): Promise<void> {
  await ensureStateDir();
  await writeFile(CONFIG_PATH, JSON.stringify(config, null, 2), "utf-8");
}

function safeConfigPayload(config: Config): JsonRecord {
  return {
    ...config,
    api_key: config.api_key ? `${config.api_key.slice(0, 8)}...` : "",
    api_key_set: Boolean(config.api_key),
  };
}

function isDemoPath(input: string): boolean {
  return input.trim().toLowerCase().endsWith(".dem");
}

function normalizeExistingPath(input: string): string {
  return resolve(input);
}

function upsertRecentDemo(state: UiState, demoPath: string): void {
  const demoKey = pathKey(demoPath);
  const recent = state.recent_demos.filter((item) => pathKey(item.path) !== demoKey);
  recent.unshift({ path: demoPath, last_loaded_at: utcIsoNow() });
  state.recent_demos = recent.slice(0, MAX_RECENT_DEMOS);
}

function recentDemosPayload(recentDemos: UiState["recent_demos"]): JsonRecord[] {
  return recentDemos
    .filter((entry) => Boolean(entry.path))
    .map((entry) => {
      const fullPath = entry.path;
      return {
        name: basename(fullPath),
        path: fullPath,
        folder_path: dirname(fullPath),
        last_loaded_at: entry.last_loaded_at,
        exists: existsSync(fullPath),
      };
    });
}

function listDemoFiles(dirPath: string, recursive: boolean): string[] {
    const results: string[] = [];
  let entries: Array<{ name: string; isDirectory(): boolean; isFile(): boolean }>;
  try {
    entries = readdirSync(dirPath, { withFileTypes: true }) as unknown as Array<{
      name: string;
      isDirectory(): boolean;
      isFile(): boolean;
    }>;
  } catch {
    return results;
  }

  for (const entry of entries) {
    const fullPath = join(dirPath, entry.name);
    if (entry.isDirectory()) {
      if (recursive) results.push(...listDemoFiles(fullPath, true));
      continue;
    }
    if (entry.isFile() && extname(entry.name).toLowerCase() === ".dem") {
      results.push(fullPath);
    }
  }
  return results;
}

async function buildLibraryPayload(): Promise<JsonRecord> {
  const uiState = await loadUiState();

  const foldersPayload: JsonRecord[] = [];
  const discovered = new Map<string, JsonRecord>();

  for (const folder of uiState.watched_folders) {
    const folderPath = String(folder.path);
    const recursive = folder.recursive !== false;
    let exists = false;
    try {
      exists = statSync(folderPath).isDirectory();
    } catch {
      exists = false;
    }

    foldersPayload.push({
      path: folderPath,
      recursive,
      exists,
    });

    if (!exists) continue;

    for (const demoPath of listDemoFiles(folderPath, recursive)) {
      try {
        const stats = statSync(demoPath);
        const normalized = resolve(demoPath);
        const payload = {
          name: basename(normalized),
          path: normalized,
          folder_path: dirname(normalized),
          modified_ts: stats.mtimeMs / 1000,
          size_mb: Math.round((stats.size / (1024 * 1024)) * 10) / 10,
          exists: true,
        };
        const key = pathKey(normalized);
        const existing = discovered.get(key) as { modified_ts?: number } | undefined;
        if (!existing || (payload.modified_ts as number) > (existing.modified_ts ?? 0)) {
          discovered.set(key, payload);
        }
      } catch {
        continue;
      }
    }
  }

  const discoveredDemos = Array.from(discovered.values())
    .sort((a, b) => Number(b.modified_ts ?? 0) - Number(a.modified_ts ?? 0))
    .slice(0, MAX_DISCOVERED_DEMOS);

  return {
    ok: true,
    watched_folders: foldersPayload,
    recent_demos: recentDemosPayload(uiState.recent_demos),
    discovered_demos: discoveredDemos,
    selected_demo_path: uiState.selected_demo_path,
    scanned_at: utcIsoNow(),
  };
}

function normalizeSide(side: string): string | null {
  const normalized = side.trim().toUpperCase();
  if (normalized === "CT") return "CT";
  if (normalized === "T" || normalized === "TERRORIST") return "TERRORIST";
  return null;
}

function filterKills(kills: KillRecord[], filters: JsonRecord): KillRecord[] {
  let filtered = [...kills];
  const player = typeof filters.player === "string" && filters.player.trim() ? filters.player.trim().toLowerCase() : null;
  const weapon = typeof filters.weapon === "string" && filters.weapon.trim() ? filters.weapon.trim().toLowerCase() : null;
  const side = typeof filters.side === "string" && filters.side.trim() ? normalizeSide(filters.side) ?? filters.side.trim().toUpperCase() : null;
  const roundNum = filters.round_num == null ? null : parseInteger(filters.round_num, NaN);
  const roundStart = filters.round_start == null ? null : parseInteger(filters.round_start, NaN);
  const roundEnd = filters.round_end == null ? null : parseInteger(filters.round_end, NaN);
  let headshot: boolean | null = null;
  try {
    headshot = parseBoolean(filters.headshot, null);
  } catch {
    headshot = null;
  }

  if (player) {
    filtered = filtered.filter((kill) => String(kill.attacker_name ?? "").toLowerCase().includes(player));
  }

  if (weapon) {
    const parts = weapon.replaceAll(",", "|").split("|").map((part) => part.trim()).filter(Boolean);
    filtered = filtered.filter((kill) => {
      const value = String(kill.weapon ?? "").toLowerCase();
      return parts.some((part) => value.includes(part));
    });
  }

  if (headshot !== null) {
    filtered = filtered.filter((kill) => Boolean(kill.headshot) === headshot);
  }

  if (roundNum !== null && Number.isFinite(roundNum)) {
    filtered = filtered.filter((kill) => Number(kill.total_rounds_played ?? -1) === roundNum);
  } else {
    let start = roundStart;
    let end = roundEnd;
    if (start !== null && end !== null && Number.isFinite(start) && Number.isFinite(end) && start > end) {
      [start, end] = [end, start];
    }
    if (start !== null && Number.isFinite(start)) {
      filtered = filtered.filter((kill) => Number(kill.total_rounds_played ?? -1) >= start);
    }
    if (end !== null && Number.isFinite(end)) {
      filtered = filtered.filter((kill) => Number(kill.total_rounds_played ?? -1) <= end);
    }
  }

  if (side) {
    filtered = filtered.filter((kill) => String(kill.attacker_team_name ?? "").toUpperCase() === side);
  }

  return filtered;
}

function pythonExecutable(): string {
  const candidates = [
    process.env.PYTHON_PATH,
    resolve(ROOT, ".venv/Scripts/python.exe"),
    resolve(ROOT, ".venv/Scripts/python"),
    resolve(ROOT, ".venv/bin/python"),
    "py",
    "python3",
    "python",
  ].filter((value): value is string => Boolean(value));

  for (const candidate of candidates) {
    if (candidate.includes("/") || candidate.includes("\\")) {
      if (existsSync(candidate)) return candidate;
      continue;
    }
    return candidate;
  }
  return "python3";
}

async function runWorker<T extends JsonRecord>(command: string, payload: JsonRecord): Promise<T> {
  const proc = Bun.spawn([pythonExecutable(), "-m", "frag_demo.worker", command], {
    cwd: ROOT,
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
    },
  });

  proc.stdin.write(JSON.stringify(payload));
  proc.stdin.end();

  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);

  let parsed: T;
  try {
    parsed = JSON.parse(stdout || "{}") as T;
  } catch {
    throw new Error(stderr || "Worker returned invalid JSON");
  }

  if (exitCode !== 0) {
    throw new Error(String(parsed.error ?? stderr ?? "Worker failed"));
  }

  return parsed;
}

async function choosePath(kind: "file" | "folder"): Promise<string> {
  if (process.platform === "darwin") {
    const script =
      kind === "file"
        ? 'POSIX path of (choose file with prompt "Select CS2 Demo")'
        : 'POSIX path of (choose folder with prompt "Select Demo Watch Folder")';
    const proc = Bun.spawn(["osascript", "-e", script], { stdout: "pipe", stderr: "pipe" });
    const [stdout, exitCode] = await Promise.all([
      new Response(proc.stdout).text(),
      proc.exited,
    ]);
    if (exitCode === 0 && stdout.trim()) return stdout.trim();
    throw new Error("No selection made.");
  }

  if (process.platform === "win32") {
    const command =
      kind === "file"
        ? "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='Demo files (*.dem)|*.dem|All files (*.*)|*.*'; if($d.ShowDialog() -eq 'OK'){Write-Output $d.FileName}"
        : "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}";
    const proc = Bun.spawn(["powershell", "-Command", command], { stdout: "pipe", stderr: "pipe" });
    const [stdout, exitCode] = await Promise.all([
      new Response(proc.stdout).text(),
      proc.exited,
    ]);
    if (exitCode === 0 && stdout.trim()) return stdout.trim();
    throw new Error("No selection made.");
  }

  throw new Error("Native browse dialog is not available on this platform.");
}

async function detectCs2Running(): Promise<boolean> {
  if (runtime.cs2JobRunning) return true;

  if (process.platform === "win32") {
    const powershell = Bun.spawn(
      ["powershell", "-Command", "Get-Process cs2 -ErrorAction SilentlyContinue | Select-Object -First 1 Id"],
      { stdout: "pipe", stderr: "pipe" },
    );
    const [stdout, exitCode] = await Promise.all([
      new Response(powershell.stdout).text(),
      powershell.exited,
    ]);
    if (exitCode === 0 && stdout.trim()) return true;

    const tasklist = Bun.spawn(["tasklist", "/fi", "imagename eq cs2.exe", "/nh"], {
      stdout: "pipe",
      stderr: "pipe",
    });
    const tasklistOut = await new Response(tasklist.stdout).text();
    return tasklistOut.toLowerCase().includes("cs2.exe");
  }

  const proc = Bun.spawn(["pgrep", "-f", "cs2"], { stdout: "pipe", stderr: "pipe" });
  const [stdout, exitCode] = await Promise.all([new Response(proc.stdout).text(), proc.exited]);
  return exitCode === 0 && stdout.trim().length > 0;
}

async function uploadDemoToFragStat(args: {
  demoPath: string;
  eventId: number;
  matchId?: number | null;
  mapName?: string | null;
}): Promise<JsonRecord> {
  const config = await loadConfig();
  if (!config.server_url || !config.api_key) {
    throw new Error("No server configured");
  }

  const form = new FormData();
  form.set("eventId", String(args.eventId));
  if (args.matchId != null) form.set("matchId", String(args.matchId));
  if (args.mapName) form.set("mapName", args.mapName);

  const demoFile = Bun.file(args.demoPath);
  const demoBlob = await demoFile.arrayBuffer();
  form.set(
    "file",
    new File([demoBlob], basename(args.demoPath), { type: "application/octet-stream" }),
  );

  const response = await fetch(`${config.server_url.replace(/\/$/, "")}/api/import/demo`, {
    method: "POST",
    headers: {
      "X-API-Key": config.api_key,
    },
    body: form,
  });

  const body = (await response.json()) as JsonRecord;
  if (!response.ok) {
    throw new Error(String(body.error ?? `${response.status} ${response.statusText}`));
  }
  return body;
}

async function fetchFragStat(pathname: string): Promise<JsonRecord | JsonRecord[]> {
  const config = await loadConfig();
  if (!config.server_url) throw new Error("No FRAG-STAT server configured");
  const response = await fetch(`${config.server_url.replace(/\/$/, "")}${pathname}`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as JsonRecord | JsonRecord[];
}

async function fetchUploadStatus(demoFileId: number): Promise<JsonRecord> {
  const config = await loadConfig();
  if (!config.server_url) throw new Error("No FRAG-STAT server configured");
  const response = await fetch(
    `${config.server_url.replace(/\/$/, "")}/api/import/demo/${demoFileId}/status`,
  );
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as JsonRecord;
}

function setLastUpload(payload: JsonRecord | null): void {
  runtime.lastUpload = payload ? { ...payload } : null;
}

async function startWatcher(): Promise<string> {
  if (runtime.watcher) return String((await loadConfig()).watch_dir);

  const config = await loadConfig();
  if (!config.watch_dir) throw new Error("No watch directory configured");

  runtime.watcher = chokidar.watch(config.watch_dir, {
    ignoreInitial: true,
    awaitWriteFinish: {
      stabilityThreshold: 4000,
      pollInterval: 500,
    },
  });

  runtime.watcher.on("add", async (filePath) => {
    if (extname(filePath).toLowerCase() !== ".dem") return;
    runtime.watcherLastFile = basename(filePath);
    const latestConfig = await loadConfig();
    const normalized = resolve(filePath);
    if (!latestConfig.auto_upload) {
      setLastUpload({
        file: basename(normalized),
        status: "detected",
        auto_upload: false,
      });
      return;
    }

    try {
      const result = await uploadDemoToFragStat({
        demoPath: normalized,
        eventId: latestConfig.default_event_id,
      });
      setLastUpload({
        file: basename(normalized),
        status: "uploaded",
        demo_file_id: result.demoFileId,
        server_status: result.status,
        map_id: result.mapId,
        message: result.message,
      });
    } catch (error) {
      setLastUpload({
        file: basename(normalized),
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  });

  return config.watch_dir;
}

async function stopWatcher(): Promise<void> {
  if (!runtime.watcher) return;
  await runtime.watcher.close();
  runtime.watcher = null;
}

function autoEncodeSnapshot(): JsonRecord {
  return {
    auto_encode_running: runtime.autoEncode.running,
    auto_encode_event_id: runtime.autoEncode.eventId,
    last_auto_encode: runtime.autoEncode.lastResult,
  };
}

async function serveFileFrom(pathname: string): Promise<Response> {
  const file = Bun.file(pathname);
  if (!(await file.exists())) return new Response("Not found", { status: 404 });
  return new Response(file);
}

export const app = new Elysia()
  .use(cors())
  .get("/", async () => {
    if (existsSync(DIST_INDEX_PATH)) return serveFileFrom(DIST_INDEX_PATH);
    return new Response("Client bundle not built. Run `bun run dev` or `bun run build:client`.", {
      status: 503,
    });
  })
  .get("/assets/:filename", async ({ params }) => {
    const filePath = resolve(DIST_DIR, "assets", params.filename);
    const assetsDir = resolve(DIST_DIR, "assets");
    if (!filePath.startsWith(assetsDir)) return new Response("Forbidden", { status: 403 });
    return serveFileFrom(filePath);
  })
  .get("/api/health", () => ({ status: "ok", time: utcIsoNow() }))
  .get("/api/status", async () => {
    const response: JsonRecord = {
      loaded: runtime.loadedDemo !== null,
      cs2_running: await detectCs2Running(),
      watcher_running: runtime.watcher !== null,
      last_upload: runtime.lastUpload,
      ...autoEncodeSnapshot(),
    };
    if (runtime.loadedDemo) {
      response.demo_path = runtime.loadedDemo.demoPath;
      response.map = runtime.loadedDemo.header.map_name ?? "?";
    }
    return response;
  })
  .get("/api/browse", async () => {
    try {
      return { ok: true, path: await choosePath("file") };
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .get("/api/browse-folder", async () => {
    try {
      return { ok: true, path: await choosePath("folder") };
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .get("/api/library", () => buildLibraryPayload())
  .get("/api/config", async () => safeConfigPayload(await loadConfig()))
  .post("/api/config", async ({ body, set }) => {
    const incoming = (body ?? {}) as JsonRecord;
    const config = await loadConfig();
    if ("server_url" in incoming) config.server_url = String(incoming.server_url ?? "").trim();
    if ("api_key" in incoming) config.api_key = String(incoming.api_key ?? "").trim();
    if ("watch_dir" in incoming) config.watch_dir = String(incoming.watch_dir ?? "").trim();
    if ("auto_upload" in incoming) {
      try {
        config.auto_upload = Boolean(parseBoolean(incoming.auto_upload, false));
      } catch (error) {
        set.status = 400;
        return { ok: false, error: `Invalid auto_upload: ${error instanceof Error ? error.message : String(error)}` };
      }
    }
    if ("default_event_id" in incoming) {
      const defaultEventId = parseInteger(incoming.default_event_id, 1);
      if (!Number.isFinite(defaultEventId) || defaultEventId < 1) {
        set.status = 400;
        return { ok: false, error: "default_event_id must be a positive integer" };
      }
      config.default_event_id = defaultEventId;
    }
    await saveConfig(config);
    if (runtime.watcher) {
      await stopWatcher();
    }
    return { ok: true, config: safeConfigPayload(config) };
  })
  .post("/api/test-connection", async ({ set }) => {
    try {
      await fetchFragStat("/api/health");
      return { ok: true };
    } catch (error) {
      set.status = 400;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .get("/api/fragstat/events", async ({ set }) => {
    try {
      return { ok: true, events: await fetchFragStat("/api/events") };
    } catch (error) {
      set.status = 400;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .get("/api/fragstat/matches/:eventId", async ({ params, set }) => {
    try {
      return {
        ok: true,
        matches: await fetchFragStat(`/api/matches/event/${params.eventId}`),
      };
    } catch (error) {
      set.status = 400;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .post("/api/upload", async ({ body, set }) => {
    const payload = (body ?? {}) as JsonRecord;
    if (!runtime.loadedDemo) {
      set.status = 400;
      return { ok: false, error: "No demo loaded" };
    }

    const config = await loadConfig();
    const eventId = parseInteger(payload.event_id, config.default_event_id);
    const matchId = payload.match_id == null || payload.match_id === "" ? null : parseInteger(payload.match_id, 0);
    const mapName = typeof payload.map_name === "string" && payload.map_name.trim() ? payload.map_name.trim() : null;

    try {
      const result = await uploadDemoToFragStat({
        demoPath: runtime.loadedDemo.demoPath,
        eventId,
        matchId: matchId && matchId > 0 ? matchId : null,
        mapName,
      });
      setLastUpload({
        file: basename(runtime.loadedDemo.demoPath),
        status: "uploaded",
        demo_file_id: result.demoFileId,
        server_status: result.status,
        map_id: result.mapId,
        message: result.message,
      });
      return {
        ok: true,
        demo_file_id: result.demoFileId,
        status: result.status,
        map_id: result.mapId,
        message: result.message,
      };
    } catch (error) {
      setLastUpload({
        file: basename(runtime.loadedDemo.demoPath),
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
      set.status = 502;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .get("/api/upload/status", async () => {
    if (!runtime.lastUpload) return { status: "none" };
    const demoFileId = runtime.lastUpload.demo_file_id;
    if (typeof demoFileId === "number") {
      try {
        const status = await fetchUploadStatus(demoFileId);
        return {
          file: runtime.lastUpload.file,
          status: status.status,
          error: status.error,
          map_id: status.mapId,
          demo_file_id: status.id,
          uploaded_at: status.uploadedAt,
          parsed_at: status.parsedAt,
        };
      } catch {
        return runtime.lastUpload;
      }
    }
    return runtime.lastUpload;
  })
  .post("/api/watcher/start", async ({ set }) => {
    try {
      const watchDir = await startWatcher();
      return { ok: true, watch_dir: watchDir };
    } catch (error) {
      set.status = 400;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .post("/api/watcher/stop", async () => {
    await stopWatcher();
    return { ok: true };
  })
  .get("/api/watcher/status", async () => {
    const config = await loadConfig();
    return {
      running: runtime.watcher !== null,
      watch_dir: config.watch_dir,
      last_file: runtime.watcherLastFile,
      last_upload: runtime.lastUpload,
    };
  })
  .post("/api/library/select", async ({ body, set }) => {
    const payload = (body ?? {}) as JsonRecord;
    const rawDemoPath = String(payload.demo_path ?? "").trim();
    if (!rawDemoPath) {
      set.status = 400;
      return { ok: false, error: "No demo path provided." };
    }
    if (!isDemoPath(rawDemoPath)) {
      set.status = 400;
      return { ok: false, error: "demo_path must end with .dem" };
    }

    const normalized = normalizeExistingPath(rawDemoPath);
    const state = await loadUiState();
    state.selected_demo_path = normalized;
    await saveUiState(state);
    return { ok: true, selected_demo_path: normalized };
  })
  .post("/api/library/watch/add", async ({ body, set }) => {
    const payload = (body ?? {}) as JsonRecord;
    const rawFolderPath = String(payload.folder_path ?? "").trim();
    if (!rawFolderPath) {
      set.status = 400;
      return { ok: false, error: "No folder path provided." };
    }
    const normalized = normalizeExistingPath(rawFolderPath);
    try {
      if (!statSync(normalized).isDirectory()) {
        set.status = 400;
        return { ok: false, error: "Path is not a directory." };
      }
    } catch {
      set.status = 400;
      return { ok: false, error: "Folder not found." };
    }

    const state = await loadUiState();
    const folderKey = pathKey(normalized);
    if (!state.watched_folders.some((item) => pathKey(item.path) === folderKey)) {
      state.watched_folders.push({ path: normalized, recursive: true });
    }
    await saveUiState(state);
    return buildLibraryPayload();
  })
  .post("/api/library/watch/remove", async ({ body, set }) => {
    const payload = (body ?? {}) as JsonRecord;
    const rawFolderPath = String(payload.folder_path ?? "").trim();
    if (!rawFolderPath) {
      set.status = 400;
      return { ok: false, error: "No folder path provided." };
    }
    const normalized = normalizeExistingPath(rawFolderPath);
    const state = await loadUiState();
    state.watched_folders = state.watched_folders.filter((item) => pathKey(item.path) !== pathKey(normalized));
    await saveUiState(state);
    return buildLibraryPayload();
  })
  .post("/api/load", async ({ body, set }) => {
    const payload = (body ?? {}) as JsonRecord;
    const demoPath = String(payload.demo_path ?? "").trim();
    if (!demoPath) {
      set.status = 400;
      return { ok: false, error: "No demo path provided." };
    }

    try {
      const result = await runWorker<JsonRecord>("load", { demo_path: demoPath });
      runtime.loadedDemo = {
        demoPath: String(result.demo_path),
        header: (result.header as JsonRecord) ?? {},
        kills: Array.isArray(result.kills) ? (result.kills as KillRecord[]) : [],
        playerSlots: (result.player_slots as Record<string, number>) ?? {},
        players: Array.isArray(result.players) ? (result.players as string[]) : [],
        weapons: Array.isArray(result.weapons) ? (result.weapons as string[]) : [],
        rounds: Array.isArray(result.rounds) ? (result.rounds as number[]) : [],
        totalKills: Number(result.total_kills ?? 0),
      };

      const state = await loadUiState();
      state.selected_demo_path = runtime.loadedDemo.demoPath;
      upsertRecentDemo(state, runtime.loadedDemo.demoPath);
      await saveUiState(state);

      return {
        ok: true,
        header: sanitizeForJson(runtime.loadedDemo.header),
        total_kills: runtime.loadedDemo.totalKills,
        players: runtime.loadedDemo.players,
        weapons: runtime.loadedDemo.weapons,
        rounds: runtime.loadedDemo.rounds,
      };
    } catch (error) {
      runtime.loadedDemo = null;
      set.status = 500;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .post("/api/kills", ({ body, set }) => {
    if (!runtime.loadedDemo) {
      set.status = 400;
      return { ok: false, error: "No demo loaded." };
    }

    try {
      const filtered = filterKills(runtime.loadedDemo.kills, (body ?? {}) as JsonRecord);
      return {
        ok: true,
        total: filtered.length,
        kills: sanitizeForJson(filtered),
      };
    } catch (error) {
      set.status = 500;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .post("/api/record", async ({ body, set }) => {
    if (!runtime.loadedDemo) {
      set.status = 400;
      return { ok: false, error: "No demo loaded." };
    }

    const payload = (body ?? {}) as JsonRecord;
    const before = parseFloatValue(payload.before, 2.0);
    const after = parseFloatValue(payload.after, 1.0);
    const framerate = parseInteger(payload.framerate, 60);
    const hudMode = typeof payload.hud_mode === "string" && payload.hud_mode.trim() ? payload.hud_mode.trim() : "deathnotices";
    const launch = Boolean(parseBoolean(payload.launch, false));

    try {
      const generated = await runWorker<JsonRecord>("generate_json", {
        demo_path: runtime.loadedDemo.demoPath,
        header: runtime.loadedDemo.header,
        player_slots: runtime.loadedDemo.playerSlots,
        kills: runtime.loadedDemo.kills,
        selected_ids: payload.selected_ids,
        selected_ticks: payload.selected_ticks,
        before,
        after,
        framerate,
        hud_mode: hudMode,
        launch,
      });

      const result: JsonRecord = {
        ok: true,
        sequences_count: generated.sequences_count,
        json_path: generated.json_path,
        launched: false,
      };

      if (!launch) return result;

      if (runtime.cs2JobRunning || (await detectCs2Running())) {
        set.status = 409;
        return {
          ok: false,
          error: "CS2 is already running. Close CS2 first, then try again. (JSON was still updated on disk.)",
          json_path: generated.json_path,
          sequences_count: generated.sequences_count,
        };
      }

      const check = await runWorker<JsonRecord>("check_launch", {
        demo_path: runtime.loadedDemo.demoPath,
      });
      if (!check.ok) {
        set.status = 400;
        return {
          ok: false,
          error: check.error,
          diagnostics: check.diagnostics,
          json_path: generated.json_path,
          sequences_count: generated.sequences_count,
          launched: false,
        };
      }

      runtime.cs2JobRunning = true;
      runtime.autoEncode.eventId += 1;
      runtime.autoEncode.running = true;
      runtime.autoEncode.lastResult = null;

      void runWorker<JsonRecord>("launch_and_encode", {
        demo_path: runtime.loadedDemo.demoPath,
        framerate,
      })
        .then((workerResult) => {
          runtime.autoEncode.eventId += 1;
          runtime.autoEncode.running = false;
          runtime.autoEncode.lastResult = {
            ...workerResult,
            event_id: runtime.autoEncode.eventId,
          };
        })
        .catch((error) => {
          runtime.autoEncode.eventId += 1;
          runtime.autoEncode.running = false;
          runtime.autoEncode.lastResult = {
            ok: false,
            encoded: [],
            errors: [],
            error: error instanceof Error ? error.message : String(error),
            event_id: runtime.autoEncode.eventId,
          };
        })
        .finally(() => {
          runtime.cs2JobRunning = false;
        });

      return {
        ...result,
        launched: true,
        diagnostics: check.diagnostics,
      };
    } catch (error) {
      set.status = 400;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .post("/api/encode", async ({ body, set }) => {
    if (!runtime.loadedDemo) {
      set.status = 400;
      return { ok: false, error: "No demo loaded." };
    }

    try {
      const payload = (body ?? {}) as JsonRecord;
      const result = await runWorker<JsonRecord>("encode", {
        demo_path: runtime.loadedDemo.demoPath,
        framerate: parseInteger(payload.framerate, 60),
        concatenate: parseBoolean(payload.concatenate, true),
      });
      if (result.ok === false && result.error) set.status = 400;
      return result;
    } catch (error) {
      set.status = 400;
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
  })
  .post("/api/clean", async ({ set }) => {
    if (!runtime.loadedDemo) {
      set.status = 400;
      return { ok: false, error: "No demo loaded." };
    }
    return runWorker<JsonRecord>("clean", { demo_path: runtime.loadedDemo.demoPath });
  })
  .get("/api/clips", async ({ set }) => {
    if (!runtime.loadedDemo) {
      set.status = 400;
      return { ok: false, error: "No demo loaded." };
    }
    return runWorker<JsonRecord>("clips", { demo_path: runtime.loadedDemo.demoPath });
  })
  .get("/clips/:filename", async ({ params }) => {
    if (!runtime.loadedDemo) return new Response("No demo loaded", { status: 404 });
    const demoDir = dirname(runtime.loadedDemo.demoPath);
    const clipPath = resolve(demoDir, params.filename);
    if (!clipPath.startsWith(demoDir) || extname(clipPath).toLowerCase() !== ".mp4") {
      return new Response("Forbidden", { status: 403 });
    }
    return serveFileFrom(clipPath);
  });

export async function shutdownRuntime(): Promise<void> {
  await stopWatcher();
}

if (import.meta.main) {
  const host = process.env.HOST ?? "0.0.0.0";
  const port = Number(process.env.PORT ?? 5000);
  app.listen({ hostname: host, port });

  const localUrl = `http://127.0.0.1:${port}`;
  const hostUrl = `http://${host}:${port}`;
  console.log(`[frag-demo] Node server running at ${hostUrl} (local ${localUrl})`);

  if (process.env.AUTO_OPEN_BROWSER !== "0") {
    if (process.platform === "darwin") {
      Bun.spawn(["open", localUrl], { stdout: "ignore", stderr: "ignore" });
    } else if (process.platform === "win32") {
      Bun.spawn(["cmd", "/c", "start", localUrl], { stdout: "ignore", stderr: "ignore" });
    }
  }
}

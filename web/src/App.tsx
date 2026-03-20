import {
  startTransition,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react";
import { apiGet, apiPost } from "./api";

type LogLine = {
  id: number;
  message: string;
  className?: string;
};

type WatchFolder = {
  path: string;
  recursive: boolean;
  exists: boolean;
};

type LibraryDemo = {
  name: string;
  path: string;
  folder_path: string;
  modified_ts?: number;
  last_loaded_at?: string;
  size_mb?: number;
  exists: boolean;
};

type LibraryPayload = {
  ok: boolean;
  watched_folders: WatchFolder[];
  recent_demos: LibraryDemo[];
  discovered_demos: LibraryDemo[];
  selected_demo_path: string | null;
  scanned_at: string;
  error?: string;
};

type FragStatConfig = {
  server_url: string;
  api_key: string;
  api_key_set: boolean;
  watch_dir: string;
  auto_upload: boolean;
  default_event_id: number;
};

type DemoHeader = {
  map_name?: string;
  tickrate?: number;
};

type LoadDemoResponse = {
  ok: boolean;
  header?: DemoHeader;
  total_kills?: number;
  players?: string[];
  weapons?: string[];
  rounds?: number[];
  error?: string;
};

type Kill = {
  kill_id?: number;
  tick?: number;
  attacker_name?: string;
  user_name?: string;
  weapon?: string;
  headshot?: boolean;
  total_rounds_played?: number;
  attacker_team_name?: string;
};

type KillsResponse = {
  ok: boolean;
  total?: number;
  kills?: Kill[];
  error?: string;
};

type RecordResponse = {
  ok: boolean;
  sequences_count?: number;
  json_path?: string;
  launched?: boolean;
  diagnostics?: string[];
  error?: string;
};

type EncodeResponse = {
  ok: boolean;
  encoded?: string[];
  concatenated?: string;
  errors?: string[];
  error?: string;
};

type ClipInfo = {
  name: string;
  size_mb: number;
  is_combined: boolean;
};

type ClipsResponse = {
  ok: boolean;
  clips?: ClipInfo[];
  error?: string;
};

type AppStatus = {
  loaded: boolean;
  cs2_running: boolean;
  watcher_running?: boolean;
  auto_encode_running?: boolean;
  auto_encode_event_id?: number;
  last_auto_encode?: {
    event_id?: number;
    ok?: boolean;
    encoded?: string[];
    concatenated?: string;
    errors?: string[];
    error?: string;
  } | null;
  last_upload?: UploadStatus | null;
};

type UploadStatus = {
  file?: string;
  status?: string;
  error?: string;
  demo_file_id?: number;
  server_status?: string;
  map_id?: number;
};

type EventInfo = {
  id: number;
  name: string;
};

type MatchInfo = {
  id: number;
  team1Name?: string;
  team2Name?: string;
};

type FragStatEventsResponse = {
  ok: boolean;
  events?: EventInfo[];
  error?: string;
};

type FragStatMatchesResponse = {
  ok: boolean;
  matches?: MatchInfo[];
  error?: string;
};

type DemoFilters = {
  player: string;
  weapon: string;
  roundStart: string;
  roundEnd: string;
  side: string;
  headshot: boolean;
};

type DemoMeta = {
  header: DemoHeader;
  totalKills: number;
  players: string[];
  weapons: string[];
  rounds: number[];
};

const STATUS_POLL_MS = 5000;
const LIBRARY_POLL_MS = 10000;

function pathKey(path: string | null | undefined): string {
  return String(path || "").replaceAll("\\", "/").toLowerCase();
}

function formatTimestamp(timestamp?: string | null): string {
  if (!timestamp) return "Unknown";
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
}

function formatMtime(seconds?: number): string {
  if (typeof seconds !== "number") return "mtime unknown";
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.getTime()) ? "mtime unknown" : date.toLocaleString();
}

function shortPath(path: string): string {
  if (!path) return "";
  const parts = path.replaceAll("\\", "/").split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return `.../${parts.slice(-2).join("/")}`;
}

function killIdentifier(kill: Kill): number {
  if (typeof kill.kill_id === "number") return kill.kill_id;
  if (typeof kill.tick === "number") return kill.tick;
  return 0;
}

function describeLastUpload(lastUpload: UploadStatus | null): string {
  if (!lastUpload) return "Watcher idle.";
  const file = lastUpload.file ? `${lastUpload.file}: ` : "";
  if (lastUpload.status === "error") {
    return `${file}error${lastUpload.error ? ` | ${lastUpload.error}` : ""}`;
  }
  if (lastUpload.status === "uploaded") {
    return `${file}uploaded | ${lastUpload.server_status || lastUpload.status}`;
  }
  if (lastUpload.status === "detected") {
    return `${file}detected | auto-upload disabled`;
  }
  return `${file}${lastUpload.status || "unknown"}`;
}

function buildWatcherStatus(
  running: boolean,
  watchDir: string,
  lastFile: string | null,
  lastUpload: UploadStatus | null,
): string {
  const parts = [running ? "Watcher running" : "Watcher stopped"];
  if (watchDir) parts.push(watchDir);
  if (lastFile) parts.push(`last file ${lastFile}`);
  if (lastUpload) parts.push(describeLastUpload(lastUpload));
  return parts.join(" | ");
}

export function App() {
  const [logs, setLogs] = useState<LogLine[]>([{ id: 1, message: "Ready." }]);
  const [library, setLibrary] = useState<LibraryPayload | null>(null);
  const [selectedDemoPath, setSelectedDemoPath] = useState<string | null>(null);
  const [demoPathInput, setDemoPathInput] = useState("");
  const [watchFolderInput, setWatchFolderInput] = useState("");
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [demoMeta, setDemoMeta] = useState<DemoMeta | null>(null);
  const [filters, setFilters] = useState<DemoFilters>({
    player: "",
    weapon: "",
    roundStart: "",
    roundEnd: "",
    side: "",
    headshot: false,
  });
  const [currentKills, setCurrentKills] = useState<Kill[]>([]);
  const [queue, setQueue] = useState<Map<number, Kill>>(new Map());
  const [clips, setClips] = useState<ClipInfo[]>([]);
  const [activeClipName, setActiveClipName] = useState<string | null>(null);
  const [fragStatConfig, setFragStatConfig] = useState<FragStatConfig>({
    server_url: "",
    api_key: "",
    api_key_set: false,
    watch_dir: "",
    auto_upload: false,
    default_event_id: 1,
  });
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [fragStatEvents, setFragStatEvents] = useState<EventInfo[]>([]);
  const [fragStatMatches, setFragStatMatches] = useState<MatchInfo[]>([]);
  const [selectedEventId, setSelectedEventId] = useState<number | null>(1);
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);
  const [fragStatStatus, setFragStatStatus] = useState("Watcher idle.");
  const [lastWatcherFile, setLastWatcherFile] = useState<string | null>(null);
  const [cs2Running, setCs2Running] = useState(false);
  const [autoEncodeRunning, setAutoEncodeRunning] = useState(false);
  const [lastAutoEncodeEventId, setLastAutoEncodeEventId] = useState(0);
  const [recordBefore, setRecordBefore] = useState("2.0");
  const [recordAfter, setRecordAfter] = useState("1.0");
  const [recordFramerate, setRecordFramerate] = useState("60");
  const [hudMode, setHudMode] = useState("deathnotices");

  const logIdRef = useRef(2);
  const logRef = useRef<HTMLDivElement | null>(null);

  const queuedKills = Array.from(queue.values());
  const selectedCount = queuedKills.length;

  function logOutput(message: string, className = ""): void {
    setLogs((current) => [
      ...current,
      { id: logIdRef.current++, message, className: className || undefined },
    ]);
  }

  useEffect(() => {
    if (!logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const loadLibrary = useEffectEvent(async (silent = false) => {
    try {
      const data = await apiGet<LibraryPayload>("/api/library");
      if (!data.ok) {
        if (!silent) logOutput(`Library error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      setLibrary(data);
      if (data.selected_demo_path) {
        setSelectedDemoPath(data.selected_demo_path);
        setDemoPathInput(data.selected_demo_path);
      }
    } catch (error) {
      if (!silent) {
        logOutput(
          `Library error: ${error instanceof Error ? error.message : String(error)}`,
          "log-error",
        );
      }
    }
  });

  const loadFragStatMatches = useEffectEvent(async (eventId: number | null, silent = false) => {
    if (!eventId) {
      setFragStatMatches([]);
      setSelectedMatchId(null);
      return;
    }
    try {
      const data = await apiGet<FragStatMatchesResponse>(`/api/fragstat/matches/${eventId}`);
      if (!data.ok) {
        if (!silent) logOutput(`Load matches error: ${data.error || "unknown"}`, "log-error");
        setFragStatMatches([]);
        setSelectedMatchId(null);
        return;
      }
      startTransition(() => {
        setFragStatMatches(data.matches || []);
        setSelectedMatchId(null);
      });
    } catch (error) {
      if (!silent) {
        logOutput(
          `Load matches error: ${error instanceof Error ? error.message : String(error)}`,
          "log-error",
        );
      }
    }
  });

  const loadFragStatEvents = useEffectEvent(async (silent = false, preferredEventId?: number | null) => {
    try {
      const data = await apiGet<FragStatEventsResponse>("/api/fragstat/events");
      if (!data.ok) {
        if (!silent) logOutput(`Load events error: ${data.error || "unknown"}`, "log-error");
        setFragStatEvents([]);
        return;
      }
      const events = data.events || [];
      setFragStatEvents(events);
      const chosenEventId =
        preferredEventId && events.some((event) => event.id === preferredEventId)
          ? preferredEventId
          : selectedEventId && events.some((event) => event.id === selectedEventId)
            ? selectedEventId
            : fragStatConfig.default_event_id &&
                events.some((event) => event.id === fragStatConfig.default_event_id)
              ? fragStatConfig.default_event_id
              : events[0]?.id ?? selectedEventId;
      setSelectedEventId(chosenEventId ?? null);
      await loadFragStatMatches(chosenEventId ?? null, true);
    } catch (error) {
      if (!silent) {
        logOutput(
          `Load events error: ${error instanceof Error ? error.message : String(error)}`,
          "log-error",
        );
      }
    }
  });

  const loadFragStatConfig = useEffectEvent(async (silent = false) => {
    try {
      const data = await apiGet<FragStatConfig>("/api/config");
      setFragStatConfig(data);
      setApiKeyInput("");
      setSelectedEventId(data.default_event_id || 1);
      if (data.server_url) {
        await loadFragStatEvents(true, data.default_event_id || 1);
      }
    } catch (error) {
      if (!silent) {
        logOutput(
          `Config error: ${error instanceof Error ? error.message : String(error)}`,
          "log-error",
        );
      }
    }
  });

  const refreshWatcherStatus = useEffectEvent(async (silent = false) => {
    try {
      const data = await apiGet<{
        running: boolean;
        watch_dir: string;
        last_file: string | null;
        last_upload: UploadStatus | null;
      }>("/api/watcher/status");
      setLastWatcherFile(data.last_file);
      setFragStatStatus(buildWatcherStatus(data.running, data.watch_dir, data.last_file, data.last_upload));
    } catch (error) {
      if (!silent) {
        logOutput(
          `Watcher status error: ${error instanceof Error ? error.message : String(error)}`,
          "log-error",
        );
      }
    }
  });

  const loadClips = useEffectEvent(async () => {
    try {
      const data = await apiGet<ClipsResponse>("/api/clips");
      if (!data.ok || !data.clips || data.clips.length === 0) {
        setClips([]);
        setActiveClipName(null);
        return;
      }
      const nextClips = data.clips;
      const combined = nextClips.find((clip) => clip.is_combined);
      startTransition(() => {
        setClips(nextClips);
        setActiveClipName((current) =>
          current && nextClips.some((clip) => clip.name === current)
            ? current
            : (combined || nextClips[0]).name,
        );
      });
    } catch {
      setClips([]);
      setActiveClipName(null);
    }
  });

  const fetchKills = useEffectEvent(async () => {
    try {
      const data = await apiPost<KillsResponse>("/api/kills", {
        player: filters.player || null,
        weapon: filters.weapon || null,
        headshot: filters.headshot ? true : null,
        round_start: filters.roundStart ? Number.parseInt(filters.roundStart, 10) : null,
        round_end: filters.roundEnd ? Number.parseInt(filters.roundEnd, 10) : null,
        side: filters.side || null,
      });
      if (!data.ok) {
        logOutput(`Filter error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      startTransition(() => {
        setCurrentKills(data.kills || []);
      });
    } catch (error) {
      logOutput(
        `Filter error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  });

  const clearLoadedState = useEffectEvent(() => {
    setDemoMeta(null);
    setCurrentKills([]);
    setQueue(new Map());
    setClips([]);
    setActiveClipName(null);
    setAutoEncodeRunning(false);
  });

  const handleAutoEncodeStatus = useEffectEvent(async (data: AppStatus) => {
    const running = Boolean(data.auto_encode_running);
    const eventId = typeof data.auto_encode_event_id === "number" ? data.auto_encode_event_id : 0;
    const lastResult = data.last_auto_encode;

    if (running && (!autoEncodeRunning || eventId !== lastAutoEncodeEventId)) {
      setLastAutoEncodeEventId(eventId);
      logOutput("CS2 finished recording. Auto-encoding clips...", "log-success");
    }

    setAutoEncodeRunning(running);

    if (!running && lastResult) {
      const resultEventId = typeof lastResult.event_id === "number" ? lastResult.event_id : eventId;
      if (resultEventId > lastAutoEncodeEventId) {
        for (const name of lastResult.encoded || []) {
          logOutput(`  Encoded: ${name}`, "log-success");
        }
        if (lastResult.concatenated) {
          logOutput(`  Combined: ${lastResult.concatenated}`, "log-success");
        }
        for (const error of lastResult.errors || []) {
          logOutput(`  ${error}`, "log-error");
        }
        if (lastResult.ok) {
          logOutput("Auto-encoding finished.", "log-success");
          await loadClips();
        } else {
          logOutput(
            `Auto-encoding failed: ${lastResult.error || "see errors above"}`,
            "log-error",
          );
        }
        setLastAutoEncodeEventId(resultEventId);
      }
    }
  });

  const pollStatus = useEffectEvent(async () => {
    try {
      const data = await apiGet<AppStatus>("/api/status");
      if (!data.loaded) {
        clearLoadedState();
      }
      setCs2Running(Boolean(data.cs2_running));
      await handleAutoEncodeStatus(data);
      if ("watcher_running" in data || data.last_upload) {
        setFragStatStatus(
          buildWatcherStatus(
            Boolean(data.watcher_running),
            fragStatConfig.watch_dir,
            lastWatcherFile,
            data.last_upload || null,
          ),
        );
      }
    } catch {
      // ignore poll failures
    }
  });

  useEffect(() => {
    void loadLibrary(true);
    void loadFragStatConfig(true);
    void refreshWatcherStatus(true);
    void pollStatus();

    const statusInterval = window.setInterval(() => {
      void pollStatus();
    }, STATUS_POLL_MS);
    const libraryInterval = window.setInterval(() => {
      void loadLibrary(true);
    }, LIBRARY_POLL_MS);
    return () => {
      window.clearInterval(statusInterval);
      window.clearInterval(libraryInterval);
    };
  }, [loadLibrary, loadFragStatConfig, pollStatus, refreshWatcherStatus]);

  useEffect(() => {
    if (!demoMeta) return;
    void fetchKills();
  }, [demoMeta, filters, fetchKills]);

  useEffect(() => {
    void loadFragStatMatches(selectedEventId, true);
  }, [selectedEventId, loadFragStatMatches]);

  function updateQueue(updater: (current: Map<number, Kill>) => Map<number, Kill>) {
    setQueue((current) => updater(new Map(current)));
  }

  async function selectDemo(path: string, persist = true): Promise<void> {
    setSelectedDemoPath(path || null);
    setDemoPathInput(path || "");
    if (!persist || !path) return;
    try {
      const data = await apiPost<{ ok: boolean; selected_demo_path?: string; error?: string }>(
        "/api/library/select",
        { demo_path: path },
      );
      if (!data.ok) {
        logOutput(`Select demo error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      if (data.selected_demo_path) {
        setSelectedDemoPath(data.selected_demo_path);
        setDemoPathInput(data.selected_demo_path);
      }
    } catch (error) {
      logOutput(
        `Select demo error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function browseDemoFile(): Promise<void> {
    try {
      const data = await apiGet<{ ok: boolean; path?: string }>("/api/browse");
      if (data.ok && data.path) {
        setDemoPathInput(data.path);
      }
    } catch (error) {
      logOutput(`Browse failed: ${error instanceof Error ? error.message : String(error)}`, "log-error");
    }
  }

  async function browseWatchFolder(): Promise<void> {
    try {
      const data = await apiGet<{ ok: boolean; path?: string }>("/api/browse-folder");
      if (data.ok && data.path) {
        setWatchFolderInput(data.path);
      }
    } catch (error) {
      logOutput(
        `Browse folder failed: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function addWatchFolder(): Promise<void> {
    if (!watchFolderInput.trim()) {
      logOutput("Enter a folder path to watch.", "log-error");
      return;
    }
    try {
      const data = await apiPost<LibraryPayload>("/api/library/watch/add", {
        folder_path: watchFolderInput.trim(),
      });
      if (!data.ok) {
        logOutput(`Add watch folder error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      setWatchFolderInput("");
      setLibrary(data);
      logOutput("Added watch folder.", "log-success");
    } catch (error) {
      logOutput(
        `Add watch folder error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function removeWatchFolder(folderPath: string): Promise<void> {
    try {
      const data = await apiPost<LibraryPayload>("/api/library/watch/remove", {
        folder_path: folderPath,
      });
      if (!data.ok) {
        logOutput(`Remove watch folder error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      setLibrary(data);
      logOutput("Removed watch folder.");
    } catch (error) {
      logOutput(
        `Remove watch folder error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function loadDemo(): Promise<void> {
    const demoPath = demoPathInput.trim();
    if (!demoPath) {
      logOutput("Enter a demo file path.", "log-error");
      return;
    }

    setLoadingDemo(true);
    logOutput(`Parsing demo: ${demoPath}`);

    try {
      const data = await apiPost<LoadDemoResponse>("/api/load", { demo_path: demoPath });
      if (!data.ok) {
        clearLoadedState();
        logOutput(`Error: ${data.error || "unknown"}`, "log-error");
        setLoadingDemo(false);
        return;
      }

      const nextMeta = {
        header: data.header || {},
        totalKills: data.total_kills || 0,
        players: data.players || [],
        weapons: data.weapons || [],
        rounds: data.rounds || [],
      };
      startTransition(() => {
        setDemoMeta(nextMeta);
        setQueue(new Map());
        setCurrentKills([]);
      });
      logOutput(
        `Loaded: ${nextMeta.header.map_name || "?"} | ${nextMeta.totalKills} kills | tickrate ${nextMeta.header.tickrate || "?"}`,
        "log-success",
      );
      await loadClips();
      await loadLibrary(true);
    } catch (error) {
      clearLoadedState();
      logOutput(`Error: ${error instanceof Error ? error.message : String(error)}`, "log-error");
    }

    setLoadingDemo(false);
  }

  function clearFilters(): void {
    setFilters({
      player: "",
      weapon: "",
      roundStart: "",
      roundEnd: "",
      side: "",
      headshot: false,
    });
  }

  function toggleKill(kill: Kill): void {
    const killId = killIdentifier(kill);
    updateQueue((current) => {
      if (current.has(killId)) {
        current.delete(killId);
      } else {
        current.set(killId, kill);
      }
      return current;
    });
  }

  function selectAll(): void {
    updateQueue((current) => {
      for (const kill of currentKills) {
        const killId = killIdentifier(kill);
        current.set(killId, kill);
      }
      return current;
    });
  }

  function selectNone(): void {
    updateQueue((current) => {
      for (const kill of currentKills) {
        const killId = killIdentifier(kill);
        current.delete(killId);
      }
      return current;
    });
  }

  function removeFromQueue(killId: number): void {
    updateQueue((current) => {
      current.delete(killId);
      return current;
    });
  }

  function clearQueue(): void {
    setQueue(new Map());
    logOutput("Queue cleared.");
  }

  async function saveFragStatConfig(): Promise<void> {
    const payload: Record<string, unknown> = {
      server_url: fragStatConfig.server_url.trim(),
      watch_dir: fragStatConfig.watch_dir.trim(),
      auto_upload: fragStatConfig.auto_upload,
      default_event_id: selectedEventId || fragStatConfig.default_event_id || 1,
    };
    if (apiKeyInput.trim() || !fragStatConfig.api_key_set) {
      payload.api_key = apiKeyInput.trim();
    }

    try {
      const data = await apiPost<{ ok: boolean; config?: FragStatConfig; error?: string }>(
        "/api/config",
        payload,
      );
      if (!data.ok || !data.config) {
        logOutput(`Save config error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      setFragStatConfig(data.config);
      setApiKeyInput("");
      logOutput("FRAG-STAT config saved.", "log-success");
      await loadFragStatEvents(true, selectedEventId || fragStatConfig.default_event_id || 1);
      await refreshWatcherStatus(true);
    } catch (error) {
      logOutput(
        `Save config error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function testFragStatConnection(): Promise<void> {
    try {
      const data = await apiPost<{ ok: boolean; error?: string }>("/api/test-connection", {});
      if (!data.ok) {
        logOutput(`FRAG-STAT connection failed: ${data.error || "unreachable"}`, "log-error");
        return;
      }
      logOutput("FRAG-STAT connection OK.", "log-success");
      await loadFragStatEvents(true, selectedEventId || fragStatConfig.default_event_id || 1);
    } catch (error) {
      logOutput(
        `FRAG-STAT connection failed: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function uploadCurrentDemo(): Promise<void> {
    const eventId = selectedEventId || fragStatConfig.default_event_id;
    if (!eventId) {
      logOutput("Select a FRAG-STAT event first.", "log-error");
      return;
    }

    try {
      const data = await apiPost<{ ok: boolean; status?: string; error?: string }>("/api/upload", {
        event_id: eventId,
        match_id: selectedMatchId,
      });
      if (!data.ok) {
        logOutput(`Upload failed: ${data.error || "unknown"}`, "log-error");
        return;
      }
      logOutput(`Uploaded demo. Server status: ${data.status || "unknown"}`, "log-success");
      await refreshWatcherStatus(true);
    } catch (error) {
      logOutput(
        `Upload failed: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function startWatcher(): Promise<void> {
    try {
      const data = await apiPost<{ ok: boolean; watch_dir?: string; error?: string }>(
        "/api/watcher/start",
        {},
      );
      if (!data.ok) {
        logOutput(`Start watcher error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      logOutput(`Watcher started: ${data.watch_dir || ""}`, "log-success");
      await refreshWatcherStatus(true);
    } catch (error) {
      logOutput(
        `Start watcher error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function stopWatcher(): Promise<void> {
    try {
      const data = await apiPost<{ ok: boolean; error?: string }>("/api/watcher/stop", {});
      if (!data.ok) {
        logOutput(`Stop watcher error: ${data.error || "unknown"}`, "log-error");
        return;
      }
      logOutput("Watcher stopped.");
      await refreshWatcherStatus(true);
    } catch (error) {
      logOutput(
        `Stop watcher error: ${error instanceof Error ? error.message : String(error)}`,
        "log-error",
      );
    }
  }

  async function startRecord(launch: boolean): Promise<void> {
    if (selectedCount === 0) {
      logOutput("Queue is empty. Check kills in the table to add them.", "log-error");
      return;
    }

    const selectedIds = queuedKills
      .map((kill) => (typeof kill.kill_id === "number" ? kill.kill_id : null))
      .filter((value): value is number => value !== null);

    logOutput(`${launch ? "Recording" : "Generating JSON for"} ${selectedIds.length} kill(s)...`);

    try {
      const data = await apiPost<RecordResponse>("/api/record", {
        selected_ids: selectedIds,
        before: Number.parseFloat(recordBefore) || 2,
        after: Number.parseFloat(recordAfter) || 1,
        framerate: Number.parseInt(recordFramerate, 10) || 60,
        hud_mode: hudMode,
        launch,
      });

      for (const item of data.diagnostics || []) {
        logOutput(`  ${item}`);
      }

      if (!data.ok) {
        logOutput(`Error: ${data.error || "unknown"}`, "log-error");
        if (data.json_path) {
          logOutput(`JSON written: ${data.json_path}`, "log-success");
        }
        return;
      }

      logOutput(
        `Generated ${data.sequences_count || 0} sequence(s) -> ${data.json_path || "unknown"}`,
        "log-success",
      );
      if (data.launched) {
        logOutput("CS2 launched via HLAE. Old clips were cleaned.", "log-success");
        setClips([]);
        setActiveClipName(null);
      }
    } catch (error) {
      logOutput(`Error: ${error instanceof Error ? error.message : String(error)}`, "log-error");
    }
  }

  async function encodeClips(): Promise<void> {
    if (cs2Running || autoEncodeRunning) {
      logOutput("Wait for CS2 to finish recording before encoding.", "log-error");
      return;
    }
    logOutput("Encoding TGA clips to MP4...");
    try {
      const data = await apiPost<EncodeResponse>("/api/encode", {
        framerate: Number.parseInt(recordFramerate, 10) || 60,
        concatenate: true,
      });
      for (const name of data.encoded || []) {
        logOutput(`  Encoded: ${name}`, "log-success");
      }
      if (data.concatenated) {
        logOutput(`  Combined: ${data.concatenated}`, "log-success");
      }
      for (const error of data.errors || []) {
        logOutput(`  ${error}`, "log-error");
      }
      if (!data.ok) {
        logOutput(`Encoding failed: ${data.error || "see errors above"}`, "log-error");
        return;
      }
      logOutput(`Done. ${(data.encoded || []).length} clip(s) encoded.`, "log-success");
      await loadClips();
    } catch (error) {
      logOutput(`Encode error: ${error instanceof Error ? error.message : String(error)}`, "log-error");
    }
  }

  async function cleanClips(): Promise<void> {
    try {
      const data = await apiPost<{ ok: boolean; removed?: number }>("/api/clean", {});
      if (data.ok) {
        logOutput(`Cleaned ${data.removed || 0} old clip(s)/video(s).`);
        await loadClips();
      }
    } catch (error) {
      logOutput(`Clean error: ${error instanceof Error ? error.message : String(error)}`, "log-error");
    }
  }

  function onConfigChange<K extends keyof FragStatConfig>(
    key: K,
    value: FragStatConfig[K],
  ): void {
    setFragStatConfig((current) => ({ ...current, [key]: value }));
  }

  function onFilterChange<K extends keyof DemoFilters>(key: K, value: DemoFilters[K]): void {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  const loaded = demoMeta !== null;
  const previewVisible = clips.length > 0;
  const eventIdValue = selectedEventId ?? fragStatConfig.default_event_id ?? 1;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-panel">
          <h1>FRAG-DEMO</h1>
          <p>Broadcast Replay Console</p>
        </div>

        <section className="sidebar-card">
          <div className="sidebar-section-title">Loaded Demos</div>
          <div className="sidebar-list">
            {library?.recent_demos.length ? (
              library.recent_demos.map((demo) => {
                const isSelected = pathKey(demo.path) === pathKey(selectedDemoPath);
                return (
                  <button
                    key={demo.path}
                    className={`sidebar-item${isSelected ? " is-selected" : ""}`}
                    type="button"
                    onClick={() => {
                      void selectDemo(demo.path, true);
                    }}
                  >
                    <div className="sidebar-item-title">{demo.name || "Unknown demo"}</div>
                    <div className="sidebar-item-meta">
                      {(shortPath(demo.folder_path || "") || "Unknown folder") +
                        " | loaded " +
                        formatTimestamp(demo.last_loaded_at)}
                    </div>
                    {demo.exists === false ? (
                      <div className="sidebar-item-flag">MISSING FILE</div>
                    ) : null}
                  </button>
                );
              })
            ) : (
              <div className="sidebar-empty">No recently loaded demos.</div>
            )}
          </div>
        </section>

        <section className="sidebar-card">
          <div className="sidebar-section-title">Watched Folders</div>
          <div className="watch-controls">
            <input
              type="text"
              value={watchFolderInput}
              placeholder="Folder path for auto-discovery"
              onChange={(event) => setWatchFolderInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void addWatchFolder();
              }}
            />
            <div className="watch-buttons">
              <button className="btn btn-secondary" type="button" onClick={() => void browseWatchFolder()}>
                Browse
              </button>
              <button className="btn btn-primary" type="button" onClick={() => void addWatchFolder()}>
                Add Folder
              </button>
              <button className="btn btn-secondary" type="button" onClick={() => void loadLibrary(false)}>
                Refresh
              </button>
            </div>
          </div>
          <div className="sidebar-list">
            {library?.watched_folders.length ? (
              library.watched_folders.map((folder) => (
                <div className="folder-row" key={folder.path}>
                  <div className="sidebar-item">
                    <div className="sidebar-item-title">{shortPath(folder.path || "")}</div>
                    <div className="sidebar-item-meta">{folder.path || ""}</div>
                    {folder.exists === false ? (
                      <div className="sidebar-item-flag">FOLDER MISSING</div>
                    ) : null}
                  </div>
                  <button
                    className="btn-icon"
                    type="button"
                    title="Remove folder"
                    onClick={() => {
                      void removeWatchFolder(folder.path);
                    }}
                  >
                    x
                  </button>
                </div>
              ))
            ) : (
              <div className="sidebar-empty">No folders configured.</div>
            )}
          </div>
        </section>

        <section className="sidebar-card sidebar-card-grow">
          <div className="sidebar-section-title">Discovered Demos</div>
          <div className="sidebar-list">
            {library?.discovered_demos.length ? (
              library.discovered_demos.map((demo) => {
                const isSelected = pathKey(demo.path) === pathKey(selectedDemoPath);
                return (
                  <button
                    key={demo.path}
                    className={`sidebar-item${isSelected ? " is-selected" : ""}`}
                    type="button"
                    onClick={() => {
                      void selectDemo(demo.path, true);
                    }}
                  >
                    <div className="sidebar-item-title">{demo.name || "Unknown demo"}</div>
                    <div className="sidebar-item-meta">
                      {(shortPath(demo.folder_path || "") || "Unknown folder") +
                        " | " +
                        formatMtime(demo.modified_ts)}
                    </div>
                    {demo.exists === false ? (
                      <div className="sidebar-item-flag">MISSING FILE</div>
                    ) : null}
                  </button>
                );
              })
            ) : (
              <div className="sidebar-empty">No demos found in watched folders.</div>
            )}
          </div>
        </section>

        <section className="sidebar-card">
          <div className="sidebar-section-title">FRAG-STAT</div>
          <div className="integration-grid">
            <input
              type="text"
              value={fragStatConfig.server_url}
              placeholder="Server URL (e.g. http://127.0.0.1:3000)"
              onChange={(event) => onConfigChange("server_url", event.target.value)}
            />
            <input
              type="password"
              value={apiKeyInput}
              placeholder={fragStatConfig.api_key_set ? "Stored API key" : "API key"}
              onChange={(event) => setApiKeyInput(event.target.value)}
            />
            <input
              type="text"
              value={fragStatConfig.watch_dir}
              placeholder="Auto-upload watch directory"
              onChange={(event) => onConfigChange("watch_dir", event.target.value)}
            />
            <select
              aria-label="FRAG-STAT event"
              value={selectedEventId ?? ""}
              onChange={(event) => {
                const value = Number.parseInt(event.target.value, 10);
                const nextValue = Number.isNaN(value) ? null : value;
                setSelectedEventId(nextValue);
                onConfigChange("default_event_id", nextValue || fragStatConfig.default_event_id);
              }}
            >
              <option value="">Select FRAG-STAT event</option>
              {fragStatEvents.map((event) => (
                <option key={event.id} value={event.id}>
                  {event.name}
                </option>
              ))}
            </select>
            <select
              aria-label="FRAG-STAT match"
              value={selectedMatchId ?? ""}
              onChange={(event) => {
                const value = Number.parseInt(event.target.value, 10);
                setSelectedMatchId(Number.isNaN(value) ? null : value);
              }}
            >
              <option value="">Auto-create match on upload</option>
              {fragStatMatches.map((match) => (
                <option key={match.id} value={match.id}>
                  {(match.team1Name || "Team 1") + " vs " + (match.team2Name || "Team 2")}
                </option>
              ))}
            </select>
            <input
              type="number"
              value={String(eventIdValue)}
              min="1"
              step="1"
              onChange={(event) => {
                const value = Number.parseInt(event.target.value, 10);
                const nextValue = Number.isNaN(value) ? 1 : value;
                setSelectedEventId(nextValue);
                onConfigChange("default_event_id", nextValue);
              }}
            />
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={fragStatConfig.auto_upload}
                onChange={(event) => onConfigChange("auto_upload", event.target.checked)}
              />
              <span>Auto-upload new demos</span>
            </label>
            <div className="watch-buttons">
              <button className="btn btn-secondary" type="button" onClick={() => void saveFragStatConfig()}>
                Save
              </button>
              <button className="btn btn-secondary" type="button" onClick={() => void testFragStatConnection()}>
                Test
              </button>
              <button className="btn btn-primary" type="button" onClick={() => void loadFragStatEvents(false)}>
                Events
              </button>
            </div>
            <div className="watch-buttons">
              <button className="btn btn-primary" type="button" onClick={() => void uploadCurrentDemo()}>
                Upload
              </button>
              <button className="btn btn-secondary" type="button" onClick={() => void startWatcher()}>
                Start Watcher
              </button>
              <button className="btn btn-secondary" type="button" onClick={() => void stopWatcher()}>
                Stop
              </button>
            </div>
            <div className="integration-status">{fragStatStatus}</div>
          </div>
        </section>

        <div className="sidebar-footer">
          <div className="sidebar-section-title">Selected Demo</div>
          <div className="selected-path">{selectedDemoPath || "No demo selected"}</div>
          <button
            className="btn btn-primary btn-load-selected"
            type="button"
            disabled={!selectedDemoPath}
            onClick={() => {
              if (selectedDemoPath) {
                setDemoPathInput(selectedDemoPath);
                void loadDemo();
              }
            }}
          >
            Load Selected Demo
          </button>
        </div>
      </aside>

      <main className="dashboard">
        <header className="topbar">
          <div>
            <h2>Operator Dashboard</h2>
            <p>
              {(library?.discovered_demos.length || 0) +
                " discovered demos | last scan " +
                formatTimestamp(library?.scanned_at)}
            </p>
          </div>
          <div className="topbar-badges">
            {cs2Running ? <div className="cs2-badge running">CS2 RUNNING</div> : null}
            <div className={`status-badge${loaded ? " loaded" : ""}`}>
              {loaded ? demoMeta?.header.map_name || "Loaded" : "No demo loaded"}
            </div>
          </div>
        </header>

        <div className="dashboard-content">
          <div className="section">
            <div className="section-title">Demo Loader</div>
            <div className="demo-loader">
              <input
                type="text"
                value={demoPathInput}
                placeholder="Path to .dem file (e.g. C:/demos/match.dem)"
                onChange={(event) => setDemoPathInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void loadDemo();
                }}
              />
              <button className="btn btn-secondary" type="button" onClick={() => void browseDemoFile()}>
                Browse Demo
              </button>
              <button className="btn btn-primary" type="button" onClick={() => void loadDemo()} disabled={loadingDemo}>
                Load Demo
              </button>
              <div className={`spinner${loadingDemo ? " active" : ""}`} />
            </div>
            {loaded ? (
              <div className="demo-info">
                <div>
                  Map: <span>{demoMeta?.header.map_name || "?"}</span>
                </div>
                <div>
                  Tickrate: <span>{demoMeta?.header.tickrate || "?"}</span>
                </div>
                <div>
                  Total kills: <span>{demoMeta?.totalKills || 0}</span>
                </div>
              </div>
            ) : null}
          </div>

          {loaded ? (
            <>
              <div className="section">
                <div className="section-title">Filters</div>
                <div className="filters">
                  <div className="filter-group">
                    <label>Player</label>
                    <select
                      value={filters.player}
                      onChange={(event) => onFilterChange("player", event.target.value)}
                    >
                      <option value="">All</option>
                      {(demoMeta?.players || []).map((player) => (
                        <option key={player} value={player}>
                          {player}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="filter-group">
                    <label>Weapon</label>
                    <select
                      value={filters.weapon}
                      onChange={(event) => onFilterChange("weapon", event.target.value)}
                    >
                      <option value="">All</option>
                      {(demoMeta?.weapons || []).map((weapon) => (
                        <option key={weapon} value={weapon}>
                          {weapon}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="filter-group">
                    <label>Round Start</label>
                    <select
                      value={filters.roundStart}
                      onChange={(event) => onFilterChange("roundStart", event.target.value)}
                    >
                      <option value="">Any</option>
                      {(demoMeta?.rounds || []).map((round) => (
                        <option key={round} value={String(round)}>
                          {round}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="filter-group">
                    <label>Round End</label>
                    <select
                      value={filters.roundEnd}
                      onChange={(event) => onFilterChange("roundEnd", event.target.value)}
                    >
                      <option value="">Any</option>
                      {(demoMeta?.rounds || []).map((round) => (
                        <option key={round} value={String(round)}>
                          {round}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="filter-group">
                    <label>Side</label>
                    <select
                      value={filters.side}
                      onChange={(event) => onFilterChange("side", event.target.value)}
                    >
                      <option value="">All</option>
                      <option value="CT">CT</option>
                      <option value="TERRORIST">T</option>
                    </select>
                  </div>
                  <div className="filter-group filter-group-checkbox">
                    <label>Headshot</label>
                    <input
                      type="checkbox"
                      checked={filters.headshot}
                      onChange={(event) => onFilterChange("headshot", event.target.checked)}
                    />
                  </div>
                  <button className="btn btn-secondary" type="button" onClick={clearFilters}>
                    Clear
                  </button>
                  <div className="filter-summary">
                    {currentKills.length} kill{currentKills.length !== 1 ? "s" : ""}
                  </div>
                </div>
              </div>

              <div className="section">
                <div className="section-title">Kills</div>
                <div className="table-controls">
                  <button className="btn btn-secondary" type="button" onClick={selectAll}>
                    Select All
                  </button>
                  <button className="btn btn-secondary" type="button" onClick={selectNone}>
                    Select None
                  </button>
                </div>
                <div className="table-wrapper">
                  {currentKills.length ? (
                    <table className="kill-table">
                      <thead>
                        <tr>
                          <th className="col-check" />
                          <th className="col-tick">Tick</th>
                          <th>Round</th>
                          <th>Attacker</th>
                          <th>Victim</th>
                          <th>Weapon</th>
                          <th>HS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {currentKills.map((kill) => {
                          const killId = killIdentifier(kill);
                          const queued = queue.has(killId);
                          return (
                            <tr key={killId} className={queued ? "selected" : ""}>
                              <td className="col-check">
                                <input
                                  type="checkbox"
                                  checked={queued}
                                  onChange={() => toggleKill(kill)}
                                />
                              </td>
                              <td className="col-tick">{kill.tick || 0}</td>
                              <td>{kill.total_rounds_played ?? "?"}</td>
                              <td>{kill.attacker_name || "?"}</td>
                              <td>{kill.user_name || "?"}</td>
                              <td>{kill.weapon || "?"}</td>
                              <td className={kill.headshot ? "hs-yes" : ""}>
                                {kill.headshot ? "HS" : ""}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : (
                    <div className="empty-state">No kills match the current filters.</div>
                  )}
                </div>
              </div>

              <div className="section">
                <div className="section-title">Recording Queue</div>
                <div className="table-controls">
                  <span className="filter-summary">
                    {selectedCount} kill{selectedCount !== 1 ? "s" : ""} queued
                  </span>
                  <button className="btn btn-secondary" type="button" style={{ marginLeft: "auto" }} onClick={clearQueue}>
                    Clear Queue
                  </button>
                </div>
                <div className="table-wrapper queue-table-wrap">
                  {queuedKills.length ? (
                    <table className="kill-table">
                      <thead>
                        <tr>
                          <th style={{ width: 30 }} />
                          <th className="col-tick">Tick</th>
                          <th>Round</th>
                          <th>Attacker</th>
                          <th>Victim</th>
                          <th>Weapon</th>
                          <th>HS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {queuedKills.map((kill) => {
                          const killId = killIdentifier(kill);
                          return (
                            <tr key={killId}>
                              <td>
                                <button className="btn-remove" type="button" onClick={() => removeFromQueue(killId)}>
                                  x
                                </button>
                              </td>
                              <td className="col-tick">{kill.tick || 0}</td>
                              <td>{kill.total_rounds_played ?? "?"}</td>
                              <td>{kill.attacker_name || "?"}</td>
                              <td>{kill.user_name || "?"}</td>
                              <td>{kill.weapon || "?"}</td>
                              <td className={kill.headshot ? "hs-yes" : ""}>
                                {kill.headshot ? "HS" : ""}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : (
                    <div className="empty-state">Select kills above with the checkboxes to queue them.</div>
                  )}
                </div>
              </div>

              <div className="section">
                <div className="section-title">Record</div>
                <div className="record-bar">
                  <div className="filter-group">
                    <label>Before (s)</label>
                    <input type="number" min="0" max="30" step="0.5" value={recordBefore} onChange={(event) => setRecordBefore(event.target.value)} />
                  </div>
                  <div className="filter-group">
                    <label>After (s)</label>
                    <input type="number" min="0" max="30" step="0.5" value={recordAfter} onChange={(event) => setRecordAfter(event.target.value)} />
                  </div>
                  <div className="filter-group">
                    <label>FPS</label>
                    <input type="number" min="24" max="300" step="1" value={recordFramerate} onChange={(event) => setRecordFramerate(event.target.value)} />
                  </div>
                  <div className="filter-group">
                    <label>HUD</label>
                    <select value={hudMode} onChange={(event) => setHudMode(event.target.value)}>
                      <option value="deathnotices">Death Notices Only</option>
                      <option value="all">All HUD</option>
                      <option value="none">No HUD</option>
                    </select>
                  </div>
                  <div className="record-actions">
                    <span className="selected-count">
                      {selectedCount} queued
                    </span>
                    <button className="btn btn-secondary" type="button" disabled={selectedCount === 0} onClick={() => void startRecord(false)}>
                      Generate JSON Only
                    </button>
                    <button
                      className="btn btn-record"
                      type="button"
                      disabled={selectedCount === 0 || cs2Running}
                      onClick={() => void startRecord(true)}
                    >
                      {cs2Running
                        ? "CS2 Running..."
                        : selectedCount === 0
                          ? "Record"
                          : `Record ${selectedCount} Kill${selectedCount !== 1 ? "s" : ""}`}
                    </button>
                    <button
                      className="btn btn-primary"
                      type="button"
                      disabled={cs2Running || autoEncodeRunning}
                      onClick={() => void encodeClips()}
                    >
                      {cs2Running ? "Wait For CS2" : autoEncodeRunning ? "Encoding..." : "Encode Clips"}
                    </button>
                  </div>
                </div>
              </div>
            </>
          ) : null}

          {previewVisible ? (
            <div className="section">
              <div className="section-title section-title-row">
                Clip Preview
                <button className="btn btn-secondary" type="button" onClick={() => void cleanClips()}>
                  Clean All Clips
                </button>
              </div>
              <div className="clip-list">
                {clips.map((clip) => {
                  const label = clip.is_combined ? "ALL CLIPS" : clip.name.replace(".mp4", "");
                  const active = clip.name === activeClipName;
                  return (
                    <button
                      key={clip.name}
                      className={`clip-btn${clip.is_combined ? " combined" : ""}${active ? " active" : ""}`}
                      type="button"
                      title={`${clip.size_mb} MB`}
                      onClick={() => setActiveClipName(clip.name)}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              <div className="video-player-wrap">
                {activeClipName ? (
                  <video key={activeClipName} controls preload="metadata" src={`/clips/${encodeURIComponent(activeClipName)}`} style={{ display: "block" }}>
                    Your browser does not support the video tag.
                  </video>
                ) : null}
              </div>
            </div>
          ) : null}

          <div className="section">
            <div className="section-title">Output</div>
            <div className="output-log" ref={logRef}>
              {logs.map((line) => (
                <div key={line.id} className={`log-line${line.className ? ` ${line.className}` : ""}`}>
                  {"> " + line.message}
                </div>
              ))}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

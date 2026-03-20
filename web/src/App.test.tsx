import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

type RouteHandler = (url: string, init?: RequestInit) => unknown | Promise<unknown>;

function installFetchMock(
  routes: Record<string, unknown | RouteHandler>,
): { calls: Array<{ url: string; init?: RequestInit }> } {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof Request
          ? input.url
          : String(input);
    calls.push({ url, init });

    const handler = routes[url];
    if (!handler) {
      throw new Error(`Unexpected fetch: ${url}`);
    }

    const payload =
      typeof handler === "function" ? await handler(url, init) : handler;

    return {
      json: async () => payload,
    } as Response;
  });

  vi.stubGlobal("fetch", mock);
  return { calls };
}

function defaultRoutes(overrides: Record<string, unknown | RouteHandler> = {}) {
  return {
    "/api/library": {
      ok: true,
      watched_folders: [{ path: "/watch", recursive: true, exists: true }],
      recent_demos: [
        {
          name: "match.dem",
          path: "/demos/match.dem",
          folder_path: "/demos",
          last_loaded_at: "2026-03-20T12:00:00.000Z",
          exists: true,
        },
      ],
      discovered_demos: [
        {
          name: "fresh.dem",
          path: "/watch/fresh.dem",
          folder_path: "/watch",
          modified_ts: 1_700_000_100,
          size_mb: 12.4,
          exists: true,
        },
      ],
      selected_demo_path: "/demos/match.dem",
      scanned_at: "2026-03-20T12:00:00.000Z",
    },
    "/api/config": {
      server_url: "http://fragstat.local",
      api_key: "stored...",
      api_key_set: true,
      watch_dir: "/watch",
      auto_upload: true,
      default_event_id: 5,
    },
    "/api/fragstat/events": {
      ok: true,
      events: [
        { id: 5, name: "Austin Major" },
        { id: 7, name: "Dallas Finals" },
      ],
    },
    "/api/fragstat/matches/5": {
      ok: true,
      matches: [{ id: 11, team1Name: "FaZe", team2Name: "Spirit" }],
    },
    "/api/fragstat/matches/7": {
      ok: true,
      matches: [{ id: 21, team1Name: "Liquid", team2Name: "Vitality" }],
    },
    "/api/watcher/status": {
      running: true,
      watch_dir: "/watch",
      last_file: "fresh.dem",
      last_upload: null,
    },
    "/api/status": {
      loaded: false,
      cs2_running: false,
      watcher_running: true,
      last_upload: null,
      auto_encode_running: false,
      auto_encode_event_id: 0,
      last_auto_encode: null,
    },
    ...overrides,
  };
}

beforeEach(() => {
  vi.spyOn(window, "setInterval").mockImplementation(
    () => 1 as unknown as ReturnType<typeof window.setInterval>,
  );
  vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App", () => {
  it("renders the FRAG-STAT-enabled React console from API state", async () => {
    installFetchMock(defaultRoutes());

    render(<App />);

    expect(await screen.findByText("Broadcast Replay Console")).toBeInTheDocument();
    expect(await screen.findByText("match.dem")).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "Austin Major" })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "FaZe vs Spirit" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("/watch")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/Watcher running \| \/watch \| last file fresh\.dem/)).toBeInTheDocument();
    });
  });

  it("posts the selected event and match when uploading", async () => {
    let uploadBody: Record<string, unknown> | null = null;
    installFetchMock(
      defaultRoutes({
        "/api/upload": (_url: string, init?: RequestInit) => {
          uploadBody = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
          return { ok: true, status: "queued" };
        },
      }),
    );

    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => {
      expect(uploadBody).toEqual({ event_id: 5, match_id: null });
    });
    expect(screen.getByText("> Uploaded demo. Server status: queued")).toBeInTheDocument();
  });
});

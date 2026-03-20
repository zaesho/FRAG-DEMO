// frag-demo frontend logic

// -- State --
let currentKills = [];          // Last fetched kill list (from filters)
let queue = new Map();          // kill_id -> kill object (the recording queue)
let cs2Running = false;
let autoEncodeRunning = false;
let lastAutoEncodeEventId = 0;
let selectedDemoPath = null;
let watchedFolders = [];
let recentDemos = [];
let discoveredDemos = [];

const STATUS_POLL_MS = 5000;
const LIBRARY_POLL_MS = 10000;

// -- DOM refs --
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// -- API helpers --
async function apiPost(url, data) {
    const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
    return res.json();
}

async function apiGet(url) {
    const res = await fetch(url);
    return res.json();
}

// -- Logging --
function logOutput(msg, cls = "") {
    const log = $("#output-log");
    if (!log) return;
    const line = document.createElement("div");
    line.className = "log-line" + (cls ? " " + cls : "");
    line.textContent = "> " + msg;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function pathKey(path) {
    return String(path || "").replaceAll("\\", "/").toLowerCase();
}

function formatTimestamp(timestamp) {
    if (!timestamp) return "Unknown";
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return String(timestamp);
    return date.toLocaleString();
}

function formatMtime(seconds) {
    if (typeof seconds !== "number") return "mtime unknown";
    const date = new Date(seconds * 1000);
    if (Number.isNaN(date.getTime())) return "mtime unknown";
    return date.toLocaleString();
}

function shortPath(path) {
    const raw = String(path || "");
    if (!raw) return "";
    const parts = raw.replaceAll("\\", "/").split("/").filter(Boolean);
    if (parts.length <= 2) return raw;
    return ".../" + parts.slice(-2).join("/");
}

function updateSelectedDemoUI() {
    const selectedLabel = $("#selected-demo-path");
    const loadBtn = $("#btn-load-selected");
    if (selectedLabel) {
        selectedLabel.textContent = selectedDemoPath || "No demo selected";
    }
    if (loadBtn) {
        loadBtn.disabled = !selectedDemoPath;
    }
}

async function selectDemo(path, persist = true) {
    selectedDemoPath = path || null;
    if (selectedDemoPath) {
        $("#demo-path").value = selectedDemoPath;
    }

    renderLibraryLists();
    updateSelectedDemoUI();

    if (!persist || !selectedDemoPath) return;

    try {
        const data = await apiPost("/api/library/select", { demo_path: selectedDemoPath });
        if (!data.ok) {
            logOutput("Select demo error: " + (data.error || "unknown"), "log-error");
            return;
        }
        if (data.selected_demo_path) {
            selectedDemoPath = data.selected_demo_path;
            $("#demo-path").value = selectedDemoPath;
            renderLibraryLists();
            updateSelectedDemoUI();
        }
    } catch (e) {
        logOutput("Select demo error: " + e.message, "log-error");
    }
}

function applyLibraryPayload(data) {
    watchedFolders = Array.isArray(data.watched_folders) ? data.watched_folders : [];
    recentDemos = Array.isArray(data.recent_demos) ? data.recent_demos : [];
    discoveredDemos = Array.isArray(data.discovered_demos) ? data.discovered_demos : [];

    if (typeof data.selected_demo_path === "string" && data.selected_demo_path.trim()) {
        selectedDemoPath = data.selected_demo_path;
        $("#demo-path").value = data.selected_demo_path;
    }

    const scanStatus = $("#library-scan-status");
    if (scanStatus) {
        const scannedAt = formatTimestamp(data.scanned_at);
        scanStatus.textContent = `${discoveredDemos.length} discovered demos | last scan ${scannedAt}`;
    }

    renderLibraryLists();
    updateSelectedDemoUI();
}

function renderDemoList(containerSel, emptySel, demos, getMeta) {
    const container = $(containerSel);
    const empty = $(emptySel);
    if (!container || !empty) return;

    if (!demos.length) {
        container.innerHTML = "";
        empty.style.display = "block";
        return;
    }

    empty.style.display = "none";
    container.innerHTML = demos
        .map((demo) => {
            const isSelected = pathKey(demo.path) === pathKey(selectedDemoPath);
            const warning = demo.exists === false
                ? '<div class="sidebar-item-flag">MISSING FILE</div>'
                : "";
            return `<button class="sidebar-item ${isSelected ? "is-selected" : ""}" data-demo-path="${escapeHtml(demo.path || "")}">
                <div class="sidebar-item-title">${escapeHtml(demo.name || "Unknown demo")}</div>
                <div class="sidebar-item-meta">${escapeHtml(getMeta(demo))}</div>
                ${warning}
            </button>`;
        })
        .join("");

    container.querySelectorAll(".sidebar-item[data-demo-path]").forEach((el) => {
        el.addEventListener("click", () => {
            void selectDemo(el.dataset.demoPath || "", true);
        });
    });
}

function renderWatchedFolders() {
    const list = $("#watch-folder-list");
    const empty = $("#watch-folder-empty");
    if (!list || !empty) return;

    if (!watchedFolders.length) {
        list.innerHTML = "";
        empty.style.display = "block";
        return;
    }

    empty.style.display = "none";
    list.innerHTML = watchedFolders
        .map((folder) => {
            const missing = folder.exists === false
                ? '<div class="sidebar-item-flag">FOLDER MISSING</div>'
                : "";
            return `<div class="folder-row">
                <div class="sidebar-item">
                    <div class="sidebar-item-title">${escapeHtml(shortPath(folder.path || ""))}</div>
                    <div class="sidebar-item-meta">${escapeHtml(folder.path || "")}</div>
                    ${missing}
                </div>
                <button class="btn-icon" type="button" title="Remove folder" data-remove-folder="${escapeHtml(folder.path || "")}">x</button>
            </div>`;
        })
        .join("");

    list.querySelectorAll("[data-remove-folder]").forEach((btn) => {
        btn.addEventListener("click", () => {
            void removeWatchFolder(btn.dataset.removeFolder || "");
        });
    });
}

function renderLibraryLists() {
    renderDemoList(
        "#recent-demo-list",
        "#recent-demo-empty",
        recentDemos,
        (demo) => `${shortPath(demo.folder_path || "") || "Unknown folder"} | loaded ${formatTimestamp(demo.last_loaded_at)}`,
    );

    renderDemoList(
        "#discovered-demo-list",
        "#discovered-demo-empty",
        discoveredDemos,
        (demo) => `${shortPath(demo.folder_path || "") || "Unknown folder"} | ${formatMtime(demo.modified_ts)}`,
    );

    renderWatchedFolders();
}

async function loadLibrary({ silent = false } = {}) {
    try {
        const data = await apiGet("/api/library");
        if (!data.ok) {
            if (!silent) logOutput("Library error: " + (data.error || "unknown"), "log-error");
            return;
        }
        applyLibraryPayload(data);
    } catch (e) {
        if (!silent) logOutput("Library error: " + e.message, "log-error");
    }
}

async function browseWatchFolder() {
    try {
        const data = await apiGet("/api/browse-folder");
        if (data.ok && data.path) {
            $("#watch-folder-path").value = data.path;
        }
    } catch (e) {
        logOutput("Browse folder failed: " + e.message, "log-error");
    }
}

async function addWatchFolder() {
    const folderPath = $("#watch-folder-path").value.trim();
    if (!folderPath) {
        logOutput("Enter a folder path to watch.", "log-error");
        return;
    }

    try {
        const data = await apiPost("/api/library/watch/add", { folder_path: folderPath });
        if (!data.ok) {
            logOutput("Add watch folder error: " + data.error, "log-error");
            return;
        }
        $("#watch-folder-path").value = "";
        applyLibraryPayload(data);
        logOutput("Added watch folder.", "log-success");
    } catch (e) {
        logOutput("Add watch folder error: " + e.message, "log-error");
    }
}

async function removeWatchFolder(folderPath) {
    if (!folderPath) return;
    try {
        const data = await apiPost("/api/library/watch/remove", { folder_path: folderPath });
        if (!data.ok) {
            logOutput("Remove watch folder error: " + data.error, "log-error");
            return;
        }
        applyLibraryPayload(data);
        logOutput("Removed watch folder.");
    } catch (e) {
        logOutput("Remove watch folder error: " + e.message, "log-error");
    }
}

async function loadSelectedDemo() {
    if (!selectedDemoPath) {
        logOutput("Select a demo in the sidebar first.", "log-error");
        return;
    }
    $("#demo-path").value = selectedDemoPath;
    await loadDemo();
}

// -- Spinner --
function setLoading(loading) {
    const spinner = $("#load-spinner");
    const btn = $("#btn-load");
    if (!spinner || !btn) return;

    if (loading) {
        spinner.classList.add("active");
        btn.disabled = true;
    } else {
        spinner.classList.remove("active");
        btn.disabled = false;
    }
}

// -- CS2 state polling --
function updateCS2State(running) {
    cs2Running = running;
    const badge = $("#cs2-badge");
    if (!badge) return;

    if (running) {
        badge.textContent = "CS2 RUNNING";
        badge.classList.add("running");
        badge.style.display = "inline-block";
    } else {
        badge.textContent = "";
        badge.classList.remove("running");
        badge.style.display = "none";
    }
    updateRecordButton();
    updateEncodeButton();
}

async function pollStatus() {
    try {
        const data = await apiGet("/api/status");
        if (!data.loaded) {
            clearLoadedState();
        }
        updateCS2State(data.cs2_running || false);
        handleAutoEncodeStatus(data);
    } catch (e) {
        // ignore poll errors
    }
}

function clearLoadedState() {
    currentKills = [];
    queue.clear();
    autoEncodeRunning = false;
    updateStatus(false);

    const demoInfo = $("#demo-info");
    if (demoInfo) demoInfo.style.display = "none";

    const previewSection = $("#preview-section");
    if (previewSection) previewSection.style.display = "none";

    $$("#filters-section, #kills-section, #queue-section, #record-section").forEach(
        (el) => (el.style.display = "none"),
    );

    renderTable([]);
    renderQueue();
    updateRecordButton();
    updateEncodeButton();
}

async function handleAutoEncodeStatus(data) {
    const running = Boolean(data.auto_encode_running);
    const eventId = Number.isInteger(data.auto_encode_event_id) ? data.auto_encode_event_id : 0;
    const lastResult = data.last_auto_encode && typeof data.last_auto_encode === "object"
        ? data.last_auto_encode
        : null;

    if (running && (!autoEncodeRunning || eventId !== lastAutoEncodeEventId)) {
        lastAutoEncodeEventId = eventId;
        logOutput("CS2 finished recording. Auto-encoding clips...", "log-success");
    }

    autoEncodeRunning = running;
    updateEncodeButton();

    if (!running && lastResult) {
        const resultEventId = Number.isInteger(lastResult.event_id) ? lastResult.event_id : eventId;
        if (resultEventId > lastAutoEncodeEventId) {
            if (Array.isArray(lastResult.encoded)) {
                for (const name of lastResult.encoded) {
                    logOutput("  Encoded: " + name, "log-success");
                }
            }
            if (lastResult.concatenated) {
                logOutput("  Combined: " + lastResult.concatenated, "log-success");
            }
            if (Array.isArray(lastResult.errors)) {
                for (const error of lastResult.errors) {
                    logOutput("  " + error, "log-error");
                }
            }
            if (lastResult.ok) {
                logOutput("Auto-encoding finished.", "log-success");
                await loadClips();
            } else {
                logOutput(
                    "Auto-encoding failed: " + (lastResult.error || "see errors above"),
                    "log-error",
                );
            }
            lastAutoEncodeEventId = resultEventId;
        }
    }
}

// -- Demo loading --
async function browseDemoFile() {
    try {
        const data = await apiGet("/api/browse");
        if (data.ok && data.path) {
            $("#demo-path").value = data.path;
        }
    } catch (e) {
        logOutput("Browse failed: " + e.message, "log-error");
    }
}

async function loadDemo() {
    const demoPath = $("#demo-path").value.trim();
    if (!demoPath) {
        logOutput("Enter a demo file path.", "log-error");
        return;
    }

    setLoading(true);
    logOutput("Parsing demo: " + demoPath);

    try {
        const data = await apiPost("/api/load", { demo_path: demoPath });

        if (!data.ok) {
            clearLoadedState();
            logOutput("Error: " + data.error, "log-error");
            setLoading(false);
            return;
        }

        const header = data.header || {};
        $("#info-map").textContent = header.map_name || "?";
        $("#info-tickrate").textContent = header.tickrate || "?";
        $("#info-kills").textContent = data.total_kills || 0;
        $("#demo-info").style.display = "flex";

        populateSelect("#filter-player", data.players || []);
        populateSelect("#filter-weapon", data.weapons || []);
        const roundOptions = (data.rounds || []).map(String);
        populateSelect("#filter-round-start", roundOptions);
        populateSelect("#filter-round-end", roundOptions);

        updateStatus(true, header.map_name || "?");

        $$("#filters-section, #kills-section, #queue-section, #record-section").forEach(
            (el) => (el.style.display = "block"),
        );

        logOutput(
            `Loaded: ${header.map_name || "?"} | ${data.total_kills} kills | tickrate ${header.tickrate || "?"}`,
            "log-success",
        );

        queue.clear();
        renderQueue();
        await fetchKills();
        await loadClips();
        await loadLibrary({ silent: true });
    } catch (e) {
        clearLoadedState();
        logOutput("Error: " + e.message, "log-error");
    }

    setLoading(false);
}

function populateSelect(selector, values) {
    const sel = $(selector);
    if (!sel) return;

    const current = sel.value;
    sel.innerHTML = '<option value="">All</option>';
    for (const v of values) {
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        sel.appendChild(opt);
    }
    if (values.includes(current)) sel.value = current;
}

function updateStatus(loaded, map) {
    const badge = $("#status-badge");
    if (!badge) return;

    if (loaded) {
        badge.textContent = map;
        badge.classList.add("loaded");
    } else {
        badge.textContent = "No demo loaded";
        badge.classList.remove("loaded");
    }
}

// -- Filtering --
async function fetchKills() {
    const player = $("#filter-player").value || null;
    const weapon = $("#filter-weapon").value || null;
    const headshot = $("#filter-headshot").checked ? true : null;
    const roundStartVal = $("#filter-round-start").value;
    const roundEndVal = $("#filter-round-end").value;
    const round_start = roundStartVal ? parseInt(roundStartVal, 10) : null;
    const round_end = roundEndVal ? parseInt(roundEndVal, 10) : null;
    const side = $("#filter-side").value || null;

    try {
        const data = await apiPost("/api/kills", {
            player,
            weapon,
            headshot,
            round_start,
            round_end,
            side,
        });

        if (!data.ok) {
            logOutput("Filter error: " + data.error, "log-error");
            return;
        }

        currentKills = data.kills || [];
        renderTable(currentKills);
    } catch (e) {
        logOutput("Filter error: " + e.message, "log-error");
    }
}

function clearFilters() {
    $("#filter-player").value = "";
    $("#filter-weapon").value = "";
    $("#filter-headshot").checked = false;
    $("#filter-round-start").value = "";
    $("#filter-round-end").value = "";
    $("#filter-side").value = "";
    void fetchKills();
}

// -- Table rendering --
function renderTable(kills) {
    const tbody = $("#kills-tbody");
    const empty = $("#kills-empty");

    if (!tbody || !empty) return;

    if (kills.length === 0) {
        tbody.innerHTML = "";
        empty.style.display = "block";
        $("#filter-count").textContent = "0 kills";
        return;
    }

    empty.style.display = "none";
    $("#filter-count").textContent = kills.length + " kill" + (kills.length !== 1 ? "s" : "");

    tbody.innerHTML = kills
        .map((k) => {
            const tick = k.tick || 0;
            const killId = Number.isInteger(k.kill_id) ? k.kill_id : tick;
            const inQueue = queue.has(killId);
            const rowClass = inQueue ? "selected" : "";
            const hs = k.headshot ? "HS" : "";
            const hsClass = k.headshot ? "hs-yes" : "";
            const round = k.total_rounds_played != null ? k.total_rounds_played : "?";
            return `<tr class="${rowClass}" data-kill-id="${killId}">
                <td class="col-check"><input type="checkbox" ${inQueue ? "checked" : ""} onchange="toggleKill(${killId})"></td>
                <td class="col-tick">${tick}</td>
                <td>${escapeHtml(round)}</td>
                <td>${escapeHtml(k.attacker_name || "?")}</td>
                <td>${escapeHtml(k.user_name || "?")}</td>
                <td>${escapeHtml(k.weapon || "?")}</td>
                <td class="${hsClass}">${hs}</td>
            </tr>`;
        })
        .join("");
}

// Checking a kill in the table directly adds/removes it from the queue
function toggleKill(killId) {
    if (queue.has(killId)) {
        queue.delete(killId);
    } else {
        const kill = currentKills.find((k) => k.kill_id === killId);
        if (kill) queue.set(killId, kill);
    }

    const row = $(`tr[data-kill-id="${killId}"]`);
    if (row) {
        row.classList.toggle("selected", queue.has(killId));
    }
    renderQueue();
    updateRecordButton();
}

function selectAll() {
    for (const k of currentKills) {
        if (Number.isInteger(k.kill_id)) queue.set(k.kill_id, k);
    }
    renderTable(currentKills);
    renderQueue();
    updateRecordButton();
}

function selectNone() {
    for (const k of currentKills) {
        if (Number.isInteger(k.kill_id)) queue.delete(k.kill_id);
    }
    renderTable(currentKills);
    renderQueue();
    updateRecordButton();
}

// -- Queue display --
function removeFromQueue(killId) {
    queue.delete(killId);
    renderQueue();
    renderTable(currentKills);
    updateRecordButton();
}

function clearQueue() {
    queue.clear();
    renderQueue();
    renderTable(currentKills);
    updateRecordButton();
    logOutput("Queue cleared.");
}

function renderQueue() {
    const tbody = $("#queue-tbody");
    const empty = $("#queue-empty");
    const count = $("#queue-count");
    if (!tbody || !empty || !count) return;

    const size = queue.size;
    count.textContent = size + " kill" + (size !== 1 ? "s" : "") + " queued";

    if (size === 0) {
        tbody.innerHTML = "";
        empty.style.display = "block";
        return;
    }

    empty.style.display = "none";
    const items = Array.from(queue.values());
    tbody.innerHTML = items
        .map((k) => {
            const tick = k.tick || 0;
            const killId = Number.isInteger(k.kill_id) ? k.kill_id : tick;
            const hs = k.headshot ? "HS" : "";
            const hsClass = k.headshot ? "hs-yes" : "";
            const round = k.total_rounds_played != null ? k.total_rounds_played : "?";
            return `<tr>
                <td><button class="btn-remove" onclick="removeFromQueue(${killId})">x</button></td>
                <td class="col-tick">${tick}</td>
                <td>${escapeHtml(round)}</td>
                <td>${escapeHtml(k.attacker_name || "?")}</td>
                <td>${escapeHtml(k.user_name || "?")}</td>
                <td>${escapeHtml(k.weapon || "?")}</td>
                <td class="${hsClass}">${hs}</td>
            </tr>`;
        })
        .join("");
}

function updateRecordButton() {
    const count = queue.size;
    const selectedCount = $("#selected-count");
    if (selectedCount) selectedCount.textContent = count + " queued";

    const btn = $("#btn-record");
    const genBtn = $("#btn-generate");
    if (!btn || !genBtn) return;

    genBtn.disabled = count === 0;

    if (cs2Running) {
        btn.textContent = "CS2 Running...";
        btn.disabled = true;
    } else if (count === 0) {
        btn.textContent = "Record";
        btn.disabled = true;
    } else {
        btn.textContent = `Record ${count} Kill${count !== 1 ? "s" : ""}`;
        btn.disabled = false;
    }
}

function updateEncodeButton() {
    const btn = $("#btn-encode");
    if (!btn) return;

    if (cs2Running) {
        btn.textContent = "Wait For CS2";
        btn.disabled = true;
    } else if (autoEncodeRunning) {
        btn.textContent = "Encoding...";
        btn.disabled = true;
    } else {
        btn.textContent = "Encode Clips";
        btn.disabled = false;
    }
}

// -- Recording --
async function startRecord(launch) {
    if (queue.size === 0) {
        logOutput("Queue is empty. Check kills in the table to add them.", "log-error");
        return;
    }

    const beforeValue = parseFloat($("#rec-before").value);
    const afterValue = parseFloat($("#rec-after").value);
    const framerateValue = parseInt($("#rec-framerate").value, 10);
    const hudMode = $("#rec-hud-mode").value || "deathnotices";
    const before = Number.isNaN(beforeValue) ? 2.0 : beforeValue;
    const after = Number.isNaN(afterValue) ? 1.0 : afterValue;
    const framerate = Number.isNaN(framerateValue) ? 60 : framerateValue;
    const selectedIds = Array.from(queue.keys());

    $("#btn-record").disabled = true;
    $("#btn-generate").disabled = true;
    logOutput(`${launch ? "Recording" : "Generating JSON for"} ${selectedIds.length} kill(s)...`);

    try {
        const data = await apiPost("/api/record", {
            selected_ids: selectedIds,
            before,
            after,
            framerate,
            hud_mode: hudMode,
            launch,
        });

        if (data.diagnostics) {
            for (const d of data.diagnostics) {
                logOutput("  " + d);
            }
        }

        if (!data.ok) {
            logOutput("Error: " + data.error, "log-error");
            if (data.json_path) {
                logOutput(`JSON written: ${data.json_path}`, "log-success");
            }
        } else {
            logOutput(
                `Generated ${data.sequences_count} sequence(s) -> ${data.json_path}`,
                "log-success",
            );
            if (data.launched) {
                logOutput("CS2 launched via HLAE. Old clips were cleaned.", "log-success");
                updateCS2State(true);
                $("#preview-section").style.display = "none";
            }
        }
    } catch (e) {
        logOutput("Error: " + e.message, "log-error");
    }

    updateRecordButton();
}

// -- Clip preview --
async function loadClips() {
    try {
        const data = await apiGet("/api/clips");
        if (!data.ok || !data.clips || data.clips.length === 0) {
            $("#preview-section").style.display = "none";
            return;
        }

        $("#preview-section").style.display = "block";
        const list = $("#clip-list");
        const player = $("#video-player");
        const empty = $("#preview-empty");

        empty.style.display = "none";
        player.style.display = "block";

        list.innerHTML = data.clips
            .map((c) => {
                const label = c.is_combined ? "ALL CLIPS" : c.name.replace(".mp4", "");
                const cls = c.is_combined ? "clip-btn combined" : "clip-btn";
                return `<button class="${cls}" data-filename="${encodeURIComponent(c.name)}" title="${escapeHtml(c.size_mb)} MB">${escapeHtml(label)}</button>`;
            })
            .join("");

        $$(".clip-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                playClip(decodeURIComponent(btn.dataset.filename || ""));
            });
        });

        const combined = data.clips.find((c) => c.is_combined);
        const first = combined || data.clips[0];
        if (first) playClip(first.name);
    } catch (e) {
        // ignore
    }
}

function playClip(filename) {
    const player = $("#video-player");
    player.src = "/clips/" + encodeURIComponent(filename);
    player.load();

    $$(".clip-btn").forEach((btn) => btn.classList.remove("active"));
    const activeBtn = Array.from($$(".clip-btn")).find(
        (btn) => decodeURIComponent(btn.dataset.filename || "") === filename,
    );
    if (activeBtn) activeBtn.classList.add("active");
}

// -- Cleanup --
async function cleanClips() {
    try {
        const data = await apiPost("/api/clean", {});
        if (data.ok) {
            logOutput(`Cleaned ${data.removed} old clip(s)/video(s).`);
            await loadClips();
        }
    } catch (e) {
        logOutput("Clean error: " + e.message, "log-error");
    }
}

// -- Encoding --
async function encodeClips() {
    if (cs2Running || autoEncodeRunning) {
        logOutput("Wait for CS2 to finish recording before encoding.", "log-error");
        return;
    }

    const framerateValue = parseInt($("#rec-framerate").value, 10);
    const framerate = Number.isNaN(framerateValue) ? 60 : framerateValue;

    $("#btn-encode").disabled = true;
    logOutput("Encoding TGA clips to MP4...");

    try {
        const data = await apiPost("/api/encode", {
            framerate,
            concatenate: true,
        });

        if (data.encoded && data.encoded.length > 0) {
            for (const v of data.encoded) {
                logOutput("  Encoded: " + v, "log-success");
            }
        }

        if (data.concatenated) {
            logOutput("  Combined: " + data.concatenated, "log-success");
        }

        if (data.errors && data.errors.length > 0) {
            for (const e of data.errors) {
                logOutput("  " + e, "log-error");
            }
        }

        if (!data.ok) {
            logOutput("Encoding failed: " + (data.error || "see errors above"), "log-error");
        } else {
            logOutput(`Done. ${data.encoded.length} clip(s) encoded.`, "log-success");
            await loadClips();
        }
    } catch (e) {
        logOutput("Encode error: " + e.message, "log-error");
    }

    $("#btn-encode").disabled = false;
}

// -- Init --
document.addEventListener("DOMContentLoaded", () => {
    $$("#filter-player, #filter-weapon, #filter-round-start, #filter-round-end, #filter-side").forEach(
        (el) => el.addEventListener("change", fetchKills),
    );
    $("#filter-headshot").addEventListener("change", fetchKills);

    $("#btn-browse").addEventListener("click", browseDemoFile);
    $("#btn-load").addEventListener("click", loadDemo);
    $("#btn-load-selected").addEventListener("click", () => {
        void loadSelectedDemo();
    });

    $("#btn-clear-filters").addEventListener("click", clearFilters);
    $("#btn-select-all").addEventListener("click", selectAll);
    $("#btn-select-none").addEventListener("click", selectNone);
    $("#btn-clear-queue").addEventListener("click", clearQueue);
    $("#btn-generate").addEventListener("click", () => startRecord(false));
    $("#btn-record").addEventListener("click", () => startRecord(true));
    $("#btn-encode").addEventListener("click", encodeClips);
    $("#btn-clean").addEventListener("click", cleanClips);

    $("#btn-browse-folder").addEventListener("click", () => {
        void browseWatchFolder();
    });
    $("#btn-add-watch").addEventListener("click", () => {
        void addWatchFolder();
    });
    $("#btn-refresh-library").addEventListener("click", () => {
        void loadLibrary();
    });

    $("#demo-path").addEventListener("keydown", (e) => {
        if (e.key === "Enter") void loadDemo();
    });

    $("#watch-folder-path").addEventListener("keydown", (e) => {
        if (e.key === "Enter") void addWatchFolder();
    });

    void pollStatus();
    void loadLibrary({ silent: true });
    setInterval(pollStatus, STATUS_POLL_MS);
    setInterval(() => {
        void loadLibrary({ silent: true });
    }, LIBRARY_POLL_MS);
    updateEncodeButton();
    updateSelectedDemoUI();

    // Keep global access for inline event handlers in table markup.
    window.toggleKill = toggleKill;
    window.removeFromQueue = removeFromQueue;
});

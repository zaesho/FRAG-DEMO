// frag-demo frontend logic

// -- State --
let currentKills = [];          // Last fetched kill list (from filters)
let queue = new Map();          // kill_id -> kill object (the recording queue)
let cs2Running = false;

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

// -- Spinner --
function setLoading(loading) {
    const spinner = $("#load-spinner");
    const btn = $("#btn-load");
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
    } catch (e) { /* ignore */ }
}

function clearLoadedState() {
    currentKills = [];
    queue.clear();
    updateStatus(false);
    $("#demo-info").style.display = "none";
    $("#preview-section").style.display = "none";
    $$("#filters-section, #kills-section, #queue-section, #record-section").forEach(
        (el) => (el.style.display = "none")
    );
    renderTable([]);
    renderQueue();
    updateRecordButton();
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
        populateSelect("#filter-round", (data.rounds || []).map(String));

        updateStatus(true, header.map_name || "?");

        $$("#filters-section, #kills-section, #queue-section, #record-section").forEach(
            (el) => (el.style.display = "block")
        );

        logOutput(
            `Loaded: ${header.map_name || "?"} | ${data.total_kills} kills | tickrate ${header.tickrate || "?"}`,
            "log-success"
        );

        queue.clear();
        renderQueue();
        await fetchKills();
        await loadClips();
    } catch (e) {
        clearLoadedState();
        logOutput("Error: " + e.message, "log-error");
    }

    setLoading(false);
}

function populateSelect(selector, values) {
    const sel = $(selector);
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
    const roundVal = $("#filter-round").value;
    const round_num = roundVal ? parseInt(roundVal) : null;
    const side = $("#filter-side").value || null;

    try {
        const data = await apiPost("/api/kills", {
            player, weapon, headshot, round_num, side,
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
    $("#filter-round").value = "";
    $("#filter-side").value = "";
    fetchKills();
}

// -- Table rendering --
function renderTable(kills) {
    const tbody = $("#kills-tbody");
    const empty = $("#kills-empty");

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
    // Update just the row highlight (no full re-render)
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
    // Only deselect kills currently visible in the table
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
    $("#selected-count").textContent = count + " queued";
    const btn = $("#btn-record");
    const genBtn = $("#btn-generate");

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
    if (cs2Running) {
        btn.textContent = "Wait For CS2";
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
    const before = Number.isNaN(beforeValue) ? 3.0 : beforeValue;
    const after = Number.isNaN(afterValue) ? 2.0 : afterValue;
    const framerate = Number.isNaN(framerateValue) ? 60 : framerateValue;
    const selectedIds = Array.from(queue.keys());

    $("#btn-record").disabled = true;
    $("#btn-generate").disabled = true;
    logOutput(`${launch ? "Recording" : "Generating JSON for"} ${selectedIds.length} kill(s)...`);

    try {
        const data = await apiPost("/api/record", {
            selected_ids: selectedIds,
            before, after, framerate,
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
                "log-success"
            );
            if (data.launched) {
                logOutput("CS2 launched via HLAE. Old clips were cleaned.", "log-success");
                updateCS2State(true);
                // Hide stale preview
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

        // Auto-play the combined clip if available, otherwise the first one
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

    // Highlight active button
    $$(".clip-btn").forEach((btn) => btn.classList.remove("active"));
    const activeBtn = Array.from($$(".clip-btn")).find(
        (btn) => decodeURIComponent(btn.dataset.filename || "") === filename
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
    if (cs2Running) {
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
    $$("#filter-player, #filter-weapon, #filter-round, #filter-side").forEach(
        (el) => el.addEventListener("change", fetchKills)
    );
    $("#filter-headshot").addEventListener("change", fetchKills);

    $("#btn-browse").addEventListener("click", browseDemoFile);
    $("#btn-load").addEventListener("click", loadDemo);
    $("#btn-clear-filters").addEventListener("click", clearFilters);
    $("#btn-select-all").addEventListener("click", selectAll);
    $("#btn-select-none").addEventListener("click", selectNone);
    $("#btn-clear-queue").addEventListener("click", clearQueue);
    $("#btn-generate").addEventListener("click", () => startRecord(false));
    $("#btn-record").addEventListener("click", () => startRecord(true));
    $("#btn-encode").addEventListener("click", encodeClips);
    $("#btn-clean").addEventListener("click", cleanClips);

    $("#demo-path").addEventListener("keydown", (e) => {
        if (e.key === "Enter") loadDemo();
    });

    pollStatus();
    setInterval(pollStatus, 5000);
    updateEncodeButton();
});

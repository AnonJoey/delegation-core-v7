// delegation-core Agnostic Orchestrator Frontend
// Pure Vanilla JS (Strict CSP) - connects to local dashboard_api.py sidecar.

let apiBase = null;
let currentNotePath = null;
let currentRawMode = false;
let currentNoteContent = "";
let isServerActive = true;

// ── API Helpers ───────────────────────────────────────────────────────────────

async function apiGet(path) {
  const res = await fetch(`${apiBase}${path}`);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `${path}: HTTP ${res.status}`);
  return body;
}

async function apiPost(path, payload) {
  const res = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `${path}: HTTP ${res.status}`);
  return body;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function paneState(title, hint = "", icon = "") {
  return `<div class="pane-state">
    ${icon ? `<div class="pane-state-icon">${icon}</div>` : ""}
    <div class="pane-state-title">${escapeHtml(title)}</div>
    ${hint ? `<div class="pane-state-hint">${escapeHtml(hint)}</div>` : ""}
  </div>`;
}

function timeAgo(seconds) {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

// ── Native Window & Server Controls ──────────────────────────────────────────

function setupWindowControls() {
  const minBtn = document.getElementById("win-minimize-btn");
  const maxBtn = document.getElementById("win-maximize-btn");
  const closeBtn = document.getElementById("win-close-btn");

  const invokeFn = window.__TAURI_INTERNALS__?.invoke || window.__TAURI__?.core?.invoke;

  if (minBtn) {
    minBtn.addEventListener("click", async () => {
      if (invokeFn) {
        try { await invokeFn("minimize_window"); } catch (e) { console.error(e); }
      }
    });
  }

  if (maxBtn) {
    maxBtn.addEventListener("click", async () => {
      if (invokeFn) {
        try { await invokeFn("toggle_maximize_window"); } catch (e) { console.error(e); }
      } else {
        if (!document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(() => {});
        } else {
          document.exitFullscreen().catch(() => {});
        }
      }
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", async () => {
      if (invokeFn) {
        try { await invokeFn("close_window"); } catch (e) { console.error(e); }
      } else {
        window.close();
      }
    });
  }
}

function setupServerControls() {
  const powerBtn = document.getElementById("server-power-btn");
  const restartBtn = document.getElementById("server-restart-btn");

  if (powerBtn) {
    powerBtn.addEventListener("click", () => {
      isServerActive = !isServerActive;
      if (isServerActive) {
        powerBtn.classList.add("is-on");
        powerBtn.classList.remove("is-off");
        powerBtn.querySelector(".btn-text").textContent = "Server ON";
        refreshStatus();
        refreshClients();
      } else {
        powerBtn.classList.remove("is-on");
        powerBtn.classList.add("is-off");
        powerBtn.querySelector(".btn-text").textContent = "Server OFF";
        document.getElementById("status-fields").innerHTML = `<span class="muted">Server Paused</span>`;
      }
    });
  }

  if (restartBtn) {
    restartBtn.addEventListener("click", async () => {
      const icon = restartBtn.querySelector("svg");
      if (icon) icon.style.animation = "spin 0.8s linear infinite";
      restartBtn.disabled = true;
      try {
        try { await apiPost("/api/llama/stop", {}); } catch (e) {}
        await new Promise((r) => setTimeout(r, 800));
        await refreshStatus();
        await refreshClients();
        await loadNotesBrowser();
        await initVaultGraph();
      } catch (e) {
        console.error("Restart error:", e);
      } finally {
        if (icon) icon.style.animation = "";
        restartBtn.disabled = false;
      }
    });
  }
}

// ── System Status & Telemetry (7 Standard Fields) ─────────────────────────────

function dot(ok) {
  return `<span class="dot ${ok ? "ok" : "bad"}"></span>`;
}

function stat(label, valueHtml) {
  return `<span class="stat"><span class="stat-label">${label}</span>${valueHtml}</span>`;
}

let llamaTogglePending = false;

function updateLlamaButton(llamaState) {
  const btn = document.getElementById("llama-toggle-btn");
  if (llamaTogglePending) return;
  btn.disabled = false;
  const isOnline = llamaState === "online" || llamaState === "unhealthy";
  btn.dataset.action = isOnline ? "stop" : "start";
  btn.querySelector(".btn-text").textContent = isOnline ? "Stop llama.cpp" : "Start llama.cpp";
}

async function refreshStatus() {
  if (!isServerActive) return;
  const el = document.getElementById("status-fields");
  try {
    const s = await apiGet("/api/status");
    el.innerHTML = [
      stat("Vault", `${dot(s.vault_ok)}${s.vault_ok ? "ok" : "missing"}`),
      stat("Binary", `${dot(s.binary_ok)}${s.binary_ok ? "ok" : "missing"}`),
      stat("Model", `${dot(s.model_ok)}${s.model_ok ? "ok" : "missing"}`),
      stat("llama.cpp", `${dot(s.llama_state === "online")}${escapeHtml(s.llama_state)}`),
      stat("Indexed notes", escapeHtml(s.chroma_indexed_notes ?? "—")),
      stat("Engine mode", escapeHtml(s.engine_mode)),
      stat("Synthesis", s.synthesis_enabled ? "on" : "off"),
    ].join("");

    // The version the server actually is. This was hardcoded in index.html and
    // said v0.9.0 while the source tree was at 0.10.0 — kept blank until the
    // server answers rather than shipping another copy to go stale.
    const badge = document.getElementById("brand-badge");
    if (badge && s.version) badge.textContent = `Orchestrator v${s.version}`;

    // Sync Obsidian Statusbar
    const sbNotes = document.getElementById("sb-notes-val");
    if (sbNotes && s.chroma_indexed_notes !== undefined) sbNotes.textContent = `${s.chroma_indexed_notes} notes`;
    const sbModel = document.getElementById("sb-model-val");
    if (sbModel && s.bge_model) sbModel.textContent = s.bge_model.rsplit ? s.bge_model.rsplit('/', 1).pop() : s.bge_model;
    const sbEngine = document.getElementById("sb-engine-val");
    if (sbEngine && s.engine_mode) sbEngine.textContent = s.engine_mode;

    updateLlamaButton(s.llama_state);
  } catch (e) {
    el.innerHTML = `<span class="muted">status unavailable — retrying</span>`;
  }
}

async function toggleLlama() {
  const btn = document.getElementById("llama-toggle-btn");
  const action = btn.dataset.action;
  llamaTogglePending = true;
  btn.disabled = true;
  btn.querySelector(".btn-text").textContent = action === "start" ? "Starting…" : "Stopping…";
  try {
    await apiPost(`/api/llama/${action}`, {});
  } catch (e) {
    alert(`Could not ${action} llama.cpp: ${e.message}`);
  }
  llamaTogglePending = false;
  setTimeout(refreshStatus, action === "start" ? 3000 : 1500);
}

// ── Connected MCP Clients ────────────────────────────────────────────────────

async function refreshClients() {
  if (!isServerActive) return;
  const el = document.getElementById("clients-list");
  const fleetGrid = document.getElementById("fleet-grid");

  try {
    const { clients } = await apiGet("/api/clients");
    if (!clients.length) {
      el.innerHTML = `<span class="muted">no clients connected</span>`;
      if (fleetGrid) {
        fleetGrid.innerHTML = paneState("No Connected MCP Clients", "No active stdio MCP server instances detected.", "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M12 2a10 10 0 0 1 10 10'></path><path d='M12 8a6 6 0 0 1 6 6'></path><circle cx='6' cy='18' r='3'></circle></svg>");
      }
      return;
    }

    el.innerHTML = clients
      .map(
        (c) => `
      <span class="client-chip">
        <span class="client-name">${escapeHtml(c.client_name ?? "unknown")}</span>
        <span class="client-meta">v${escapeHtml(c.client_version ?? "?")} · ${c.tool_calls} calls · ${timeAgo(c.seconds_since_active)}</span>
      </span>`
      )
      .join("");

    if (fleetGrid) {
      fleetGrid.innerHTML = clients
        .map(
          (c) => `
        <div class="fleet-card">
          <div class="fleet-card-header">
            <span class="dot ok"></span>
            <span class="fleet-client-name">${escapeHtml(c.client_name ?? "Unknown Client")}</span>
            <span class="count">v${escapeHtml(c.client_version ?? "1.0")}</span>
          </div>
          <div style="font-size: 12px; color: var(--fg-muted); margin-bottom: 8px;">
            PID: <code>${c.pid}</code> · Active: ${timeAgo(c.seconds_since_active)}
          </div>
          <div style="font-size: 12px;">
            <strong>${c.tool_calls}</strong> MCP tool invocations recorded this session.
          </div>
        </div>`
        )
        .join("");
    }
  } catch (e) {
    el.innerHTML = `<span class="muted">clients unavailable — retrying</span>`;
  }
}

// ── Tab & Navigation Switching ───────────────────────────────────────────────

// ── Tab navigation, Obsidian-style ───────────────────────────────────────────
// Three things make Obsidian's tabs feel fluid rather than merely functional:
// the active tab survives a restart, the keyboard reaches every tab without the
// mouse, and the indicator travels instead of blinking to its new place. All three
// are cheap here; none needs a framework.

const TAB_STORAGE_KEY = "dc.activeTab";

function tabButtons() {
  return Array.from(document.querySelectorAll(".ribbon .ribbon-btn[data-tab]"));
}

/** Slide the marker to sit alongside the active ribbon button. */
function moveTabIndicator(btn) {
  const indicator = document.getElementById("tab-indicator");
  if (!indicator || !btn) return;
  // offsetTop is relative to .ribbon, which is the indicator's positioned parent.
  indicator.style.height = `${btn.offsetHeight}px`;
  indicator.style.transform = `translateY(${btn.offsetTop}px)`;
}

// Each section owns both columns: a navigator in the left dock and its content in
// main. Switching sections swaps both. Leaving the vault tree up while looking at,
// say, the MCP fleet gave the dock nothing to do — the navigator has to belong to
// the section, which is how Obsidian's ribbon behaves.
const DOCK_TITLES = {
  notes: "Vault",
  tasks: "Processes",
  search: "Search",
  fleet: "MCP Connections",
};

// Search has no content of its own: picking a result opens a note, and notes are
// read in the same place they are read from the tree. Mapping it onto the notes
// panel avoids both an empty column and a second, competing reader.
const MAIN_FOR = { notes: "notes", search: "notes", tasks: "tasks", fleet: "fleet" };

function activateTab(name, { persist = true } = {}) {
  const btn = tabButtons().find((b) => b.dataset.tab === name);
  const panel = document.getElementById(`tab-${MAIN_FOR[name] || name}`);
  if (!btn || !panel) return false;

  document.querySelectorAll(".dock-panel").forEach((d) => d.classList.remove("active"));
  const dock = document.getElementById(`dock-${name}`);
  if (dock) dock.classList.add("active");
  const dockTitle = document.getElementById("dock-title");
  if (dockTitle) dockTitle.textContent = DOCK_TITLES[name] || name;
  if (name === "fleet") refreshMcpConnections();

  tabButtons().forEach((b) => {
    const on = b === btn;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
  panel.classList.add("active");

  moveTabIndicator(btn);
  if (persist) {
    try { localStorage.setItem(TAB_STORAGE_KEY, name); } catch { /* private mode */ }
  }
  return true;
}

function cycleTab(step) {
  const btns = tabButtons();
  const i = btns.findIndex((b) => b.classList.contains("active"));
  if (i === -1) return;
  // wrap in both directions, so Ctrl+Shift+Tab from the first lands on the last
  const next = btns[(i + step + btns.length) % btns.length];
  activateTab(next.dataset.tab);
}

// Collapsing the graph pane, as Obsidian collapses a dock. The state persists,
// because a pane you deliberately hid should stay hidden across restarts.
const GRAPH_PANE_KEY = "dc.graphPaneCollapsed";

function setGraphPaneCollapsed(collapsed) {
  const pane = document.getElementById("graph-pane");
  const btn = document.getElementById("btn-toggle-graph-pane");
  if (!pane) return;
  pane.classList.toggle("collapsed", collapsed);
  if (btn) {
    btn.classList.toggle("collapsed", collapsed);
    btn.setAttribute("aria-pressed", collapsed ? "true" : "false");
  }
  try { localStorage.setItem(GRAPH_PANE_KEY, collapsed ? "1" : "0"); } catch { /* ignore */ }
  // No manual canvas resize here: ForceGraph already observes its own parent, and
  // going from display:none back to visible fires that observer on its own.
}

function setupGraphPaneToggle() {
  const btn = document.getElementById("btn-toggle-graph-pane");
  if (!btn) return;
  let collapsed = false;
  try { collapsed = localStorage.getItem(GRAPH_PANE_KEY) === "1"; } catch { /* ignore */ }
  setGraphPaneCollapsed(collapsed);

  btn.addEventListener("click", () => {
    setGraphPaneCollapsed(!document.getElementById("graph-pane").classList.contains("collapsed"));
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "\\") {
      e.preventDefault();
      setGraphPaneCollapsed(!document.getElementById("graph-pane").classList.contains("collapsed"));
    }
  });
}

function setupNavigation() {
  tabButtons().forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });

  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key === "Tab") {
      e.preventDefault();
      cycleTab(e.shiftKey ? -1 : 1);
      return;
    }
    // Ctrl+1..9 jumps straight to a tab, as in Obsidian and every browser
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= 9) {
      const btn = tabButtons()[n - 1];
      if (btn) {
        e.preventDefault();
        activateTab(btn.dataset.tab);
      }
    }
  });

  // Restore the tab from last run; fall back to whichever is marked active in the
  // markup if the stored one no longer exists.
  let restored = false;
  try {
    const saved = localStorage.getItem(TAB_STORAGE_KEY);
    if (saved) restored = activateTab(saved, { persist: false });
  } catch { /* ignore */ }
  if (!restored) {
    const current = tabButtons().find((b) => b.classList.contains("active")) || tabButtons()[0];
    if (current) activateTab(current.dataset.tab, { persist: false });
  }

  // The indicator is positioned from measured geometry, so it has to be
  // recomputed whenever that geometry can change.
  const nav = document.querySelector(".ribbon");
  if (nav && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      const active = tabButtons().find((b) => b.classList.contains("active"));
      moveTabIndicator(active);
    }).observe(nav);
  }

  setupGraphPaneToggle();

  document.getElementById("btn-toggle-vault-graph").addEventListener("click", () => switchGraphMode("vault"));
  document.getElementById("btn-toggle-code-graph").addEventListener("click", () => switchGraphMode("code"));
}

function switchGraphMode(mode) {
  const btnVault = document.getElementById("btn-toggle-vault-graph");
  const btnCode = document.getElementById("btn-toggle-code-graph");
  const vaultView = document.getElementById("vault-graph-view");
  const codeView = document.getElementById("code-graph-view");
  const indicator = document.getElementById("graph-type-indicator");

  if (mode === "vault") {
    btnVault.classList.add("active");
    btnCode.classList.remove("active");
    vaultView.hidden = false;
    codeView.hidden = true;
    indicator.textContent = "Wikilinks Network";
  } else {
    btnCode.classList.add("active");
    btnVault.classList.remove("active");
    codeView.hidden = false;
    vaultView.hidden = true;
    indicator.textContent = "Code AST (Graphify)";
    loadCodeGraphs();
  }
}

// ── Knowledge Vault Browser & Markdown Reader ────────────────────────────────

let vaultDirs = null;          // [{path, name, depth, count}] — 25 entries here
let collapsedDirs = new Set();  // dir paths the user folded shut
let currentListing = null;      // {dir, total, offset, notes, has_more}
let findResults = null;         // literal-search results, when the filter is active
let selectedFolder = null;
const NOTES_PAGE = 200;

function renderFolderList() {
  const folderList = document.getElementById("folder-list");
  if (!vaultDirs) return;
  if (!vaultDirs.length) {
    folderList.innerHTML = paneState("No Folders", "The vault has no notes yet.");
    return;
  }

  // A directory is hidden when any ancestor is collapsed. Depth is precomputed
  // server-side, so rendering is a flat map with an indent — no recursion, and
  // no virtualization needed for 25 rows.
  const hidden = (path) => {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) {
      if (collapsedDirs.has(parts.slice(0, i).join("/"))) return true;
    }
    return false;
  };
  const hasChildren = (path) => vaultDirs.some((d) => d.path.startsWith(path + "/"));

  folderList.innerHTML = vaultDirs
    .filter((d) => !hidden(d.path))
    .map((d) => {
      const kids = hasChildren(d.path);
      const folded = collapsedDirs.has(d.path);
      const caret = kids ? (folded ? "▸" : "▾") : "&nbsp;";
      return `<div class="list-item dir-row ${d.path === selectedFolder ? "active" : ""}"
        data-folder="${escapeHtml(d.path)}" style="padding-left:${0.5 + d.depth * 0.85}rem" tabindex="0">
        <span class="dir-caret" data-toggle="${escapeHtml(d.path)}">${caret}</span>
        ${escapeHtml(d.name)} <span class="count">${d.count}</span>
      </div>`;
    })
    .join("");

  folderList.querySelectorAll(".dir-caret").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const path = el.dataset.toggle;
      collapsedDirs.has(path) ? collapsedDirs.delete(path) : collapsedDirs.add(path);
      renderFolderList();
    });
  });
  folderList.querySelectorAll(".list-item").forEach((el) => {
    el.addEventListener("click", () => selectFolder(el.dataset.folder));
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") selectFolder(el.dataset.folder);
    });
  });
}

function renderNoteList() {
  const noteList = document.getElementById("note-list");
  const countBadge = document.getElementById("note-count-badge");

  // Filter active → show literal-search hits across the whole vault instead of
  // one directory. The old filter was a substring test over whichever notes had
  // already been loaded, so it could not see past the first page.
  const listing = findResults
    ? { notes: findResults.results, total: findResults.count, has_more: false, offset: 0 }
    : currentListing;

  if (!listing) {
    noteList.innerHTML = paneState("Pick a folder", "Choose a directory to list its notes.");
    if (countBadge) countBadge.textContent = "0";
    return;
  }
  if (countBadge) countBadge.textContent = listing.total;

  if (!listing.notes.length) {
    noteList.innerHTML = findResults
      ? paneState("No matches", "No note title or path contains that text.")
      : paneState("Empty folder", "Notes filed here will appear in this list.");
    return;
  }

  const rows = listing.notes
    .map((n) => `<div class="list-item" data-path="${escapeHtml(n.path)}" tabindex="0">
      ${escapeHtml(n.title)}${findResults ? `<span class="note-dir">${escapeHtml(n.path.split("/").slice(0, -1).join("/"))}</span>` : ""}
    </div>`)
    .join("");

  const shown = listing.offset + listing.notes.length;
  const more = listing.has_more
    ? `<button class="list-more" id="notes-load-more">Load more — showing ${shown} of ${listing.total}</button>`
    : "";
  noteList.innerHTML = rows + more;

  noteList.querySelectorAll(".list-item").forEach((el) => {
    el.addEventListener("click", () => selectNote(el.dataset.path, el));
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") selectNote(el.dataset.path, el);
    });
  });
  const moreBtn = document.getElementById("notes-load-more");
  if (moreBtn) moreBtn.addEventListener("click", () => loadNotes(selectedFolder, shown, true));
}

async function loadNotesBrowser() {
  const folderList = document.getElementById("folder-list");
  try {
    const data = await apiGet("/api/vault/tree");
    vaultDirs = data.directories || [];
  } catch (e) {
    folderList.innerHTML = paneState("Vault unavailable", "Could not load the directory tree.");
    return;
  }
  // Generated code-graph articles live under <folder>/graphs/<name>/ and number
  // in the thousands; start them folded so the browser opens on real notes.
  vaultDirs.forEach((d) => {
    if (d.path.endsWith("/graphs")) collapsedDirs.add(d.path);
  });
  renderFolderList();
  if (vaultDirs.length) selectFolder(vaultDirs[0].path);
}

async function loadNotes(dir, offset = 0, append = false) {
  try {
    const data = await apiGet(`/api/vault/notes?dir=${encodeURIComponent(dir)}&offset=${offset}&limit=${NOTES_PAGE}`);
    if (data.error) throw new Error(data.error);
    currentListing = append && currentListing
      ? { ...data, notes: currentListing.notes.concat(data.notes), offset: 0 }
      : data;
  } catch (e) {
    currentListing = { dir, total: 0, offset: 0, notes: [], has_more: false };
  }
  renderNoteList();
}

function selectFolder(dir) {
  selectedFolder = dir;
  document.querySelectorAll("#folder-list .list-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.folder === dir);
  });
  loadNotes(dir);
}

function setupNotesFilter() {
  const input = document.getElementById("notes-filter");
  let timer = null;
  input.addEventListener("input", () => {
    const q = input.value.trim();
    clearTimeout(timer);
    // Debounced because this is a server round-trip now, not a local filter.
    timer = setTimeout(async () => {
      if (!q) {
        findResults = null;
        renderNoteList();
        return;
      }
      try {
        findResults = await apiGet(`/api/vault/find?q=${encodeURIComponent(q)}&limit=100`);
      } catch (e) {
        findResults = { results: [], count: 0 };
      }
      renderNoteList();
    }, 180);
  });
}

async function selectNoteByTitle(title) {
  // Literal lookup: the semantic endpoint cannot be trusted to return an exact
  // title — one written minutes earlier did not make its own top 3.
  try {
    const data = await apiGet(`/api/vault/find?q=${encodeURIComponent(title)}&limit=1`);
    if (data.results && data.results.length) selectNote(data.results[0].path, null);
  } catch (e) {
    /* nothing to open */
  }
}

// Backlinks were always computed server-side (linker.inject_backlinks writes
// them into note bodies on every write) but never shown as a relation, so a
// reader saw only whatever text happened to be in the note. Half the
// hand-written notes here have inbound links, so this is not a corner feature.
async function renderNoteLinks(path) {
  const el = document.getElementById("note-links");
  if (!el) return;
  el.hidden = true;
  el.innerHTML = "";
  let data;
  try {
    data = await apiGet(`/api/vault/backlinks?path=${encodeURIComponent(path)}`);
  } catch (e) {
    return;
  }
  if (data.error) return;
  // The note may have been switched while this request was in flight.
  if (currentNotePath !== path) return;
  if (!data.inbound.length && !data.outbound.length) return;

  const section = (title, rows) =>
    rows.length
      ? `<div class="note-links-group"><h4>${title}</h4>${rows.join("")}</div>`
      : "";

  const inbound = data.inbound.map(
    (n) => `<a class="note-link-row" data-path="${escapeHtml(n.path)}">${escapeHtml(n.title)}</a>`
  );
  // Broken targets are shown, not dropped: a note pointing at something that no
  // longer exists would otherwise look well-connected.
  const outbound = data.outbound.map((o) =>
    o.broken
      ? `<span class="note-link-row broken" title="No note with this name">${escapeHtml(o.target)} — missing</span>`
      : `<a class="note-link-row" data-path="${escapeHtml(o.path)}">${escapeHtml(o.target)}</a>`
  );

  el.innerHTML =
    section(`Linked from (${data.inbound_count})`, inbound) +
    section(`Links to (${data.outbound.length})`, outbound);
  el.hidden = false;
  el.querySelectorAll("a.note-link-row").forEach((a) => {
    a.addEventListener("click", () => selectNote(a.dataset.path, null));
  });
}

async function selectNote(path, el) {
  // Leaving edit mode on switch: otherwise the editor keeps the previous note's
  // text while the header shows the new one, and Save would overwrite the wrong
  // file with it.
  if (editing) setEditChrome(false);
  currentNotePath = path;
  document.querySelectorAll("#note-list .list-item").forEach((e) => e.classList.remove("active"));
  if (el) el.classList.add("active");

  const header = document.getElementById("note-view-header");
  const state = document.getElementById("note-state");
  const wrapper = document.getElementById("note-body-wrapper");
  const titleEl = document.getElementById("note-view-title");
  const folderEl = document.getElementById("note-folder-badge");
  const renderedEl = document.getElementById("note-rendered");
  const rawEl = document.getElementById("note-content");
  const frontmatterEl = document.getElementById("note-frontmatter");

  wrapper.hidden = true;
  header.hidden = true;
  state.hidden = false;
  state.innerHTML = paneState("Loading note…", "", "loading");

  try {
    const note = await apiGet(`/api/vault/note?path=${encodeURIComponent(path)}`);
    currentNoteContent = note.content;

    titleEl.textContent = note.title;
    folderEl.textContent = path.split("/")[0] || "Vault";
    header.hidden = false;

    let content = note.content;
    frontmatterEl.hidden = true;
    if (content.startsWith("---\n")) {
      const endIdx = content.indexOf("\n---\n", 4);
      if (endIdx !== -1) {
        const fmText = content.substring(4, endIdx);
        content = content.substring(endIdx + 5);
        frontmatterEl.innerHTML = parseFrontmatterHTML(fmText);
        frontmatterEl.hidden = false;
      }
    }

    renderedEl.innerHTML = renderMarkdown(content);
    rawEl.textContent = note.content;

    renderedEl.querySelectorAll(".wikilink-badge").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        const target = link.dataset.target;
        selectNoteByTitle(target);
      });
    });

    renderNoteLinks(path);

    state.hidden = true;
    wrapper.hidden = false;
    activateTab("notes");
  } catch (e) {
    state.innerHTML = paneState("Could not load note", "The file may have been moved or deleted.");
  }
}

function parseFrontmatterHTML(fmText) {
  const lines = fmText.split("\n");
  return lines
    .map((line) => {
      const parts = line.split(":");
      if (parts.length < 2) return "";
      const key = parts[0].trim();
      const val = parts.slice(1).join(":").trim();
      return `<div class="frontmatter-tag"><span class="stat-label">${escapeHtml(key)}:</span> <span class="frontmatter-val">${escapeHtml(val)}</span></div>`;
    })
    .join("");
}

function renderMarkdown(text) {
  let html = escapeHtml(text);

  html = html.replace(/^### (.*$)/gim, "<h3>$1</h3>");
  html = html.replace(/^## (.*$)/gim, "<h2>$1</h2>");
  html = html.replace(/^# (.*$)/gim, "<h1>$1</h1>");

  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>");

  html = html.replace(/\[\[([^\]\|]+)(?:\|([^\]]+))?\]\]/g, (match, target, display) => {
    const label = display || target;
    return `<a class="wikilink-badge" data-target="${escapeHtml(target.trim())}"><svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7'></path><path d='M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7'></path></svg> ${escapeHtml(label.trim())}</a>`;
  });

  html = html.replace(/\n\n/g, "</p><p>");
  return `<p>${html}</p>`;
}

function setupNoteActions() {
  document.getElementById("btn-toggle-raw").addEventListener("click", () => {
    currentRawMode = !currentRawMode;
    const btn = document.getElementById("btn-toggle-raw");
    const rendered = document.getElementById("note-rendered");
    const raw = document.getElementById("note-content");
    btn.textContent = currentRawMode ? "Formatted" : "Raw";
    rendered.hidden = currentRawMode;
    raw.hidden = !currentRawMode;
  });

  document.getElementById("btn-copy-path").addEventListener("click", () => {
    if (currentNotePath) {
      navigator.clipboard.writeText(currentNotePath);
      const btn = document.getElementById("btn-copy-path");
      btn.textContent = "Copied!";
      setTimeout(() => (btn.textContent = "Copy Path"), 1500);
    }
  });

  document.getElementById("btn-edit-note").addEventListener("click", () => enterEditMode());
  document.getElementById("btn-cancel-edit").addEventListener("click", () => exitEditMode());
  document.getElementById("btn-save-note").addEventListener("click", () => saveCurrentNote());
  document.getElementById("btn-new-note").addEventListener("click", () => createNoteHere());
  document.getElementById("btn-rename-note").addEventListener("click", () => renameCurrentNote());
}

// ── Editing ───────────────────────────────────────────────────────────────────
// Writes go through the server (POST /api/vault/note/*), which owns indexing.
// Writing straight to disk from Tauri would be faster and would leave the note
// unindexed until something else noticed — a second write path free to drift
// from the one the MCP tools use.

let editing = false;

function setEditChrome(on) {
  editing = on;
  document.getElementById("btn-edit-note").hidden = on;
  document.getElementById("btn-save-note").hidden = !on;
  document.getElementById("btn-cancel-edit").hidden = !on;
  document.getElementById("note-editor").hidden = !on;
  document.getElementById("note-rendered").hidden = on || currentRawMode;
  document.getElementById("note-content").hidden = on || !currentRawMode;
  document.getElementById("note-links").hidden = on;
}

function editorStatus(text, isError) {
  const el = document.getElementById("note-editor-status");
  el.textContent = text;
  el.classList.toggle("error", Boolean(isError));
  el.hidden = !text;
}

function enterEditMode() {
  if (!currentNotePath) return;
  // The raw pane already holds the exact file text, frontmatter included —
  // which is what save writes back, so the editor starts from it verbatim.
  document.getElementById("note-editor").value =
    document.getElementById("note-content").textContent;
  setEditChrome(true);
  editorStatus("");
}

function exitEditMode() {
  setEditChrome(false);
  editorStatus("");
}

async function saveCurrentNote() {
  if (!currentNotePath) return;
  const content = document.getElementById("note-editor").value;
  editorStatus("Saving…");
  try {
    const res = await apiPost("/api/vault/note/save", { path: currentNotePath, content });
    if (res.error) throw new Error(res.error);
    exitEditMode();
    await selectNote(currentNotePath, null);   // re-read from disk, re-render, refresh links
  } catch (e) {
    editorStatus(`Save failed: ${e.message}`, true);
  }
}

async function renameCurrentNote() {
  if (!currentNotePath) return;
  const current = currentNotePath.split("/").pop().replace(/\.md$/, "");
  const title = prompt("Rename note to:", current);
  if (!title || !title.trim() || title.trim() === current) return;
  editorStatus("Renaming…");
  try {
    const res = await apiPost("/api/vault/note/rename", {
      path: currentNotePath,
      new_title: title.trim(),
    });
    if (res.error) throw new Error(res.error);
    // The server repoints every [[wikilink]] aimed at the old stem; say how
    // many, since that is the part a user cannot verify by looking.
    editorStatus(`Renamed — ${res.links_rewritten} note(s) repointed`);
    await loadNotes(selectedFolder);
    await selectNote(res.path, null);
  } catch (e) {
    editorStatus(`Rename failed: ${e.message}`, true);
  }
}

async function createNoteHere() {
  const folder = (selectedFolder || "").split("/")[0];
  if (!folder) return;
  const title = prompt(`New note in ${folder}:`);
  if (!title || !title.trim()) return;
  try {
    const res = await apiPost("/api/vault/note/create", {
      folder,
      title: title.trim(),
      content: "## Summary\n\n",
    });
    if (res.error) throw new Error(res.error);
    await loadNotes(selectedFolder);
    await selectNote(res.path, null);
  } catch (e) {
    editorStatus(`Create failed: ${e.message}`, true);
  }
}

// ── Vector Semantic Search Studio ──────────────────────────────────────────────

function setupVectorSearch() {
  const input = document.getElementById("vector-search-input");
  const btn = document.getElementById("vector-search-btn");
  const resultsPane = document.getElementById("vector-search-results");

  async function executeSearch() {
    const query = input.value.trim();
    if (!query) return;

    resultsPane.innerHTML = paneState("Searching vector space…", "", "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><circle cx='11' cy='11' r='8'></circle><line x1='21' y1='21' x2='16.65' y2='16.65'></line></svg>");
    try {
      const data = await apiGet(`/api/vault/search?q=${encodeURIComponent(query)}&limit=8`);
      if (!data.results || !data.results.length) {
        resultsPane.innerHTML = paneState("No Vector Matches", "No notes exceeded similarity threshold.");
        return;
      }

      resultsPane.innerHTML = data.results
        .map((r) => {
          const simPct = Math.round((r.similarity || 0) * 100);
          return `
          <div class="search-result-card" data-path="${escapeHtml(r.path)}">
            <div class="search-card-header">
              <span class="search-card-title">${escapeHtml(r.title)}</span>
              <span class="similarity-badge">${simPct}% match</span>
            </div>
            <div style="font-size: 11px; color: var(--fg-dim); margin-bottom: 6px;"><svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'></path></svg> ${escapeHtml(r.path)}</div>
            <div style="font-size: 12.5px; color: var(--fg-muted); line-height: 1.4;">${escapeHtml(r.snippet || "Click to view note...")}</div>
          </div>`;
        })
        .join("");

      resultsPane.querySelectorAll(".search-result-card").forEach((card) => {
        card.addEventListener("click", () => {
          selectNote(card.dataset.path, null);
        });
      });
    } catch (e) {
      resultsPane.innerHTML = paneState("Search Error", e.message, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'></path><line x1='12' y1='9' x2='12' y2='13'></line><line x1='12' y1='17' x2='12.01' y2='17'></line></svg>");
    }
  }

  btn.addEventListener("click", executeSearch);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") executeSearch();
  });
}

// ── Code AST Graphs (Graphify Integration) ──────────────────────────────────

async function loadCodeGraphs() {
  const select = document.getElementById("code-graph-select");
  const details = document.getElementById("code-graph-details");

  try {
    const data = await apiGet("/api/graphs");
    const graphs = data.graphs || {};
    const keys = Object.keys(graphs);

    if (!keys.length) {
      select.innerHTML = `<option value="">No code graphs registered</option>`;
      details.innerHTML = paneState("No AST Graphs", "Run `delegation-core graph build <path>` to build a code knowledge graph.", "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M5 3v4a7 7 0 0 0 14 0V3'></path><path d='M5 21v-4a7 7 0 0 1 14 0v4'></path><line x1='7' y1='9' x2='17' y2='9'></line><line x1='7' y1='15' x2='17' y2='15'></line></svg>");
      return;
    }

    select.innerHTML = `<option value="">Select registered graph (${keys.length})</option>` +
      keys.map((k) => `<option value="${escapeHtml(k)}">${escapeHtml(k)} (${graphs[k].node_count || 0} nodes)</option>`).join("");

    select.onchange = async () => {
      const val = select.value;
      if (!val) return;
      details.innerHTML = paneState("Loading graph report…", "", "⏳");
      try {
        const report = await apiGet(`/api/graphs/get?name=${encodeURIComponent(val)}`);
        details.innerHTML = `
          <div style="padding: 12px; background: rgba(23,32,54,0.4); border-radius: 8px; margin-bottom: 12px;">
            <h3 style="margin: 0 0 8px; color: var(--accent-cyan);">${escapeHtml(val)} Code Graph</h3>
            <pre style="white-space: pre-wrap; font-size: 12px; color: var(--fg-muted); max-height: 350px; overflow-y: auto;">${escapeHtml(report.report)}</pre>
          </div>`;
      } catch (err) {
        details.innerHTML = paneState("Could not load report", err.message, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'></path><line x1='12' y1='9' x2='12' y2='13'></line><line x1='12' y1='17' x2='12.01' y2='17'></line></svg>");
      }
    };
  } catch (e) {
    details.innerHTML = paneState("Graph Error", e.message, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'></path><line x1='12' y1='9' x2='12' y2='13'></line><line x1='12' y1='17' x2='12.01' y2='17'></line></svg>");
  }
}

// ── Process & Task Orchestrator ──────────────────────────────────────────────

let currentProcessStatusFilter = "active";
let selectedProcessId = null;

function setupProcesses() {
  const selectFilter = document.getElementById("tasks-status-filter");
  if (selectFilter) {
    selectFilter.addEventListener("change", () => {
      currentProcessStatusFilter = selectFilter.value;
      loadProcessList();
    });
  }

  const modal = document.getElementById("new-task-modal");
  document.getElementById("tasks-new-btn").addEventListener("click", () => (modal.hidden = false));
  document.getElementById("modal-close-btn").addEventListener("click", () => (modal.hidden = true));
  document.getElementById("modal-cancel-btn").addEventListener("click", () => (modal.hidden = true));

  document.getElementById("new-task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("task-name").value.trim();
    const description = document.getElementById("task-desc").value.trim();
    const stepsRaw = document.getElementById("task-steps").value;
    const steps = stepsRaw.split("\n").map((s) => s.trim()).filter(Boolean);

    try {
      const proc = await apiPost("/api/processes/create", { name, description, steps });
      modal.hidden = true;
      document.getElementById("new-task-form").reset();
      loadProcessList();
      selectProcess(proc.process_id);
    } catch (err) {
      alert(`Could not create process: ${err.message}`);
    }
  });

  loadProcessList();
}

async function loadProcessList() {
  const listEl = document.getElementById("task-list");
  try {
    const data = await apiGet(`/api/processes?status=${currentProcessStatusFilter}`);
    const procs = data.processes || [];

    if (!procs.length) {
      listEl.innerHTML = paneState("No processes", `No ${currentProcessStatusFilter} processes.`, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><rect x='8' y='2' width='8' height='4' rx='1'></rect><path d='M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2'></path></svg>");
      return;
    }

    listEl.innerHTML = procs
      .map((p) => {
        const total = p.steps ? p.steps.length : 0;
        const done = p.steps ? p.steps.filter((s) => s.done).length : 0;
        const pct = total ? Math.round((done / total) * 100) : 0;
        const isSel = p.process_id === selectedProcessId;

        return `
        <div class="task-card ${isSel ? "active" : ""}" data-id="${escapeHtml(p.process_id)}">
          <div class="task-card-title">${escapeHtml(p.name)}</div>
          <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--fg-dim);">
            <span class="count">${escapeHtml(p.status)}</span>
            <span>${done}/${total} steps</span>
          </div>
          <div class="task-progress-bar">
            <div class="task-progress-fill" style="width: ${pct}%;"></div>
          </div>
        </div>`;
      })
      .join("");

    listEl.querySelectorAll(".task-card").forEach((card) => {
      card.addEventListener("click", () => selectProcess(card.dataset.id));
    });
  } catch (e) {
    listEl.innerHTML = paneState("Error loading tasks", e.message, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'></path><line x1='12' y1='9' x2='12' y2='13'></line><line x1='12' y1='17' x2='12.01' y2='17'></line></svg>");
  }
}

async function selectProcess(id) {
  selectedProcessId = id;
  loadProcessList();
  const detailEl = document.getElementById("task-detail");
  detailEl.innerHTML = paneState("Loading process…", "", "loading");

  try {
    const p = await apiGet(`/api/processes/get?id=${encodeURIComponent(id)}`);
    const total = p.steps ? p.steps.length : 0;
    const doneCount = p.steps ? p.steps.filter((s) => s.done).length : 0;

    detailEl.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px;">
        <div>
          <span class="note-folder-badge">${escapeHtml(p.status)}</span>
          <h2 style="margin: 6px 0 2px; color: var(--fg-main);">${escapeHtml(p.name)}</h2>
          <span style="font-size: 11px; color: var(--fg-dim);">ID: <code>${escapeHtml(p.process_id)}</code></span>
        </div>
        <div style="display: flex; gap: 6px;">
          ${p.status === "active" ? `<button class="btn-action-sm" onclick="updateProcessStatus('${p.process_id}', 'paused')">Pause</button>` : ""}
          ${p.status === "paused" ? `<button class="btn-action-sm" onclick="updateProcessStatus('${p.process_id}', 'active')">Resume</button>` : ""}
          ${p.status !== "done" ? `<button class="btn-action-sm" onclick="updateProcessStatus('${p.process_id}', 'done')">Complete</button>` : ""}
          ${p.status !== "cancelled" ? `<button class="btn-action-sm" onclick="updateProcessStatus('${p.process_id}', 'cancelled')">Cancel</button>` : ""}
        </div>
      </div>

      <p style="color: var(--fg-muted); font-size: 13px;">${escapeHtml(p.description || "No description provided.")}</p>

      <h3 style="font-size: 12px; text-transform: uppercase; color: var(--fg-dim); margin-top: 20px; letter-spacing: 0.05em;">Execution Steps (${doneCount}/${total})</h3>
      <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px;">
        ${(p.steps || []).map((s, idx) => `
          <label style="display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: rgba(23,32,54,0.4); border: 1px solid var(--border-subtle); border-radius: 6px; cursor: pointer;">
            <input type="checkbox" ${s.done ? "checked" : ""} onchange="toggleStepDone('${p.process_id}', ${idx})" />
            <span style="${s.done ? "text-decoration: line-through; color: var(--fg-dim);" : "color: var(--fg-main);"}"">${escapeHtml(s.name)}</span>
          </label>
        `).join("")}
      </div>`;
  } catch (e) {
    detailEl.innerHTML = paneState("Process Error", e.message, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'></path><line x1='12' y1='9' x2='12' y2='13'></line><line x1='12' y1='17' x2='12.01' y2='17'></line></svg>");
  }
}

window.updateProcessStatus = async function(id, newStatus) {
  try {
    await apiPost("/api/processes/update", { process_id: id, status: newStatus });
    selectProcess(id);
  } catch (e) {
    alert(`Could not update status: ${e.message}`);
  }
};

window.toggleStepDone = async function(id, stepIdx) {
  try {
    await apiPost("/api/processes/update", { process_id: id, step_done: stepIdx });
    selectProcess(id);
  } catch (e) {
    alert(`Could not toggle step: ${e.message}`);
  }
};

// ── Custom Force-Directed Vault Graph Canvas ─────────────────────────────────

class ForceGraph {
  constructor(canvas, data, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.folderIndex = opts.folderIndex || {};
    this.onNodeClick = opts.onNodeClick || null;

    this._resizeCanvas();
    this._setupResizeObserver();
    this.nodes = data.nodes.map((n) => ({
      ...n,
      x: Math.random() * (this.width || 380),
      y: Math.random() * (this.height || 400),
      vx: 0,
      vy: 0,
      radius: 4,
    }));

    const nodeById = new Map(this.nodes.map((n) => [n.id, n]));
    this.edges = data.edges
      .map((e) => ({
        source: nodeById.get(e.source),
        target: nodeById.get(e.target),
      }))
      .filter((e) => e.source && e.target);

    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.hoveredNode = null;
    this.draggedNode = null;

    // Back-reference for introspection: lets the view state (pan, zoom) be read
    // from outside without threading a handle through every caller. Used by the
    // UI tests to assert that dragging actually moved the view.
    this.canvas.__graph = this;

    this._setupInteraction();
    this._startSimulation();
  }

  _resizeCanvas() {
    this.dpr = window.devicePixelRatio || 1;
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.floor(this.width * this.dpr);
    this.canvas.height = Math.floor(this.height * this.dpr);
    if (this.nodes) this._render();
  }

  _setupResizeObserver() {
    if (window.ResizeObserver && this.canvas.parentElement) {
      this._resizeObserver = new ResizeObserver(() => this._resizeCanvas());
      this._resizeObserver.observe(this.canvas.parentElement);
    }
    const dpr = window.devicePixelRatio || 1;
    this._dprQuery = window.matchMedia(`(resolution: ${dpr}dppx)`);
    this._onDprChange = () => this._resizeCanvas();
    if (this._dprQuery.addEventListener) {
      this._dprQuery.addEventListener("change", this._onDprChange);
    }
  }

  _startSimulation() {
    let alpha = 1;
    const step = () => {
      if (alpha > 0.01) {
        alpha *= 0.98;

        for (let i = 0; i < this.nodes.length; i++) {
          for (let j = i + 1; j < this.nodes.length; j++) {
            const n1 = this.nodes[i];
            const n2 = this.nodes[j];
            const dx = n2.x - n1.x;
            const dy = n2.y - n1.y;
            const distSq = dx * dx + dy * dy || 1;
            if (distSq < 25000) {
              const force = (300 / distSq) * alpha;
              const fx = dx * force;
              const fy = dy * force;
              n1.vx -= fx;
              n1.vy -= fy;
              n2.vx += fx;
              n2.vy += fy;
            }
          }
        }

        for (const e of this.edges) {
          const dx = e.target.x - e.source.x;
          const dy = e.target.y - e.source.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = (dist - 40) * 0.05 * alpha;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          e.source.vx += fx;
          e.source.vy += fy;
          e.target.vx -= fx;
          e.target.vy -= fy;
        }

        const cx = this.width / 2;
        const cy = this.height / 2;
        for (const n of this.nodes) {
          if (n === this.draggedNode) continue;
          n.vx += (cx - n.x) * 0.001 * alpha;
          n.vy += (cy - n.y) * 0.001 * alpha;
          n.vx *= 0.85;
          n.vy *= 0.85;
          n.x += n.vx;
          n.y += n.vy;
        }
      }

      this._render();
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  _render() {
    const { ctx, width, height, dpr } = this;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    ctx.translate(width / 2 + this.panX, height / 2 + this.panY);
    ctx.scale(this.zoom, this.zoom);
    ctx.translate(-width / 2, -height / 2);

    ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const e of this.edges) {
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
    }
    ctx.stroke();

    const palette = ["#38bdf8", "#fb923c", "#2dd4bf", "#c084fc", "#a3e635", "#f472b6", "#64748b"];
    for (const n of this.nodes) {
      const slot = this.folderIndex[n.folder] ?? 6;
      ctx.fillStyle = palette[slot % palette.length];
      ctx.beginPath();
      ctx.arc(n.x, n.y, n === this.hoveredNode ? 7 : 4, 0, Math.PI * 2);
      ctx.fill();
    }

    if (this.hoveredNode) {
      ctx.fillStyle = "#ffffff";
      ctx.font = "11px Inter, sans-serif";
      ctx.fillText(this.hoveredNode.title, this.hoveredNode.x + 8, this.hoveredNode.y + 4);
    }

    ctx.restore();
  }

  _setupInteraction() {
    const getPos = (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const clientX = e.clientX - rect.left;
      const clientY = e.clientY - rect.top;
      // Pan is applied before scale in _draw, so it lives in screen pixels while
      // node coordinates live in world space — hence the divide here, and the
      // absence of one when panning below.
      const x = (clientX - (this.width / 2 + this.panX)) / this.zoom + this.width / 2;
      const y = (clientY - (this.height / 2 + this.panY)) / this.zoom + this.height / 2;
      return { x, y, clientX, clientY };
    };

    const nodeAt = (p) =>
      this.nodes.find((n) => Math.hypot(n.x - p.x, n.y - p.y) < 10) || null;

    // Panning state. Kept separate from node dragging: grabbing empty space moves
    // the view, grabbing a node moves that node — the distinction Obsidian makes.
    let panning = false;
    let panStart = null;     // {clientX, clientY, panX, panY}
    let travelled = 0;       // pointer distance since mousedown, in screen px

    const setCursor = () => {
      this.canvas.style.cursor = panning
        ? "grabbing"
        : this.draggedNode
          ? "grabbing"
          : this.hoveredNode
            ? "pointer"
            : "grab";
    };

    this.canvas.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      const p = getPos(e);
      travelled = 0;
      const hit = nodeAt(p);
      if (hit) {
        this.draggedNode = hit;
      } else {
        panning = true;
        panStart = { clientX: e.clientX, clientY: e.clientY, panX: this.panX, panY: this.panY };
      }
      setCursor();
    });

    // Move and release are bound to the window, not the canvas: a drag that leaves
    // the canvas should keep working until the button is released, rather than
    // freezing at the edge.
    window.addEventListener("mousemove", (e) => {
      if (panning && panStart) {
        const dx = e.clientX - panStart.clientX;
        const dy = e.clientY - panStart.clientY;
        travelled = Math.max(travelled, Math.hypot(dx, dy));
        this.panX = panStart.panX + dx;   // screen-space: no zoom divide
        this.panY = panStart.panY + dy;
        return;
      }
      if (this.draggedNode) {
        const p = getPos(e);
        travelled = Math.max(travelled, 1e9);   // any node drag suppresses the click
        this.draggedNode.x = p.x;
        this.draggedNode.y = p.y;
        return;
      }
      const p = getPos(e);
      const inside =
        p.clientX >= 0 && p.clientY >= 0 &&
        p.clientX <= this.canvas.clientWidth && p.clientY <= this.canvas.clientHeight;
      this.hoveredNode = inside ? nodeAt(p) : null;
      setCursor();
    });

    window.addEventListener("mouseup", () => {
      panning = false;
      panStart = null;
      this.draggedNode = null;
      setCursor();
    });

    this.canvas.addEventListener("click", (e) => {
      // A pan that ends over a node must not also open it. Five pixels is enough
      // to tell a click from a drag without making deliberate clicks feel picky.
      if (travelled > 5) return;
      const clicked = nodeAt(getPos(e));
      if (clicked && this.onNodeClick) this.onNodeClick(clicked.path);
    });

    this.canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const p = getPos(e);
      const next = Math.max(0.2, Math.min(5, this.zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
      // Anchor the zoom on the pointer, so the point under the cursor stays put.
      // Zooming about the centre instead makes panning and zooming fight each
      // other — you zoom in, lose what you were looking at, and pan to find it.
      this.panX = p.clientX - this.width / 2 - (p.x - this.width / 2) * next;
      this.panY = p.clientY - this.height / 2 - (p.y - this.height / 2) * next;
      this.zoom = next;
    }, { passive: false });

    this.canvas.style.cursor = "grab";
  }
}

// This pane is the knowledge graph; code-graph articles belong to the Code view
// next to it. The API states its own bounds so a capped or filtered answer is
// never rendered as if it were the whole vault.
function renderGraphScope(data) {
  const wrap = document.getElementById("graph-scope");
  const note = document.getElementById("graph-scope-note");
  if (!wrap || !note) return;
  wrap.hidden = false;
  const parts = [
    data.truncated
      ? `showing the ${data.nodes.length} most recent of ${data.total_nodes} notes`
      : `${data.nodes.length} notes`,
  ];
  if (data.generated_excluded) {
    parts.push(`${data.generated_excluded} code-graph articles are under Code`);
  }
  note.textContent = parts.join(" · ");
}

async function initVaultGraph() {
  const canvas = document.getElementById("graph-canvas");
  const stateEl = document.getElementById("graph-state");
  const legendEl = document.getElementById("graph-legend");

  try {
    const data = await apiGet("/api/vault/graph");
    stateEl.hidden = true;
    renderGraphScope(data);

    const folders = Array.from(new Set(data.nodes.map((n) => n.folder))).sort();
    const folderIndex = {};
    folders.forEach((f, idx) => (folderIndex[f] = idx));

    const palette = ["#38bdf8", "#fb923c", "#2dd4bf", "#c084fc", "#a3e635", "#f472b6", "#64748b"];
    legendEl.hidden = false;
    legendEl.innerHTML = folders
      .map(
        (f, idx) => `
      <span class="legend-item">
        <span class="legend-swatch" style="background: ${palette[idx % palette.length]}"></span>
        ${escapeHtml(f)}
      </span>`
      )
      .join("");

    const graph = new ForceGraph(canvas, data, {
      folderIndex,
      onNodeClick: (path) => selectNote(path, null),
    });

    document.getElementById("graph-zoom-in").onclick = () => (graph.zoom *= 1.2);
    document.getElementById("graph-zoom-out").onclick = () => (graph.zoom *= 0.8);
    document.getElementById("graph-reset").onclick = () => {
      graph.zoom = 1;
      graph.panX = 0;
      graph.panY = 0;
    };
  } catch (e) {
    stateEl.innerHTML = paneState("Graph unavailable", e.message, "<svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'></path><line x1='12' y1='9' x2='12' y2='13'></line><line x1='12' y1='17' x2='12.01' y2='17'></line></svg>");
  }
}

// ── Obsidian-Style Settings Modal & Orchestration Configuration ────────────────

let currentSettingsData = null;
let selectedEngineMode = "local";

function setupSettingsModal() {
  const modal = document.getElementById("settings-modal");
  const triggerBtn = document.getElementById("btn-settings-trigger");
  const closeBtn = document.getElementById("settings-close-btn");
  const cancelBtn = document.getElementById("settings-cancel-btn");
  const saveBtn = document.getElementById("settings-save-btn");
  const toast = document.getElementById("settings-toast");

  if (!modal || !triggerBtn) return;

  triggerBtn.addEventListener("click", () => {
    modal.hidden = false;
    loadSettings();
  });

  const closeModal = () => (modal.hidden = true);
  closeBtn.addEventListener("click", closeModal);
  cancelBtn.addEventListener("click", closeModal);

  document.querySelectorAll(".settings-nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".settings-nav-item").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".settings-sec").forEach((s) => s.classList.remove("active"));
      btn.classList.add("active");
      const secId = btn.dataset.sec;
      document.getElementById(secId).classList.add("active");
    });
  });

  document.querySelectorAll(".mode-card").forEach((card) => {
    card.addEventListener("click", () => {
      document.querySelectorAll(".mode-card").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      selectedEngineMode = card.dataset.mode;
    });
  });

  const searchSlider = document.getElementById("cfg-search-threshold");
  const searchVal = document.getElementById("search-thresh-val");
  if (searchSlider && searchVal) {
    searchSlider.addEventListener("input", () => (searchVal.textContent = searchSlider.value));
  }

  const mergeSlider = document.getElementById("cfg-merge-threshold");
  const mergeVal = document.getElementById("merge-thresh-val");
  if (mergeSlider && mergeVal) {
    mergeSlider.addEventListener("input", () => (mergeVal.textContent = mergeSlider.value));
  }

  const modelSelect = document.getElementById("cfg-model-select");
  const modelPathInput = document.getElementById("cfg-llama-model");
  if (modelSelect && modelPathInput) {
    modelSelect.addEventListener("change", () => {
      if (modelSelect.value) modelPathInput.value = modelSelect.value;
    });
  }

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";

    const payload = {
      engine_mode: selectedEngineMode,
      budget_mode: document.getElementById("cfg-budget-mode").value,
      synthesis_enabled: document.getElementById("cfg-synthesis-enabled").checked,
      synthesis_lang: document.getElementById("cfg-synthesis-lang").value,
      web_search_enabled: document.getElementById("cfg-web-search").checked,
      llama_model: document.getElementById("cfg-llama-model").value.trim(),
      llama_binary: document.getElementById("cfg-llama-binary").value.trim(),
      llama_ctx: parseInt(document.getElementById("cfg-llama-ctx").value) || 4096,
      llama_ngl: parseInt(document.getElementById("cfg-llama-ngl").value) || 999,
      bge_model: document.getElementById("cfg-bge-model").value.trim(),
      search_threshold: parseFloat(document.getElementById("cfg-search-threshold").value) || 0.55,
      merge_threshold: parseFloat(document.getElementById("cfg-merge-threshold").value) || 0.88,
      max_tokens: parseInt(document.getElementById("cfg-max-tokens").value) || 2048,
    };

    try {
      await apiPost("/api/config/update", payload);
      toast.hidden = false;
      setTimeout(() => (toast.hidden = true), 2500);
      refreshStatus();
    } catch (err) {
      alert(`Could not save config: ${err.message}`);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Settings";
    }
  });
}

async function loadSettings() {
  try {
    const data = await apiGet("/api/config");
    currentSettingsData = data;
    const cfg = data.config || {};

    selectedEngineMode = cfg.engine_mode || "local";
    document.querySelectorAll(".mode-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.mode === selectedEngineMode);
    });

    document.getElementById("cfg-budget-mode").value = cfg.budget_mode || "normal";
    document.getElementById("cfg-synthesis-enabled").checked = !!cfg.synthesis_enabled;
    document.getElementById("cfg-synthesis-lang").value = cfg.synthesis_lang || "en";
    document.getElementById("cfg-web-search").checked = !!cfg.web_search_enabled;

    document.getElementById("cfg-llama-model").value = cfg.llama_model || "";
    document.getElementById("cfg-llama-binary").value = cfg.llama_binary || "";
    document.getElementById("cfg-llama-ctx").value = cfg.llama_ctx || 4096;
    document.getElementById("cfg-llama-ngl").value = cfg.llama_ngl || 999;

    document.getElementById("cfg-bge-model").value = cfg.bge_model || "BAAI/bge-base-en-v1.5";
    document.getElementById("cfg-search-threshold").value = cfg.search_threshold || 0.55;
    document.getElementById("search-thresh-val").textContent = cfg.search_threshold || 0.55;

    document.getElementById("cfg-merge-threshold").value = cfg.merge_threshold || 0.88;
    document.getElementById("merge-thresh-val").textContent = cfg.merge_threshold || 0.88;

    document.getElementById("cfg-max-tokens").value = cfg.max_tokens || 2048;

    document.getElementById("cfg-vault-path").value = cfg.vault_path || "";

    const modelSelect = document.getElementById("cfg-model-select");
    const avail = data.available_models || [];
    if (avail.length) {
      modelSelect.innerHTML = `<option value="">Select detected GGUF model (${avail.length})</option>` +
        avail.map((m) => `<option value="${escapeHtml(m)}" ${m === cfg.llama_model ? "selected" : ""}>${escapeHtml(m)}</option>`).join("");
    } else {
      modelSelect.innerHTML = `<option value="">No GGUF models in ~/.delegation_core/models</option>`;
    }

    const folderChips = document.getElementById("cfg-vault-folders-chips");
    const folders = cfg.vault_folders || [];
    folderChips.innerHTML = folders
      .map((f) => `<span class="count" style="font-size: 12px; padding: 3px 10px;"><svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'></path></svg> ${escapeHtml(f)}</span>`)
      .join("");
  } catch (e) {
    console.error("Could not load settings:", e);
  }
}

async function purgeOrphans() {
  const btn = document.getElementById("purge-orphans-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Purging…";
  }
  try {
    const res = await apiPost("/api/system/purge_orphans", {});
    alert(res.message || "Purged dead sessions and orphaned processes.");
    refreshClients();
  } catch (e) {
    alert(`Could not purge orphans: ${e.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Purge Orphans";
    }
  }
}

function setupPaneResizer() {
  // One handler for both docks. The distinction matters: a left dock's width is the
  // pointer's distance from the left edge, a right dock's is its distance from the
  // right. Reusing the left formula on the right — which is what the single-pane
  // version did — makes the pane grow when you drag it smaller.
  const docks = [
    { resizer: "left-resizer",  pane: "left-dock",  side: "left",  min: 180, max: 480 },
    { resizer: "right-resizer", pane: "graph-pane", side: "right", min: 220, max: 700 },
  ];

  for (const { resizer: rid, pane: pid, side, min, max } of docks) {
    const resizer = document.getElementById(rid);
    const pane = document.getElementById(pid);
    if (!resizer || !pane) continue;

    let dragging = false;

    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const raw = side === "left" ? e.clientX : window.innerWidth - e.clientX;
      pane.style.width = `${Math.max(min, Math.min(max, raw))}px`;
    });

    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove("resizing");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      try { localStorage.setItem(`dc.${pid}.width`, pane.style.width); } catch { /* ignore */ }
    });

    resizer.addEventListener("mousedown", () => {
      dragging = true;
      resizer.classList.add("resizing");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    });

    try {
      const saved = localStorage.getItem(`dc.${pid}.width`);
      if (saved) pane.style.width = saved;
    } catch { /* ignore */ }
  }
}

// ── MCP connections ──────────────────────────────────────────────────────────

async function refreshMcpConnections() {
  const el = document.getElementById("mcp-conn-list");
  if (!el || !apiBase) return;
  try {
    // The MCP servers Claude has mounted — not the client surfaces attached to
    // delegation-core. Both are "MCP connections" but they point opposite ways,
    // and this panel is about what the LLM can reach.
    const { windows } = await apiGet("/api/mcp/windows");
    if (!windows || !windows.length) {
      el.innerHTML = paneState("No MCP servers configured", "Nothing is mounted for the provider.");
      return;
    }
    el.innerHTML = windows.map((w) => `
      <div class="mcp-conn ${w.mounted ? "is-mounted" : "is-dormant"}">
        <div class="mcp-conn-head">
          <span class="dot ${w.mounted ? "ok" : "bad"}"></span>
          <span class="mcp-conn-name">${escapeHtml(w.name)}</span>
        </div>
        <div class="mcp-conn-meta">${w.mounted ? "mounted" : "registered, not mounted"}${
          w.managed ? "" : " · always on"}</div>
      </div>`).join("");
  } catch (e) {
    el.innerHTML = paneState("Connections unavailable", "The sidecar did not answer.");
  }
}

// ── Engine mode ──────────────────────────────────────────────────────────────

const ENGINE_MODES = ["local", "hybrid", "agent"];
let engineMode = null;

function renderEngineSelect() {
  const sel = document.getElementById("engine-mode-select");
  if (!sel) return;
  sel.value = engineMode || "";
  sel.dataset.mode = engineMode || "unknown";
  sel.title = `Engine mode: ${engineMode || "unknown"}`;
}

async function setEngineMode(next) {
  if (!apiBase || !ENGINE_MODES.includes(next)) return;
  const previous = engineMode;
  engineMode = next;
  renderEngineSelect();
  try {
    await apiPost("/api/config/update", { engine_mode: next });
    refreshStatus();
  } catch (e) {
    engineMode = previous;   // a control that lies about the saved state is worse
    renderEngineSelect();    // than one that visibly refuses
    alert(`Could not switch engine mode: ${e.message}`);
  }
}

async function loadEngineMode() {
  try {
    const res = await apiGet("/api/config");
    // /api/config nests everything under a "config" key; the fallback keeps this
    // working if that ever flattens.
    const cfg = res.config || res;
    engineMode = cfg.engine_mode || null;
  } catch { engineMode = null; }
  renderEngineSelect();
}

// ── Startup & Initialization ──────────────────────────────────────────────────

async function getApiPort() {
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = window.__TAURI_INTERNALS__;
    return await invoke("get_api_port");
  }
  return 8182;
}

async function main() {
  // The UI is wired up before the sidecar is contacted, and unconditionally.
  // Previously a failed port lookup returned here, which left the whole window
  // inert — no ribbon, no navigation, not even the per-panel error states that
  // exist precisely for this situation. Setup touches no network, so there is no
  // reason for it to depend on the network succeeding.
  setupWindowControls();
  setupServerControls();
  setupSettingsModal();
  setupPaneResizer();
  setupNavigation();
  setupNotesFilter();
  setupNoteActions();
  setupVectorSearch();
  setupProcesses();

  document.getElementById("llama-toggle-btn").addEventListener("click", toggleLlama);
  const engineSel = document.getElementById("engine-mode-select");
  if (engineSel) engineSel.addEventListener("change", (e) => setEngineMode(e.target.value));

  // Only now reach for the sidecar. If it cannot be found the app stays usable and
  // says so, instead of presenting a frozen shell with no explanation.
  try {
    const port = await getApiPort();
    apiBase = `http://127.0.0.1:${port}`;
  } catch (e) {
    console.error("Could not determine sidecar port", e);
    const status = document.getElementById("status-fields");
    if (status) {
      status.innerHTML = `<span class="muted">sidecar unreachable — panels will stay empty</span>`;
    }
    return;   // navigation, ribbon and panel states are already live
  }

  const fleetRefreshBtn = document.getElementById("fleet-refresh-btn");
  if (fleetRefreshBtn) fleetRefreshBtn.addEventListener("click", refreshClients);

  const purgeBtn = document.getElementById("purge-orphans-btn");
  if (purgeBtn) purgeBtn.addEventListener("click", purgeOrphans);

  loadEngineMode();
  refreshStatus();
  refreshClients();
  loadNotesBrowser();
  initVaultGraph();

  setInterval(refreshStatus, 5000);
  setInterval(refreshClients, 5000);
  setInterval(() => {
    if (document.getElementById("dock-fleet")?.classList.contains("active")) refreshMcpConnections();
  }, 5000);
}

document.addEventListener("DOMContentLoaded", main);

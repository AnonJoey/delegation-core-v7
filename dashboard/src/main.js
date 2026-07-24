// delegation-core dashboard frontend. Plain JS, no framework/bundler — fetches
// from the local dashboard_api.py sidecar (see src-tauri/src/lib.rs for how its
// port is picked up) and renders five things: server status, connected MCP
// clients, a notes browser, the vault as a force-directed wikilink graph, and
// a tracked-process (task) browser.

let apiBase = null;

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

// All rendered text ultimately comes from local files (note content/titles,
// process names/notes) or the far end of an MCP client's initialize handshake
// (client name/version) — none of it is something this app authored, so it's
// escaped before going into innerHTML rather than trusted as safe markup.
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Shared empty/loading/error treatment for panel bodies. Both title and hint
// pass through escapeHtml even though every current call site uses literal
// strings — keeps the helper safe if a message ever interpolates data.
function paneState(title, hint = "", cls = "") {
  return `<div class="pane-state${cls ? ` ${cls}` : ""}">
    <div class="pane-state-title">${escapeHtml(title)}</div>
    ${hint ? `<div class="pane-state-hint">${escapeHtml(hint)}</div>` : ""}
  </div>`;
}

// ── status panel ─────────────────────────────────────────────────────────────

function dot(ok) {
  return `<span class="dot ${ok ? "ok" : "bad"}"></span>`;
}

function stat(label, valueHtml) {
  return `<span class="stat"><span class="stat-label">${label}</span>${valueHtml}</span>`;
}

// True only for the brief window right after a start/stop click — keeps the
// button showing "Starting…/Stopping…" through that click's own explicit
// refreshStatus() (see toggleLlama()) instead of the regular 5s poll
// clobbering it back to "Start/Stop" a split second later.
let llamaTogglePending = false;

function updateLlamaButton(llamaState) {
  const btn = document.getElementById("llama-toggle-btn");
  if (llamaTogglePending) return;
  btn.disabled = false;
  if (llamaState === "online" || llamaState === "unhealthy") {
    btn.textContent = "Stop llama.cpp";
    btn.dataset.action = "stop";
  } else {
    btn.textContent = "Start llama.cpp";
    btn.dataset.action = "start";
  }
}

async function refreshStatus() {
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
    updateLlamaButton(s.llama_state);
  } catch (e) {
    el.innerHTML = `<span class="muted">status unavailable — retrying</span>`;
  }
}

async function toggleLlama() {
  const btn = document.getElementById("llama-toggle-btn");
  const action = btn.dataset.action; // "start" or "stop", set by the last updateLlamaButton()
  llamaTogglePending = true;
  btn.disabled = true;
  btn.textContent = action === "start" ? "Starting…" : "Stopping…";
  try {
    await apiPost(`/api/llama/${action}`, {});
  } catch (e) {
    alert(`Could not ${action} llama.cpp: ${e.message}`);
  }
  llamaTogglePending = false;
  // Stopping is fast (a few seconds at most); starting can take up to ~90s
  // (see engine.py's health-poll loop) — either way, re-check shortly rather
  // than waiting out the regular 5s interval, but don't pretend to know the
  // real state before actually asking.
  setTimeout(refreshStatus, action === "start" ? 3000 : 1500);
}

// ── connected clients panel ──────────────────────────────────────────────────

function timeAgo(seconds) {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

async function refreshClients() {
  const el = document.getElementById("clients-list");
  try {
    const { clients } = await apiGet("/api/clients");
    if (!clients.length) {
      el.innerHTML = `<span class="muted">no clients connected</span>`;
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
  } catch (e) {
    el.innerHTML = `<span class="muted">clients unavailable — retrying</span>`;
  }
}

// ── notes browser ────────────────────────────────────────────────────────────

let vaultTree = null;
let notesFilter = "";
let selectedFolder = null;

function noteMatchesFilter(n) {
  return !notesFilter || n.title.toLowerCase().includes(notesFilter);
}

function renderFolderList() {
  const folderList = document.getElementById("folder-list");
  const folders = Object.keys(vaultTree.folders);
  if (!folders.length) {
    folderList.innerHTML = paneState("No folders", "The vault has no configured folders yet.");
    return;
  }
  folderList.innerHTML = folders
    .map((f) => {
      const count = vaultTree.folders[f].filter(noteMatchesFilter).length;
      return `<div class="list-item ${f === selectedFolder ? "active" : ""}" data-folder="${escapeHtml(f)}" tabindex="0">${escapeHtml(f)} <span class="count">${count}</span></div>`;
    })
    .join("");
  folderList.querySelectorAll(".list-item").forEach((el) => {
    el.addEventListener("click", () => selectFolder(el.dataset.folder));
  });
}

function renderNoteList() {
  const noteList = document.getElementById("note-list");
  const notes = (vaultTree.folders[selectedFolder] || []).filter(noteMatchesFilter);
  if (!notes.length) {
    noteList.innerHTML = notesFilter
      ? paneState("No matches", "No note titles in this folder match the filter.")
      : paneState("Empty folder", "Notes filed here will appear in this list.");
    return;
  }
  noteList.innerHTML = notes
    .map((n) => `<div class="list-item" data-path="${escapeHtml(n.path)}" tabindex="0">${escapeHtml(n.title)}</div>`)
    .join("");
  noteList.querySelectorAll(".list-item").forEach((el) => {
    el.addEventListener("click", () => selectNote(el.dataset.path, el));
  });
}

async function loadNotesBrowser() {
  const folderList = document.getElementById("folder-list");
  try {
    vaultTree = await apiGet("/api/vault/tree");
  } catch (e) {
    folderList.innerHTML = paneState("Vault unavailable", "Could not load the note tree.");
    throw e; // still surfaces to the caller — see main()'s Promise.allSettled
  }
  renderFolderList();
  const folders = Object.keys(vaultTree.folders);
  if (folders.length) selectFolder(folders[0]);
}

function selectFolder(folder) {
  selectedFolder = folder;
  document.querySelectorAll("#folder-list .list-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.folder === folder);
  });
  renderNoteList();
}

function setupNotesFilter() {
  const input = document.getElementById("notes-filter");
  input.addEventListener("input", () => {
    notesFilter = input.value.trim().toLowerCase();
    if (!vaultTree) return;
    renderFolderList(); // counts reflect the filter
    renderNoteList();
  });
}

async function selectNote(path, el) {
  document.querySelectorAll("#note-list .list-item").forEach((e) => e.classList.remove("active"));
  el.classList.add("active");
  const state = document.getElementById("note-state");
  const content = document.getElementById("note-content");
  content.hidden = true;
  state.hidden = false;
  state.innerHTML = paneState("Loading note…", "", "loading");
  try {
    const { content: text } = await apiGet(`/api/vault/note?path=${encodeURIComponent(path)}`);
    content.textContent = text; // textContent, not innerHTML — raw note body, never parsed as markup
    state.hidden = true;
    content.hidden = false;
  } catch (e) {
    state.innerHTML = paneState("Could not load note", "The file may have been moved or deleted.");
  }
}

// ── vault graph (custom force-directed layout, canvas, no external library) ─

class ForceGraph {
  constructor(canvas, data, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.folderIndex = opts.folderIndex || {}; // folder name -> palette slot, shared with the legend
    this.onNodeClick = opts.onNodeClick || null;
    this._resizeCanvas(); // sets this.width/height (logical) + this.dpr before anything below uses them
    this.nodes = data.nodes.map((n) => ({
      ...n,
      x: Math.random() * this.width,
      y: Math.random() * this.height,
      vx: 0,
      vy: 0,
    }));
    this.byId = Object.fromEntries(this.nodes.map((n) => [n.id, n]));
    this.edges = data.edges
      .map((e) => ({ source: this.byId[e.source], target: this.byId[e.target] }))
      .filter((e) => e.source && e.target);
    // Degree, radius, and neighbor sets are static once edges are built —
    // precomputed here so _draw() and hover highlighting don't refilter the
    // edge list every frame.
    for (const n of this.nodes) {
      n.degree = 0;
      n.neighbors = new Set();
    }
    for (const e of this.edges) {
      e.source.degree++;
      e.target.degree++;
      e.source.neighbors.add(e.target);
      e.target.neighbors.add(e.source);
    }
    for (const n of this.nodes) n.r = Math.min(4 + n.degree * 1.2, 16);
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    this.dragNode = null;
    this.hoverNode = null;
    this._watchResize();
    this._bindEvents();
    this._tick();
  }

  // canvas.width/height (attributes) are the backing-buffer resolution; the
  // canvas's on-screen CSS size is separately controlled by the flexbox
  // layout (100% of its parent). Leaving the buffer at a flat 1:1 mapping to
  // CSS pixels renders blurrier than the surrounding UI on any HiDPI display
  // — Retina on macOS, and Windows' near-universal fractional display
  // scaling — since the browser then upscales the low-res buffer to fill the
  // physically-higher-res screen. this.width/this.height (logical/CSS
  // pixels) are what every physics and hit-testing calculation in this class
  // uses, matching what mouse events (e.offsetX/offsetY) always report
  // regardless of screen density; the buffer itself is sized at
  // width*dpr/height*dpr, with the mismatch corrected by one ctx.scale(dpr,
  // dpr) in _draw() rather than by touching any of that logical-space math.
  _resizeCanvas() {
    const rect = this.canvas.parentElement.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.round(rect.width * this.dpr);
    this.canvas.height = Math.round(rect.height * this.dpr);
  }

  _watchResize() {
    // ResizeObserver, not a raw window "resize" listener: it also reacts to
    // layout-driven size changes of the graph pane itself (not just
    // top-level OS window resizes), and the browser naturally coalesces it
    // to at most once per frame instead of firing on every intermediate
    // pixel during a drag-resize the way "resize" can. Supported across all
    // three of Tauri's webview engines (WebView2/Windows, WebKitGTK/Linux,
    // WKWebView/macOS) — this is a standard web platform API, not a
    // Linux-only or Windows-only assumption.
    new ResizeObserver(() => this._resizeCanvas()).observe(this.canvas.parentElement);

    // devicePixelRatio has no native "it changed" event — the standard
    // workaround is a matchMedia query pinned to the *current* ratio, which
    // fires once that stops matching (i.e. the ratio changed), and must be
    // re-armed at the new ratio to keep watching. This is what catches
    // dragging the window between differently-scaled displays (per-monitor
    // DPI on Windows; moving between a Retina and an external non-Retina
    // display on macOS) even when the window's CSS size hasn't changed at
    // all, which ResizeObserver alone would miss.
    const watchDpr = () => {
      matchMedia(`(resolution: ${this.dpr}dppx)`).addEventListener(
        "change",
        () => { this._resizeCanvas(); watchDpr(); },
        { once: true }
      );
    };
    watchDpr();
  }

  _nodeAt(sx, sy) {
    const { x, y } = this._toWorld(sx, sy);
    return this.nodes.find((n) => Math.hypot(n.x - x, n.y - y) < Math.max(n.r + 4, 10));
  }

  _bindEvents() {
    const c = this.canvas;
    let panStart = null;

    c.addEventListener("mousedown", (e) => {
      const hit = this._nodeAt(e.offsetX, e.offsetY);
      // Remembered so mouseup can tell a click (open the note) apart from a
      // drag (reposition the node) by how far the pointer traveled.
      this._pressAt = { x: e.offsetX, y: e.offsetY, node: hit };
      if (hit) {
        this.dragNode = hit;
      } else {
        panStart = { x: e.offsetX, y: e.offsetY, offX: this.offsetX, offY: this.offsetY };
      }
    });
    c.addEventListener("mousemove", (e) => {
      if (this.dragNode) {
        const { x, y } = this._toWorld(e.offsetX, e.offsetY);
        this.dragNode.x = x;
        this.dragNode.y = y;
        this.dragNode.vx = 0;
        this.dragNode.vy = 0;
      } else if (panStart) {
        this.offsetX = panStart.offX + (e.offsetX - panStart.x);
        this.offsetY = panStart.offY + (e.offsetY - panStart.y);
        this.hoverNode = null;
      } else {
        this.hoverNode = this._nodeAt(e.offsetX, e.offsetY);
        c.style.cursor = this.hoverNode ? "pointer" : "grab";
      }
    });
    c.addEventListener("mouseleave", () => {
      this.hoverNode = null;
    });
    c.addEventListener("click", (e) => {
      const p = this._pressAt;
      if (!p || !p.node || !this.onNodeClick) return;
      if (Math.hypot(e.offsetX - p.x, e.offsetY - p.y) < 5) this.onNodeClick(p.node);
    });
    window.addEventListener("mouseup", () => {
      this.dragNode = null;
      panStart = null;
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 0.9;
      this.scale = Math.min(4, Math.max(0.2, this.scale * factor));
    });
  }

  _toWorld(sx, sy) {
    return { x: (sx - this.offsetX) / this.scale, y: (sy - this.offsetY) / this.scale };
  }

  _step() {
    const nodes = this.nodes;
    const REPEL = 2200;
    const SPRING = 0.02;
    const SPRING_LEN = 70;
    const DAMPING = 0.85;
    // Two close nodes (common with 100+ nodes randomly seeded into a modest
    // canvas) used to produce a near-zero distSq, and REPEL/distSq spiked
    // into the thousands with nothing capping the resulting velocity — that
    // one huge kick could fling a node into a *different* cluster and repeat,
    // never settling. MIN_DIST_SQ floors the repulsion at a sane distance
    // instead of letting it blow up near zero; MAX_SPEED caps how far any
    // single tick can move a node regardless of how large the force was.
    const MIN_DIST_SQ = 400;
    const MAX_SPEED = 8;

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const distSq = Math.max(dx * dx + dy * dy, MIN_DIST_SQ);
        const force = REPEL / distSq;
        const dist = Math.sqrt(distSq);
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    for (const e of this.edges) {
      const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
      const dist = Math.max(Math.hypot(dx, dy), 1);
      const force = (dist - SPRING_LEN) * SPRING;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      e.source.vx += fx; e.source.vy += fy;
      e.target.vx -= fx; e.target.vy -= fy;
    }
    const cx = this.width / 2, cy = this.height / 2; // logical center, not the dpr-scaled backing buffer
    for (const n of nodes) {
      if (n === this.dragNode) continue;
      n.vx += (cx - n.x) * 0.0008;
      n.vy += (cy - n.y) * 0.0008;
      n.vx *= DAMPING; n.vy *= DAMPING;
      const speed = Math.hypot(n.vx, n.vy);
      if (speed > MAX_SPEED) {
        n.vx = (n.vx / speed) * MAX_SPEED;
        n.vy = (n.vy / speed) * MAX_SPEED;
      }
      n.x += n.vx; n.y += n.vy;
    }
  }

  _draw() {
    const ctx = this.ctx;
    const styles = getComputedStyle(document.documentElement);
    const border = styles.getPropertyValue("--border").trim();
    const accent = styles.getPropertyValue("--accent").trim();
    const fg = styles.getPropertyValue("--fg").trim();
    const halo = styles.getPropertyValue("--bg-panel").trim();
    // Read per frame like the vars above, so a live theme switch recolors the
    // graph without a reload.
    const palette = [1, 2, 3, 4, 5, 6].map((i) => styles.getPropertyValue(`--graph-${i}`).trim());
    const other = styles.getPropertyValue("--graph-other").trim();
    const hover = this.hoverNode;
    const dimmed = (n) => hover && n !== hover && !hover.neighbors.has(n);

    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.save();
    ctx.scale(this.dpr, this.dpr); // logical (CSS-pixel) space -> the dpr-scaled backing buffer
    ctx.translate(this.offsetX, this.offsetY);
    ctx.scale(this.scale, this.scale);

    ctx.strokeStyle = border;
    ctx.lineWidth = 1 / this.scale;
    ctx.globalAlpha = hover ? 0.25 : 1;
    for (const e of this.edges) {
      if (hover && (e.source === hover || e.target === hover)) continue; // redrawn highlighted below
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();
    }
    if (hover) {
      ctx.globalAlpha = 1;
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.5 / this.scale;
      for (const e of this.edges) {
        if (e.source !== hover && e.target !== hover) continue;
        ctx.beginPath();
        ctx.moveTo(e.source.x, e.source.y);
        ctx.lineTo(e.target.x, e.target.y);
        ctx.stroke();
      }
    }

    for (const n of this.nodes) {
      const slot = this.folderIndex[n.folder];
      ctx.fillStyle = slot === undefined ? other : palette[slot];
      ctx.globalAlpha = dimmed(n) ? 0.3 : 1;
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (hover) {
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2 / this.scale;
      ctx.beginPath();
      ctx.arc(hover.x, hover.y, hover.r + 3 / this.scale, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (this.scale > 0.6) {
      ctx.fillStyle = fg;
      ctx.font = `${11 / this.scale}px sans-serif`;
      for (const n of this.nodes) {
        if (n === hover) continue; // its label is drawn below, at any zoom
        // fillText, not innerHTML — canvas text draws characters directly,
        // never interpreted as markup, so n.title needs no escaping here.
        ctx.globalAlpha = dimmed(n) ? 0.3 : 1;
        ctx.fillText(n.title, n.x + n.r + 4, n.y + 4);
      }
      ctx.globalAlpha = 1;
    }

    if (hover) {
      // Full title regardless of zoom threshold, with a panel-colored halo so
      // it stays readable over edges and neighboring labels.
      ctx.font = `600 ${12 / this.scale}px sans-serif`;
      ctx.strokeStyle = halo;
      ctx.lineWidth = 3 / this.scale;
      ctx.strokeText(hover.title, hover.x + hover.r + 5, hover.y + 4);
      ctx.fillStyle = fg;
      ctx.fillText(hover.title, hover.x + hover.r + 5, hover.y + 4);
    }
    ctx.restore();
  }

  _tick() {
    // Skip the O(n^2) repulsion pass and redraw while the window is minimized
    // or occluded on another virtual desktop — rAF alone doesn't guarantee
    // that (behavior varies by webview/platform), and this graph runs forever
    // for the life of the app, so an unconditional loop would burn CPU/battery
    // indefinitely for a window nobody can see. Still reschedule so it picks
    // back up immediately once visible again.
    if (!document.hidden) {
      this._step();
      this._draw();
    }
    requestAnimationFrame(() => this._tick());
  }
}

// Jump from a graph node to its note in the Notes tab, reusing the existing
// browser flow (tab click handler, selectFolder, selectNote) rather than a
// parallel code path. Clears the filter first so the target can't be hidden.
function openNoteFromGraph(node) {
  if (!vaultTree || !node.path || !vaultTree.folders[node.folder]) return;
  document.querySelector('.tab-btn[data-tab="notes"]').click();
  if (notesFilter) {
    document.getElementById("notes-filter").value = "";
    notesFilter = "";
    renderFolderList();
  }
  selectFolder(node.folder);
  const item = [...document.querySelectorAll("#note-list .list-item")].find(
    (el) => el.dataset.path === node.path
  );
  if (item) {
    selectNote(node.path, item);
    item.scrollIntoView({ block: "nearest" });
  }
}

async function loadVaultGraph() {
  const canvas = document.getElementById("graph-canvas");
  const stateEl = document.getElementById("graph-state");
  const legend = document.getElementById("graph-legend");
  try {
    const data = await apiGet("/api/vault/graph");
    if (!data.nodes.length) {
      stateEl.innerHTML = paneState("Vault graph is empty", "Notes linked with [[wikilinks]] will appear here.");
      return;
    }
    // First six folders (sorted, so the assignment is stable across reloads)
    // get a categorical color; any beyond that share the muted "other" swatch
    // rather than cycling hues into ambiguity.
    const folders = [...new Set(data.nodes.map((n) => n.folder).filter(Boolean))].sort();
    const folderIndex = {};
    folders.forEach((f, i) => {
      if (i < 6) folderIndex[f] = i;
    });
    legend.innerHTML = folders
      .map((f) => {
        const swatch = folderIndex[f] === undefined ? "var(--graph-other)" : `var(--graph-${folderIndex[f] + 1})`;
        return `<span class="legend-item"><span class="legend-swatch" style="background: ${swatch}"></span>${escapeHtml(f)}</span>`;
      })
      .join("");
    legend.hidden = false;
    stateEl.hidden = true;
    new ForceGraph(canvas, data, { folderIndex, onNodeClick: openNoteFromGraph }); // sizes the canvas itself — see _resizeCanvas()
  } catch (e) {
    stateEl.hidden = false;
    stateEl.innerHTML = paneState("Could not load the vault graph", "It will not retry on its own — restart the app once the sidecar is up.");
    throw e; // still surfaces to the caller — see main()'s Promise.allSettled
  }
}

// ── task tracker ─────────────────────────────────────────────────────────────

let selectedTaskId = null;

async function loadTaskList() {
  const status = document.getElementById("tasks-status-filter").value;
  const el = document.getElementById("task-list");
  // Only show the loading state when there's nothing on screen yet — this
  // also runs after every step/status update, where a flash of "Loading…"
  // over an already-rendered list would just be churn.
  if (!el.childElementCount) el.innerHTML = paneState("Loading processes…", "", "loading");
  try {
    const { processes } = await apiGet(`/api/processes?status=${encodeURIComponent(status)}`);
    if (!processes.length) {
      el.innerHTML =
        status === "active"
          ? paneState("No active processes", "Start one with + New, or switch the filter to All.")
          : paneState("No processes", "Nothing matches this filter.");
      return;
    }
    el.innerHTML = processes
      .map((p) => {
        const done = p.steps.filter((s) => s.done).length;
        const stepsLabel = p.steps.length ? `${done}/${p.steps.length} steps` : p.status;
        return `<div class="task-list-item ${p.id === selectedTaskId ? "active" : ""}" data-id="${escapeHtml(p.id)}" tabindex="0">
          <div class="task-name">${escapeHtml(p.name)}</div>
          <div class="task-meta">${escapeHtml(stepsLabel)}</div>
        </div>`;
      })
      .join("");
    el.querySelectorAll(".task-list-item").forEach((item) => {
      item.addEventListener("click", () => selectTask(item.dataset.id));
    });
  } catch (e) {
    el.innerHTML = paneState("Tasks unavailable", "Could not reach the process store — change the filter to retry.");
  }
}

async function selectTask(id) {
  selectedTaskId = id;
  document.querySelectorAll(".task-list-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === id);
  });
  await renderTaskDetail(id);
}

async function renderTaskDetail(id) {
  const el = document.getElementById("task-detail");
  el.innerHTML = paneState("Loading process…", "", "loading");
  let proc;
  try {
    proc = await apiGet(`/api/processes/get?id=${encodeURIComponent(id)}`);
  } catch (e) {
    el.innerHTML = paneState("Could not load process", "It may have been removed — refresh the list.");
    return;
  }

  const stepsHtml = proc.steps
    .map(
      (s, i) => `<li class="${s.done ? "done" : ""}" data-index="${i}">
        <input type="checkbox" ${s.done ? "checked" : ""} ${s.done ? "disabled" : ""} data-index="${i}" />
        ${escapeHtml(s.description)}
      </li>`
    )
    .join("");

  const notesHtml = proc.notes.length
    ? proc.notes
        .map(
          (n) => `<li>
            <div class="note-timestamp">${escapeHtml(n.timestamp)}</div>
            <div>${escapeHtml(n.text)}</div>
          </li>`
        )
        .join("")
    : `<li class="muted">no notes yet</li>`;

  el.innerHTML = `
    <h2>${escapeHtml(proc.name)}</h2>
    <div class="task-status-badge">${escapeHtml(proc.status)}</div>
    ${proc.description ? `<div class="task-description">${escapeHtml(proc.description)}</div>` : ""}

    <div class="task-status-actions">
      <button data-status="active">Active</button>
      <button data-status="paused">Pause</button>
      <button data-status="done">Done</button>
      <button data-status="cancelled">Cancel</button>
    </div>

    ${proc.steps.length ? `<ul class="task-steps">${stepsHtml}</ul>` : ""}

    <ul class="task-notes">${notesHtml}</ul>

    <div class="note-input-row">
      <input type="text" id="new-note-input" placeholder="Add a note…" />
      <button id="add-note-btn" type="button">Add</button>
    </div>
  `;

  // If apiPost() fails here, without this the checkbox is left showing
  // "checked" by the browser's own default behavior even though nothing was
  // actually saved (the re-render that would reveal the mismatch never runs)
  // — silently misleading. Reset the visual state and say so instead.
  el.querySelectorAll('.task-steps input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", async () => {
      try {
        await apiPost("/api/processes/update", {
          process_id: proc.id,
          step_done: Number(cb.dataset.index),
        });
        await renderTaskDetail(proc.id);
        await loadTaskList();
      } catch (e) {
        cb.checked = false;
        alert(`Could not update step: ${e.message}`);
      }
    });
  });

  el.querySelectorAll(".task-status-actions button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await apiPost("/api/processes/update", { process_id: proc.id, status: btn.dataset.status });
        await renderTaskDetail(proc.id);
        await loadTaskList();
      } catch (e) {
        alert(`Could not update status: ${e.message}`);
      }
    });
  });

  const noteInput = document.getElementById("new-note-input");
  const addNote = async () => {
    const text = noteInput.value.trim();
    if (!text) return;
    try {
      await apiPost("/api/processes/update", { process_id: proc.id, note: text });
      await renderTaskDetail(proc.id);
    } catch (e) {
      alert(`Could not add note: ${e.message}`);
    }
  };
  document.getElementById("add-note-btn").addEventListener("click", addNote);
  noteInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") addNote();
  });
}

function showNewTaskForm() {
  selectedTaskId = null;
  document.querySelectorAll(".task-list-item").forEach((el) => el.classList.remove("active"));
  const el = document.getElementById("task-detail");
  el.innerHTML = `
    <h2>New process</h2>
    <div class="task-form">
      <label>Name<br /><input type="text" id="new-task-name" /></label>
      <label>Description<br /><textarea id="new-task-description" rows="3"></textarea></label>
      <label>Steps (comma-separated)<br /><input type="text" id="new-task-steps" placeholder="plan, implement, test" /></label>
      <button id="create-task-btn" type="button">Create</button>
    </div>
  `;
  document.getElementById("create-task-btn").addEventListener("click", async () => {
    const name = document.getElementById("new-task-name").value.trim();
    if (!name) return;
    const description = document.getElementById("new-task-description").value.trim();
    const steps = document
      .getElementById("new-task-steps")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const proc = await apiPost("/api/processes/create", { name, description, steps });
      await loadTaskList();
      await selectTask(proc.id);
    } catch (e) {
      alert(`Could not create process: ${e.message}`);
    }
  });
}

function setupTasksPanel() {
  document.getElementById("tasks-status-filter").addEventListener("change", loadTaskList);
  document.getElementById("tasks-new-btn").addEventListener("click", showNewTaskForm);
}

// List rows are divs with tabindex, not buttons — delegate Enter/Space so
// keyboard focus can activate them like a click. Attached once per container;
// survives every innerHTML re-render inside.
function setupListKeyboard() {
  for (const id of ["folder-list", "note-list", "task-list"]) {
    document.getElementById(id).addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const item = e.target.closest(".list-item, .task-list-item");
      if (item) {
        e.preventDefault();
        item.click();
      }
    });
  }
}

// ── tabs ─────────────────────────────────────────────────────────────────────
// The vault graph is no longer one of these — it's always visible in its own
// pane (see #graph-pane in index.html) rather than a tab someone has to click
// into, so only Notes/Tasks toggle here.

let tasksLoaded = false;

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      document.getElementById(`tab-${tab}`).classList.add("active");
      // tasksLoaded is only set on success — a failed first load (e.g. the
      // sidecar wasn't fully warmed up yet) stays retriable by just switching
      // tabs away and back, rather than being stuck forever.
      if (tab === "tasks" && !tasksLoaded) {
        setupTasksPanel();
        await loadTaskList(); // already catches its own errors internally
        tasksLoaded = true;
      }
    });
  });
}

// ── boot ─────────────────────────────────────────────────────────────────────

async function main() {
  const port = await window.__TAURI__.core.invoke("get_api_port");
  apiBase = `http://127.0.0.1:${port}`;

  setupTabs();
  setupNotesFilter();
  setupListKeyboard();
  document.getElementById("llama-toggle-btn").addEventListener("click", toggleLlama);
  // refreshStatus/refreshClients/loadVaultGraph already catch their own
  // errors (or, for loadNotesBrowser, re-throw so its own caller can tell) —
  // Promise.allSettled (not awaiting each in sequence) means one of these
  // failing can't stop the others, or skip registering the setInterval calls
  // below, the way a plain sequential `await` chain would.
  await Promise.allSettled([
    refreshStatus(),
    refreshClients(),
    loadNotesBrowser(),
    loadVaultGraph(),
  ]);

  setInterval(refreshStatus, 5000);
  setInterval(refreshClients, 5000);
}

window.addEventListener("DOMContentLoaded", main);

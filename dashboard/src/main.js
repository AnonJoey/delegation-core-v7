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

// ── status panel ─────────────────────────────────────────────────────────────

function dot(ok) {
  return `<span class="dot ${ok ? "ok" : "bad"}"></span>`;
}

function stat(label, valueHtml) {
  return `<span class="stat"><span class="stat-label">${label}</span>${valueHtml}</span>`;
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
  } catch (e) {
    el.innerHTML = `<span class="muted">status unavailable</span>`;
  }
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
    el.innerHTML = `<span class="muted">unavailable</span>`;
  }
}

// ── notes browser ────────────────────────────────────────────────────────────

let vaultTree = null;

async function loadNotesBrowser() {
  const folderList = document.getElementById("folder-list");
  try {
    vaultTree = await apiGet("/api/vault/tree");
  } catch (e) {
    folderList.innerHTML = `<div class="muted list-item">unavailable</div>`;
    throw e; // still surfaces to the caller — see main()'s Promise.allSettled
  }
  const folders = Object.keys(vaultTree.folders);
  folderList.innerHTML = folders
    .map(
      (f) =>
        `<div class="list-item" data-folder="${escapeHtml(f)}">${escapeHtml(f)} <span class="count">${vaultTree.folders[f].length}</span></div>`
    )
    .join("");
  folderList.querySelectorAll(".list-item").forEach((el) => {
    el.addEventListener("click", () => selectFolder(el.dataset.folder));
  });
  if (folders.length) selectFolder(folders[0]);
}

function selectFolder(folder) {
  document.querySelectorAll("#folder-list .list-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.folder === folder);
  });
  const noteList = document.getElementById("note-list");
  const notes = vaultTree.folders[folder] || [];
  if (!notes.length) {
    noteList.innerHTML = `<div class="muted list-item">empty</div>`;
    return;
  }
  noteList.innerHTML = notes
    .map((n) => `<div class="list-item" data-path="${escapeHtml(n.path)}">${escapeHtml(n.title)}</div>`)
    .join("");
  noteList.querySelectorAll(".list-item").forEach((el) => {
    el.addEventListener("click", () => selectNote(el.dataset.path, el));
  });
}

async function selectNote(path, el) {
  document.querySelectorAll("#note-list .list-item").forEach((e) => e.classList.remove("active"));
  el.classList.add("active");
  const content = document.getElementById("note-content");
  content.textContent = "loading…";
  content.classList.add("muted");
  try {
    const { content: text } = await apiGet(`/api/vault/note?path=${encodeURIComponent(path)}`);
    content.textContent = text; // textContent, not innerHTML — raw note body, never parsed as markup
    content.classList.remove("muted");
  } catch (e) {
    content.textContent = "could not load note";
  }
}

// ── vault graph (custom force-directed layout, canvas, no external library) ─

class ForceGraph {
  constructor(canvas, data) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.nodes = data.nodes.map((n) => ({
      ...n,
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: 0,
      vy: 0,
    }));
    this.byId = Object.fromEntries(this.nodes.map((n) => [n.id, n]));
    this.edges = data.edges
      .map((e) => ({ source: this.byId[e.source], target: this.byId[e.target] }))
      .filter((e) => e.source && e.target);
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    this.dragNode = null;
    this._bindEvents();
    this._tick();
  }

  _bindEvents() {
    const c = this.canvas;
    let panStart = null;

    c.addEventListener("mousedown", (e) => {
      const { x, y } = this._toWorld(e.offsetX, e.offsetY);
      const hit = this.nodes.find((n) => Math.hypot(n.x - x, n.y - y) < 10);
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
      }
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
    const cx = this.canvas.width / 2, cy = this.canvas.height / 2;
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

    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.save();
    ctx.translate(this.offsetX, this.offsetY);
    ctx.scale(this.scale, this.scale);

    ctx.strokeStyle = border;
    ctx.lineWidth = 1 / this.scale;
    for (const e of this.edges) {
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();
    }

    for (const n of this.nodes) {
      const degree = this.edges.filter((e) => e.source === n || e.target === n).length;
      const r = Math.min(4 + degree * 1.2, 16);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = accent;
      ctx.fill();
    }

    if (this.scale > 0.6) {
      ctx.fillStyle = fg;
      ctx.font = `${11 / this.scale}px sans-serif`;
      for (const n of this.nodes) {
        // fillText, not innerHTML — canvas text draws characters directly,
        // never interpreted as markup, so n.title needs no escaping here.
        ctx.fillText(n.title, n.x + 10, n.y + 4);
      }
    }
    ctx.restore();
  }

  _tick() {
    this._step();
    this._draw();
    requestAnimationFrame(() => this._tick());
  }
}

async function loadVaultGraph() {
  const canvas = document.getElementById("graph-canvas");
  const hint = document.querySelector("#graph-pane .graph-hint");
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  try {
    const data = await apiGet("/api/vault/graph");
    new ForceGraph(canvas, data);
  } catch (e) {
    if (hint) hint.textContent = "Could not load the vault graph.";
    throw e; // still surfaces to the caller — see main()'s Promise.allSettled
  }
}

// ── task tracker ─────────────────────────────────────────────────────────────

let selectedTaskId = null;

async function loadTaskList() {
  const status = document.getElementById("tasks-status-filter").value;
  const el = document.getElementById("task-list");
  try {
    const { processes } = await apiGet(`/api/processes?status=${encodeURIComponent(status)}`);
    if (!processes.length) {
      el.innerHTML = `<div class="muted list-item">no processes</div>`;
      return;
    }
    el.innerHTML = processes
      .map((p) => {
        const done = p.steps.filter((s) => s.done).length;
        const stepsLabel = p.steps.length ? `${done}/${p.steps.length} steps` : p.status;
        return `<div class="task-list-item ${p.id === selectedTaskId ? "active" : ""}" data-id="${escapeHtml(p.id)}">
          <div class="task-name">${escapeHtml(p.name)}</div>
          <div class="task-meta">${escapeHtml(stepsLabel)}</div>
        </div>`;
      })
      .join("");
    el.querySelectorAll(".task-list-item").forEach((item) => {
      item.addEventListener("click", () => selectTask(item.dataset.id));
    });
  } catch (e) {
    el.innerHTML = `<div class="muted list-item">unavailable</div>`;
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
  el.innerHTML = `<div class="muted">loading…</div>`;
  let proc;
  try {
    proc = await apiGet(`/api/processes/get?id=${encodeURIComponent(id)}`);
  } catch (e) {
    el.innerHTML = `<div class="muted">could not load process</div>`;
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

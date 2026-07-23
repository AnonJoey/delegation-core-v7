// delegation-core dashboard frontend. Plain JS, no framework/bundler — fetches
// from the local dashboard_api.py sidecar (see src-tauri/src/lib.rs for how its
// port is picked up) and renders four things: server status, connected MCP
// clients, a notes browser, and the vault as a force-directed wikilink graph.

let apiBase = null;

async function apiGet(path) {
  const res = await fetch(`${apiBase}${path}`);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

// ── status panel ─────────────────────────────────────────────────────────────

function dot(ok) {
  return `<span class="dot ${ok ? "ok" : "bad"}"></span>`;
}

async function refreshStatus() {
  const el = document.getElementById("status-fields");
  try {
    const s = await apiGet("/api/status");
    el.innerHTML = `
      <dt>Vault</dt><dd>${dot(s.vault_ok)}${s.vault_ok ? "ok" : "missing"}</dd>
      <dt>Binary</dt><dd>${dot(s.binary_ok)}${s.binary_ok ? "ok" : "missing"}</dd>
      <dt>Model</dt><dd>${dot(s.model_ok)}${s.model_ok ? "ok" : "missing"}</dd>
      <dt>llama.cpp</dt><dd>${dot(s.llama_state === "online")}${s.llama_state}</dd>
      <dt>Indexed notes</dt><dd>${s.chroma_indexed_notes ?? "—"}</dd>
      <dt>Engine mode</dt><dd>${s.engine_mode}</dd>
      <dt>Synthesis</dt><dd>${s.synthesis_enabled ? "on" : "off"}</dd>
    `;
  } catch (e) {
    el.innerHTML = `<dt class="muted">status unavailable</dt>`;
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
      el.innerHTML = `<li class="muted">no clients connected</li>`;
      return;
    }
    el.innerHTML = clients
      .map(
        (c) => `
      <li>
        <div class="client-name">${c.client_name ?? "unknown"}</div>
        <div class="client-meta">v${c.client_version ?? "?"} · ${c.tool_calls} calls · ${timeAgo(c.seconds_since_active)}</div>
      </li>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<li class="muted">unavailable</li>`;
  }
}

// ── notes browser ────────────────────────────────────────────────────────────

let vaultTree = null;

async function loadNotesBrowser() {
  vaultTree = await apiGet("/api/vault/tree");
  const folderList = document.getElementById("folder-list");
  const folders = Object.keys(vaultTree.folders);
  folderList.innerHTML = folders
    .map(
      (f) =>
        `<div class="list-item" data-folder="${f}">${f} <span class="count">${vaultTree.folders[f].length}</span></div>`
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
    .map((n) => `<div class="list-item" data-path="${n.path}">${n.title}</div>`)
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
    content.textContent = text;
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

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const distSq = Math.max(dx * dx + dy * dy, 1);
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
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  const data = await apiGet("/api/vault/graph");
  new ForceGraph(canvas, data);
}

// ── tabs ─────────────────────────────────────────────────────────────────────

let graphLoaded = false;

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      document.getElementById(`tab-${tab}`).classList.add("active");
      if (tab === "graph" && !graphLoaded) {
        graphLoaded = true;
        await loadVaultGraph();
      }
    });
  });
}

// ── boot ─────────────────────────────────────────────────────────────────────

async function main() {
  const port = await window.__TAURI__.core.invoke("get_api_port");
  apiBase = `http://127.0.0.1:${port}`;

  setupTabs();
  await refreshStatus();
  await refreshClients();
  await loadNotesBrowser();

  setInterval(refreshStatus, 5000);
  setInterval(refreshClients, 5000);
}

window.addEventListener("DOMContentLoaded", main);

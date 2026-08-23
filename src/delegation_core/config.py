"""
config.py — User configuration stored at ~/.delegation_core/config.json.

v0.2 additions:
  synthesis_enabled  toggle the LLM synthesis pipeline (default True)
  synthesis_lang     language for synthesis prompts: "en" | "pt" (default "en")
  budget_mode        "normal" | "cpu" | "auto" — auto measures tok/sec at startup
                     and computes per-task budgets that stay within mcp_timeout_sec
  ingest_chunk_size  chunk size for external ingestion
  ingest_chunk_overlap  overlap for external ingestion chunks

v0.3 additions:
  split_min_chars    files larger than this are split into multiple notes
  split_max_notes    maximum notes produced per recursive split (default 10)

v0.4 additions:
  tok_sec              measured tokens/sec (0 = not calibrated yet)
  mcp_timeout_sec      budget ceiling for auto mode (default 60s)
  quality_threshold    synthesis scores below this trigger repair (default 0.50)
  heal_per_run         max notes re-synthesized per maintenance pass (default 10)
  never_merge_folders  folders excluded from merge and heal passes (default: sessions)

v0.5.1 additions:
  web_search_enabled  opt-in DuckDuckGo web search (default False). Off by
                      default because it reaches the public internet, outside
                      the local-only design. Requires the [web] extra:
                      pip install "delegation-core[web]"
  engine_mode         where generation runs: "local" (llama.cpp, default) or
                      "agent" (no local model — synthesis/compression delegated
                      to the calling Claude). Embeddings + search stay local in
                      both modes. "agent" is for machines that can't spare the
                      RAM/CPU to run a local model alongside other apps.

v0.7.0 additions:
  graphs_dir, graphs_registry_path  storage for the vendored code-graph pipeline
                      (graph_build/graph_list/graph_report tools). Opt-in via the
                      [graph] extra — see delegation_core/graph/__init__.py.
"""

import json
import logging
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("config")

CONFIG_DIR = Path.home() / ".delegation_core"
CONFIG_FILE = CONFIG_DIR / "config.json"


def resolve_folder(name: str, folders: list) -> str | None:
    """Return the canonically-cased entry of `folders` matching `name`, else None.

    Vault folder names are user-defined: the shipped defaults are lowercase
    ("decisions", "research", ...), but a vault configured through the wizard or
    edited by hand commonly uses Capitalized names ("Decisions", "Reference").
    Code that hardcoded a lowercase name and tested `"sessions" in folders`
    silently did the wrong thing on such a vault — export_session wrote to
    folders[0], the classifier's folder hints and its own returned label never
    matched, and orphan accounting skipped nothing. Matching is case-insensitive
    but the *caller* always gets back the real folder name, so the value is safe
    to use directly as a path segment.
    """
    if not name:
        return None
    target = name.strip().lower()
    for f in folders:
        if str(f).strip().lower() == target:
            return f
    return None


@dataclass
class Config:
    # ── vault ────────────────────────────────────────────────────────────────
    vault_path: str = ""
    vault_folders: list = field(default_factory=lambda: [
        "decisions", "research", "tools", "fixes", "reference", "sessions"
    ])

    # ── llama.cpp ────────────────────────────────────────────────────────────
    llama_binary: str = ""
    llama_model: str = ""
    llama_port: int = 8181
    llama_ctx: int = 4096
    llama_ngl: int = 999   # GPU layers to offload (999 = all)

    # ── embeddings ───────────────────────────────────────────────────────────
    bge_model: str = "BAAI/bge-base-en-v1.5"

    # ── similarity thresholds ────────────────────────────────────────────────
    search_threshold: float = 0.55
    merge_threshold: float = 0.88

    # ── inference defaults ───────────────────────────────────────────────────
    max_tokens: int = 2048

    # ── v0.2: synthesis pipeline ─────────────────────────────────────────────
    synthesis_enabled: bool = True
    synthesis_lang: str = "en"   # "en" | "pt"

    # ── v0.2: hardware budget mode ───────────────────────────────────────────
    # "cpu": applies strict token caps to stay within the 120s MCP timeout on
    #        CPU-only machines (SAAD deployment pattern).
    # "normal": no additional caps beyond max_tokens.
    budget_mode: str = "normal"

    # ── v0.2: external ingestion (ABNER) ─────────────────────────────────────
    ingest_chunk_size: int = 4000
    ingest_chunk_overlap: int = 200

    # ── v0.12: vault note chunking ───────────────────────────────────────────
    # Until v0.12 a vault note was indexed as ONE ChromaDB row holding the whole
    # file. Every embedding model has a hard input ceiling, so anything past it
    # was silently dropped: measured on a real vault, a 122k-token code-graph
    # report was 6.7% represented in the index and a 60k-token transcript 13.6%.
    # The remainder was unsearchable with nothing anywhere reporting it missing.
    # Notes are now chunked the way ingest.py has always chunked external files.
    # Sized in CHARACTERS (chunk_text splits on characters) — see
    # embed_max_seq_length for the token ceiling these must stay under.
    vault_chunk_size: int = 4000
    vault_chunk_overlap: int = 200

    # ── v0.12: embedding execution limits ────────────────────────────────────
    # Both pinned explicitly rather than inherited from the model card. bge-m3
    # advertises 8192, and transformer attention costs batch x seq^2: a batch of
    # 8 at 8192 asks roughly 32 GB of attention buffer, which OOMs a 16 GB card
    # mid-reindex — observed in production, not theorised. With chunking above,
    # no chunk comes near this ceiling, so lowering it costs no recall.
    # 0 means "leave the model's own default alone".
    embed_max_seq_length: int = 2048
    embed_batch_size: int = 8

    # ── v0.12: default search scope ──────────────────────────────────────────
    # "" means adaptive — decided per vault from how much of it is generated.
    # A fixed "notes" default was right for the vault it was measured on (3,692
    # generated articles against 187 hand-written notes) and exactly wrong for a
    # vault whose authoritative material is ingested: one field deployment had
    # 6,637 external files invisible to the default every agent uses, and 3 of 4
    # probe queries returned a raw transcript where scope='all' returned the
    # authoritative document. Set explicitly to pin one scope for this machine.
    default_search_scope: str = ""

    # ── v0.12: subkind ranking weights (raw vs curated) ──────────────────────
    # Dampens raw verbose transcripts/chat dumps so authoritative curated notes
    # rank higher when semantic similarity is close. Default 1.0 for curated.
    subkind_weights: dict = field(default_factory=lambda: {
        "curated": 1.0,
        "chat": 0.98,
        "transcript": 0.95,
        "generated": 1.0,
        "external": 1.0,
    })

    # ── v0.3: recursive note splitting ───────────────────────────────────────
    # Files larger than split_min_chars trigger the three-tier split strategy.
    # PDFs with > 1 extractable page are always split regardless of char count.
    split_min_chars: int = 3000
    split_max_notes: int = 10

    # ── v0.4: quality + healing ───────────────────────────────────────────────
    tok_sec: float = 0.0
    mcp_timeout_sec: int = 60
    quality_threshold: float = 0.50
    heal_per_run: int = 10
    never_merge_folders: list = field(default_factory=lambda: ["sessions"])

    # ── v0.5.1: optional web search ──────────────────────────────────────────
    # Opt-in. Reaches the public internet via DuckDuckGo, so it is off by
    # default and its dependency (duckduckgo-search) ships as the [web] extra.
    web_search_enabled: bool = False

    # ── v0.5.1: engine mode ──────────────────────────────────────────────────
    # "local"  — run the model locally via llama.cpp (default).
    # "agent"  — no local model. Synthesis/compression is delegated to the
    #            calling MCP client (Claude): interactive tools return the raw
    #            retrieved material for the agent to reason over, and background
    #            maintenance uses a deterministic extractive fallback.
    # "hybrid" — best of both. Interactive/light work is delegated to Claude
    #            (fast, no local load). Big/slow/bulk generation uses the LOCAL
    #            model instead: background pipelines (synthesize/heal/ingestion)
    #            route local automatically (no agent is in the loop there), and
    #            oversized interactive inputs are NOT auto-run — the tool returns
    #            a token-cost estimate plus an explicit offer to run locally
    #            (pass use_local=true) so the choice is surfaced, never silent.
    # BGE embeddings + ChromaDB search always run locally in every mode.
    engine_mode: str = "local"

    # ── v0.5.1: hybrid routing ───────────────────────────────────────────────
    # In hybrid mode, an interactive input at or above this many characters is
    # treated as "big": instead of delegating to Claude, the tool returns a
    # cost estimate and offers the local model (opt-in via use_local=true).
    hybrid_local_min_chars: int = 8000

    # ── v0.11: HTTP transport ────────────────────────────────────────────────
    # The server is a single long-lived daemon that every MCP client connects
    # to over HTTP, replacing the previous stdio model where each client
    # spawned its own process (and its own BGE copy, and its own ChromaDB
    # writer — see client_tracking.py's module docstring for what that cost).
    #
    # server_host stays on loopback. Nothing here is designed to be reachable
    # from another machine: the token below is a guard against other *local*
    # processes, not a substitute for network isolation.
    server_host: str = "127.0.0.1"
    server_port: int = 8787
    server_path: str = "/mcp"

    # Bearer token every client must present. Generated on first use by
    # ensure_server_token(); empty means "not yet generated", never "no auth".
    # dashboard_api.py learned this the hard way in v0.8.1 — an unauthenticated
    # local port let any website in the user's browser read and write the vault
    # via a guessed port. The surface here is the whole vault plus every tool,
    # so there is no unauthenticated mode to fall back to.
    server_token: str = ""

    # The dashboard's JSON API, served by the daemon on a second loopback port.
    # It used to be a sidecar the Tauri app spawned, which was the right shape
    # under stdio — mcp.run() served one transport, so the dashboard could not
    # attach to the MCP server and needed its own process. That process then
    # built its own VaultManager: measured on this machine, opening the
    # dashboard added a second 2314 MiB copy of BGE-m3 to the GPU and a second
    # ChromaDB opener on the same index, which is exactly what moving to a
    # daemon removed for every other client.
    #
    # The daemon already holds a warm VaultManager, so it serves those routes
    # itself. 0 disables it, and the sidecar entry point still works standalone
    # for a machine running no daemon.
    dashboard_port: int = 8788

    # How long the local model may sit loaded with an empty task line before the
    # worker unloads it. A queued task pins ~11.5 GiB on this machine (gemma-12B
    # Q6 alongside BGE-m3, measured: 3838 -> 15386 MiB), and without this a
    # single task holds that until the next daemon restart — reintroducing
    # exactly the GPU contention that engine_mode "agent" exists to avoid.
    #
    # Only applies in agent mode, where the local model runs solely to serve the
    # queue. In local/hybrid mode it is the engine every other caller uses, and
    # unloading it under them would turn one idle minute into a 10s reload on
    # the next call. 0 disables the unload entirely.
    local_idle_shutdown_sec: int = 300

    # ── v0.11: local-model queue ─────────────────────────────────────────────
    # How many llama.cpp requests may be in flight at once. One daemon now
    # fronts every client, so without this a burst of clients would stampede a
    # single local model. 1 keeps the model's own batching predictable; raise
    # it only if the model is served with real parallel slots.
    llama_queue_concurrency: int = 1

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def llama_url(self) -> str:
        return f"http://localhost:{self.llama_port}"

    @property
    def server_url(self) -> str:
        """The MCP endpoint clients put in their config. Always loopback."""
        return f"http://{self.server_host}:{self.server_port}{self.server_path}"

    @property
    def vault(self) -> Path:
        return Path(self.vault_path).expanduser()

    @property
    def chroma_path(self) -> Path:
        return self.vault / ".chroma_bge"

    @property
    def collection_name(self) -> str:
        """ChromaDB collection for the configured embedding model.

        Derived rather than fixed so two models can be indexed side by side in the
        same store: their vector dimensions differ (768 vs 1024), so a shared
        collection would reject the second one outright. With one collection each,
        switching models is a config edit — the other index is still there.
        """
        from .embeddings import collection_name_for
        return collection_name_for(self.bge_model)

    @property
    def log_path(self) -> Path:
        return CONFIG_DIR / "server.log"

    @property
    def llama_log_path(self) -> Path:
        return CONFIG_DIR / "llama_cpp.log"

    @property
    def models_dir(self) -> Path:
        return CONFIG_DIR / "models"

    @property
    def llama_dir(self) -> Path:
        return CONFIG_DIR / "llama"

    @property
    def processes_path(self) -> Path:
        return CONFIG_DIR / "processes.json"

    @property
    def graphs_dir(self) -> Path:
        return CONFIG_DIR / "graphs"

    @property
    def graphs_registry_path(self) -> Path:
        return CONFIG_DIR / "graphs_registry.json"

    @property
    def is_cpu_budget(self) -> bool:
        return self.budget_mode == "cpu"

    @property
    def is_agent_mode(self) -> bool:
        """True when generation is delegated to the calling Claude (no local model)."""
        return self.engine_mode == "agent"

    @property
    def is_hybrid_mode(self) -> bool:
        """True when light work goes to Claude but big/bulk work uses the local model."""
        return self.engine_mode == "hybrid"

    @property
    def uses_local_model(self) -> bool:
        """True when this mode may need llama.cpp (local always; hybrid for big/bulk)."""
        return self.engine_mode in ("local", "hybrid")

    # Background/bulk generation tasks: no agent is in the loop when these run
    # (fire-and-forget maintenance/ingestion), so in hybrid they route to the
    # local model rather than being delegated to Claude.
    _HEAVY_TASKS = frozenset({"synthesize", "heal", "review_body"})

    def route(self, task: str = "default", input_chars: int = 0, use_local: bool = False) -> str:
        """Decide where a generation call runs. Returns:
          "local" — run llama.cpp locally
          "agent" — delegate to the calling Claude (return raw material)
          "offer" — big interactive input: don't auto-run; return a cost estimate
                    and offer the local model (caller re-invokes with use_local)
        Considers engine_mode + task type + input size + explicit opt-in.
        """
        if self.engine_mode == "local":
            return "local"
        if self.engine_mode == "agent":
            return "agent"
        # hybrid — nuanced routing
        if use_local:
            return "local"
        if task in self._HEAVY_TASKS:
            return "local"                       # background/bulk always local
        if input_chars >= self.hybrid_local_min_chars:
            return "offer"                       # big: surface the choice + cost
        return "agent"                           # light interactive → Claude

    def is_configured(self) -> bool:
        return bool(self.vault_path and self.llama_binary and self.llama_model)

    # ── persistence ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                return cls(**known)
            except Exception as e:
                logger.error("Could not load %s: %s — using defaults", CONFIG_FILE, e)
        return cls()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        # server_token lives in here, so the file is a secret now. Best-effort:
        # chmod is a no-op for this purpose on Windows, where the user profile
        # directory is what actually restricts access.
        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass

    def ensure_server_token(self) -> str:
        """Return the bearer token, generating and persisting one on first call.

        Called from the server's startup path rather than only from `setup`, so
        an install that predates HTTP transport (or a config edited by hand)
        still comes up authenticated instead of coming up open.
        """
        if not self.server_token:
            self.server_token = secrets.token_urlsafe(32)
            self.save()
        return self.server_token

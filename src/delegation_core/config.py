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
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("config")

CONFIG_DIR = Path.home() / ".delegation_core"
CONFIG_FILE = CONFIG_DIR / "config.json"


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

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def llama_url(self) -> str:
        return f"http://localhost:{self.llama_port}"

    @property
    def vault(self) -> Path:
        return Path(self.vault_path).expanduser()

    @property
    def chroma_path(self) -> Path:
        return self.vault / ".chroma_bge"

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

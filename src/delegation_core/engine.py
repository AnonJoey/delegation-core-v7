"""
engine.py — DelegationEngine: manages the llama.cpp subprocess and inference.

v0.2: budget_mode awareness — when cfg.is_cpu_budget, hard caps are applied to
max_tokens in invoke() so the server stays within the 120s MCP client timeout
on CPU-only hardware (SAAD deployment pattern).

v0.4: async inference via httpx.AsyncClient. Subprocess management (startup
health polling, _start) remains sync and runs in a thread executor when called
from async context. Per-task budgets apply in all modes; CPU mode applies
stricter caps on top.

v0.4: auto budget mode — calibrate() measures actual tok/sec at startup;
_compute_budgets() derives per-task caps that stay within mcp_timeout_sec
(default 60s) with a 0.70 safety factor. budget_mode = "auto" selects this path.
"""

import asyncio
import atexit
import logging
import platform
import subprocess
import threading
import time

import httpx
import requests

from .config import Config

logger = logging.getLogger("engine")

# Normal-mode per-task token defaults
_TASK_BUDGETS: dict[str, int] = {
    "classify":       15,
    "compress":       400,
    "search_summary": 300,
    "synthesize":     2500,
    "summary":        200,
    "section_title":  20,
    "default":        512,
}

# CPU mode applies these stricter caps instead
_CPU_TASK_BUDGETS: dict[str, int] = {
    "classify":       8,
    "compress":       200,
    "search_summary": 180,
    "synthesize":     2500,
    "summary":        200,
    "section_title":  20,
    "default":        256,
}


def _compute_budgets(tok_sec: float, timeout_sec: int) -> dict[str, int]:
    """Compute per-task token caps from measured throughput and MCP timeout.

    Safety factor 0.70 keeps all tasks 30% inside the MCP timeout wall.
    Synthesis gets 1.5× headroom because it benefits most from longer output.
    """
    from math import floor
    ceiling = max(floor(timeout_sec * tok_sec * 0.70), 20)
    if ceiling < 50:
        import logging as _log
        _log.getLogger("engine").warning(
            "_compute_budgets: very low ceiling=%d (%.1f tok/sec × %ds × 0.70). "
            "Run 'delegation-core run --recalibrate' if this seems wrong.",
            ceiling, tok_sec, timeout_sec,
        )
    return {
        "classify":       min(15,   ceiling),
        "compress":       min(400,  ceiling),
        "search_summary": min(300,  ceiling),
        "synthesize":     min(2500, floor(ceiling * 1.5)),
        "summary":        min(200,  ceiling),
        "section_title":  min(20,   ceiling),
        "default":        min(512,  ceiling),
    }


def _detached_popen_kwargs() -> dict:
    if platform.system() == "Windows":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class DelegationEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self._we_started_it = False
        self._log_fh = None
        self._start_lock = threading.Lock()
        self._http: httpx.AsyncClient | None = None
        atexit.register(self._shutdown)

    # ── async HTTP client ─────────────────────────────────────────────────────

    @property
    def _async_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
                timeout=httpx.Timeout(3600.0, connect=10.0),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> "DelegationEngine":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()

    # ── public ───────────────────────────────────────────────────────────────

    async def ensure_running(self) -> bool:
        if self.cfg.is_agent_mode:
            return False   # no local model to run in agent mode
        if await self.check_health():
            return True
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._start_locked)

    async def check_health(self) -> bool:
        if self.cfg.is_agent_mode:
            return False   # nothing to health-check; generation is delegated
        try:
            r = await self._async_client.get(f"{self.cfg.llama_url}/health", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def budget(self, task: str, requested: int = 0) -> int:
        """Return effective max_tokens for a task, respecting budget_mode.

        auto   mode: caps derived from calibrated tok_sec + mcp_timeout_sec.
        cpu    mode: stricter fixed caps from _CPU_TASK_BUDGETS.
        normal mode: per-task defaults from _TASK_BUDGETS.
        If requested > 0: min(requested, effective cap).
        """
        if self.cfg.budget_mode == "auto" and self.cfg.tok_sec > 0:
            budgets = _compute_budgets(self.cfg.tok_sec, self.cfg.mcp_timeout_sec)
        elif self.cfg.is_cpu_budget:
            budgets = _CPU_TASK_BUDGETS
        else:
            budgets = _TASK_BUDGETS
        cap = budgets.get(task, budgets.get("default", 512))
        return min(requested, cap) if requested else cap

    async def calibrate(self) -> float:
        """Measure actual tok/sec from a direct API call that reads completion_tokens.

        Using invoke() would discard usage stats. We call the endpoint directly so we
        can read r.json()["usage"]["completion_tokens"] and avoid dividing by a fixed
        target that the model may not reach (EOS fires early at ~15-20 tokens for
        "count to ten", causing a ~2× overestimate when target_tokens=40 is used).
        """
        await self.ensure_running()
        payload = {
            "model": "local",
            "messages": [{"role": "user", "content": "Write the numbers one to twenty, one per line."}],
            "max_tokens": 100,
            "temperature": 0.0,
        }
        start = time.monotonic()
        r = await self._async_client.post(
            f"{self.cfg.llama_url}/v1/chat/completions",
            json=payload,
        )
        elapsed = max(time.monotonic() - start, 0.5)
        data = r.json()
        actual_tokens = data.get("usage", {}).get("completion_tokens", 0)
        if actual_tokens <= 0:
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            actual_tokens = max(len(content) // 4, 5)
        tok_sec = round(actual_tokens / elapsed, 2)
        self.cfg.tok_sec = tok_sec
        self.cfg.save()
        logger.info("Calibrated: %.2f tok/sec (%.1fs for %d actual tokens)", tok_sec, elapsed, actual_tokens)
        return tok_sec

    async def invoke(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 0,
        temperature: float = 0.4,
        task: str = "default",
        max_retries: int = 3,
        retry_delay: int = 20,
    ) -> str:
        """Async call to llama.cpp /v1/chat/completions. task selects budget cap."""
        # v5.1 agent mode: no local model. Interactive tools branch to hand raw
        # context to the calling Claude before reaching invoke(); the callers
        # that DO reach here are background/no-agent pipelines (classify,
        # synthesize, heal) which cannot call back into the agent. Give them a
        # deterministic extractive reduction so maintenance never hangs on a
        # model that isn't there.
        if self.cfg.is_agent_mode:
            return self._extractive_fallback(prompt, self.budget(task, max_tokens))

        if not await self.ensure_running():
            raise RuntimeError(
                "llama.cpp could not be reached or started. "
                "Check llama_binary and llama_model in ~/.delegation_core/config.json"
            )

        effective_tokens = self.budget(task, max_tokens)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": "local",
            "messages": messages,
            "max_tokens": effective_tokens,
            "temperature": temperature,
        }

        for attempt in range(1, max_retries + 1):
            try:
                r = await self._async_client.post(
                    f"{self.cfg.llama_url}/v1/chat/completions",
                    json=payload,
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
                if r.status_code in (503, 429):
                    logger.warning("llama.cpp busy (%s). Retry %d/%d", r.status_code, attempt, max_retries)
                    await asyncio.sleep(retry_delay)
                    continue
                r.raise_for_status()
            except httpx.TimeoutException:
                logger.warning("Inference timeout. Retry %d/%d", attempt, max_retries)
                await asyncio.sleep(retry_delay)
            except Exception as e:
                if attempt >= max_retries:
                    raise RuntimeError(f"Delegation failed after {max_retries} attempts: {e}")
                await asyncio.sleep(retry_delay)

        raise RuntimeError("Exhausted retries without success.")

    # ── private ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extractive_fallback(prompt: str, max_tokens: int) -> str:
        """Deterministic, zero-compute reduction used in agent mode for
        background callers that can't delegate to the agent.

        Callers format prompts as "<instruction>\\n\\n<raw payload>", so we drop
        the leading instruction line and return the payload truncated to roughly
        max_tokens*4 characters (the usual token→char rule of thumb). This is
        not a summary — it's a safe pass-through so maintenance keeps moving; the
        real summarization happens interactively when the agent is in the loop.
        """
        payload = prompt.strip()
        if "\n\n" in payload:
            tail = payload.split("\n\n", 1)[1].strip()
            payload = tail or payload
        char_cap = max(200, int(max_tokens) * 4)
        return payload[:char_cap]

    def _is_healthy(self) -> bool:
        """Sync health check used during subprocess startup polling."""
        try:
            r = requests.get(f"{self.cfg.llama_url}/health", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def _start_locked(self) -> bool:
        with self._start_lock:
            if self._is_healthy():
                return True
            return self._start()

    def _start(self) -> bool:
        from pathlib import Path

        binary = Path(self.cfg.llama_binary).expanduser()
        model = Path(self.cfg.llama_model).expanduser()

        if not binary.exists():
            logger.error("llama-server binary not found: %s", binary)
            return False
        if not model.exists():
            logger.error("Model file not found: %s", model)
            return False

        cmd = [
            str(binary),
            "--model", str(model),
            "--port", str(self.cfg.llama_port),
            "--ctx-size", str(self.cfg.llama_ctx),
            "--n-gpu-layers", str(self.cfg.llama_ngl),
        ]
        logger.info("Starting llama.cpp: %s", " ".join(cmd))

        try:
            # llama.cpp's stdout/stderr are redirected straight into this file
            # for the process's whole lifetime — a plain "a" open with no
            # rotation grows unbounded across restarts (observed at ~490KB
            # after normal use on one dev machine, and this runs as a
            # long-lived autostart service). subprocess needs a real fd, not
            # a logging.Handler, so rotate by size at each startup instead:
            # good enough since growth only matters over the many restarts a
            # persistent service accumulates, not within a single run.
            _MAX_LLAMA_LOG_BYTES = 10 * 1024 * 1024
            log_path = self.cfg.llama_log_path
            if log_path.exists() and log_path.stat().st_size > _MAX_LLAMA_LOG_BYTES:
                rotated = log_path.with_suffix(log_path.suffix + ".1")
                rotated.unlink(missing_ok=True)
                log_path.rename(rotated)
            self._log_fh = open(log_path, "a")
            self._proc = subprocess.Popen(
                cmd,
                stdout=self._log_fh,
                stderr=self._log_fh,
                **_detached_popen_kwargs(),
            )
            self._we_started_it = True
        except Exception as e:
            logger.error("Failed to start llama.cpp: %s", e)
            return False

        for i in range(45):
            time.sleep(2)
            if self._is_healthy():
                logger.info("llama.cpp ready after %ds", (i + 1) * 2)
                return True
            if self._proc.poll() is not None:
                logger.error("llama.cpp exited prematurely — check %s", self.cfg.llama_log_path)
                return False

        logger.error("llama.cpp did not become healthy within 90s")
        return False

    def _shutdown(self):
        if self._we_started_it and self._proc and self._proc.poll() is None:
            logger.info("Stopping llama.cpp subprocess")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

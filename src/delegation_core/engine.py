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
from contextlib import asynccontextmanager

import httpx
import requests

from .config import Config

logger = logging.getLogger("engine")


class EmptyAnswer(RuntimeError):
    """The model answered with no text at all.

    Its own type because it is the one failure at this seam that must NOT be
    retried: the server responded, in time, with an empty message. Same prompt,
    same budget, same empty answer three retries later.
    """


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


# ── local-model queue ────────────────────────────────────────────────────────
#
# Process-global on purpose. llama.cpp is ONE server process shared by everything
# in here, while DelegationEngine gets instantiated more than once (server.py
# builds one, cli.py builds its own). A per-instance gate would let those two
# stampede the same model, which is the exact thing this exists to stop.
#
# Why a threading.Semaphore and not asyncio.Semaphore: this process runs several
# event loops. jobs.submit() calls asyncio.run() inside a daemon thread — the
# comment at server.py's _run_bg spells that out — so an asyncio primitive
# created on the main loop and awaited from a job's loop raises "attached to a
# different loop". A threading semaphore is loop-agnostic; the wait is handed to
# an executor thread so no event loop is ever blocked while queueing.
_model_gate: threading.Semaphore | None = None
_model_gate_lock = threading.Lock()
_queue_waiting = 0          # callers parked in the queue, not yet running
_queue_running = 0          # callers currently talking to llama.cpp
_queue_counters_lock = threading.Lock()


def _gate(concurrency: int) -> threading.Semaphore:
    """Return the process-wide gate, sized by the first caller to need it."""
    global _model_gate
    if _model_gate is None:
        with _model_gate_lock:
            if _model_gate is None:
                _model_gate = threading.Semaphore(max(1, concurrency))
                logger.info("Local-model queue armed at concurrency=%d", max(1, concurrency))
    return _model_gate


def queue_stats() -> dict:
    """Snapshot of the local-model queue, for heartbeat()/dashboards."""
    with _queue_counters_lock:
        return {"waiting": _queue_waiting, "running": _queue_running}


def _reset_queue_for_tests() -> None:
    """Drop the gate so a test can re-arm it at a different concurrency."""
    global _model_gate, _queue_waiting, _queue_running
    with _model_gate_lock:
        _model_gate = None
    with _queue_counters_lock:
        _queue_waiting = 0
        _queue_running = 0


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

    async def ensure_running(self, force: bool = False) -> bool:
        # force=True is the local task line (localworker.py): work an agent
        # queued *for the local model*, which must run even in agent mode. Every
        # other caller keeps the old behaviour, so nothing the daemon starts by
        # itself can wake llama.cpp.
        if self.cfg.is_agent_mode and not force:
            return False   # no local model to run in agent mode
        if await self.check_health(force=force):
            return True
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._start_locked)

    async def check_health(self, force: bool = False) -> bool:
        if self.cfg.is_agent_mode and not force:
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
        # Calibrate in the SAME regime inference runs in, or the number it
        # produces describes a different machine than the one that will serve
        # the requests. With thinking on, a reasoning model spends most of its
        # completion tokens on a channel the caller never receives, so the
        # per-task caps _compute_budgets derives from this measurement are caps
        # on thought rather than on answer — and every task truncates.
        if not getattr(self.cfg, "llama_enable_thinking", False):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
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
        force_local: bool = False,
    ) -> str:
        """Async call to llama.cpp /v1/chat/completions. task selects budget cap.

        force_local bypasses the agent-mode short-circuit below. It exists for
        the local task line: a task an agent explicitly queued for the local
        model, where returning an extractive summary instead of running it would
        answer a different question than the one asked.
        """
        # v5.1 agent mode: no local model. Interactive tools branch to hand raw
        # context to the calling Claude before reaching invoke(); the callers
        # that DO reach here are background/no-agent pipelines (classify,
        # synthesize, heal) which cannot call back into the agent. Give them a
        # deterministic extractive reduction so maintenance never hangs on a
        # model that isn't there.
        if self.cfg.is_agent_mode and not force_local:
            return self._extractive_fallback(prompt, self.budget(task, max_tokens))

        # Everything past here talks to the one local model, so it queues. The
        # gate is taken before ensure_running() deliberately: starting llama.cpp
        # is itself the heaviest thing that can happen here, and letting four
        # callers discover a cold model simultaneously is how you get four
        # startup attempts racing for the same port.
        async with self._queued():
            return await self._invoke_now(
                prompt, system, max_tokens, temperature, task, max_retries,
                retry_delay, force_local=force_local,
            )

    @asynccontextmanager
    async def _queued(self):
        """Hold the process-wide local-model gate for the duration of the block."""
        global _queue_waiting, _queue_running
        gate = _gate(self.cfg.llama_queue_concurrency)

        # Fast path: gate free, no executor hop, no measurable overhead.
        if not gate.acquire(blocking=False):
            with _queue_counters_lock:
                _queue_waiting += 1
            waited_from = time.time()
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, gate.acquire)
            finally:
                with _queue_counters_lock:
                    _queue_waiting -= 1
            logger.info("Waited %.1fs in the local-model queue", time.time() - waited_from)

        with _queue_counters_lock:
            _queue_running += 1
        try:
            yield
        finally:
            with _queue_counters_lock:
                _queue_running -= 1
            gate.release()

    async def _invoke_now(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 0,
        temperature: float = 0.4,
        task: str = "default",
        max_retries: int = 3,
        retry_delay: int = 20,
        force_local: bool = False,
    ) -> str:
        """invoke()'s body, run with the local-model gate already held."""
        if not await self.ensure_running(force=force_local):
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
        # A reasoning model splits its output in two: `reasoning_content` for
        # the private thought channel, `content` for the answer. The budget is
        # spent on the first before a single token of the second is written, so
        # a long prompt with a bounded budget returns HTTP 200, finish_reason
        # "length", and an EMPTY answer. Nothing in the response says the model
        # ran out; the caller just gets "".
        #
        # `chat_template_kwargs.enable_thinking` is the server-side off switch
        # and it is honoured by llama.cpp's OpenAI endpoint. Note that
        # `reasoning_format: "none"` is NOT the same thing and is not a
        # substitute: measured here, it leaves thinking on and dumps the raw
        # `<|channel>thought` text into `content` instead.
        if not getattr(self.cfg, "llama_enable_thinking", False):
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        for attempt in range(1, max_retries + 1):
            try:
                r = await self._async_client.post(
                    f"{self.cfg.llama_url}/v1/chat/completions",
                    json=payload,
                )
                if r.status_code == 200:
                    return self._answer_of(r.json())
                if r.status_code in (503, 429):
                    logger.warning("llama.cpp busy (%s). Retry %d/%d", r.status_code, attempt, max_retries)
                    await asyncio.sleep(retry_delay)
                    continue
                r.raise_for_status()
            except httpx.TimeoutException:
                logger.warning("Inference timeout. Retry %d/%d", attempt, max_retries)
                await asyncio.sleep(retry_delay)
            except EmptyAnswer:
                # Not transient. The server answered, in time, with nothing —
                # the same prompt and the same budget will answer with nothing
                # again. Retrying it burns three retry_delays (60s at the
                # default) and then reports "Delegation failed after 3
                # attempts", which buries the one sentence that says what
                # actually happened. Raise it as it is.
                raise
            except Exception as e:
                if attempt >= max_retries:
                    raise RuntimeError(f"Delegation failed after {max_retries} attempts: {e}")
                await asyncio.sleep(retry_delay)

        raise RuntimeError("Exhausted retries without success.")

    # ── private ──────────────────────────────────────────────────────────────

    @staticmethod
    def _answer_of(data: dict) -> str:
        """The model's answer, and never a silent empty string.

        Three things go wrong at this seam and all three look like success:

        1. `content` is absent, because a reasoning model wrote only into
           `reasoning_content` before the budget ran out;
        2. `content` is JSON `null` rather than a string, which used to return
           `None` from a function annotated `-> str` and blow up at the caller
           instead of here;
        3. the answer is whitespace.

        Falling back to `reasoning_content` is deliberate: a truncated thought
        is worth more to the caller than nothing at all, and it makes the
        failure visible in the result instead of invisible in its absence. If
        both are empty the response carried no answer, and that is an error the
        caller must be told about rather than a task that quietly succeeded.
        """
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"llama.cpp returned no choices: {e}") from e

        content = (message.get("content") or "").strip()
        if content:
            return content

        reasoning = (message.get("reasoning_content") or "").strip()
        if reasoning:
            finish = (data.get("choices") or [{}])[0].get("finish_reason", "")
            logger.warning(
                "Model wrote only into reasoning_content (finish_reason=%s) — "
                "returning the thought channel. Raise max_tokens or keep "
                "llama_enable_thinking off.", finish,
            )
            return reasoning

        usage = data.get("usage") or {}
        raise EmptyAnswer(
            "llama.cpp answered with an empty message "
            f"(finish_reason={(data.get('choices') or [{}])[0].get('finish_reason')!r}, "
            f"completion_tokens={usage.get('completion_tokens')}). "
            "The budget was most likely spent on the reasoning channel."
        )


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
            # Endurecimento e nao defeito: o objeto vai para Popen(stdout=...),
            # que usa o fd cru, entao o llama.cpp escreve os proprios bytes.
            # Explicito para que uma escrita futura em Python por aqui nao herde
            # o encoding do locale.
            self._log_fh = open(log_path, "a", encoding="utf-8")
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

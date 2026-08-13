"""daemon.py + the CLI commands that write to the index.

The property under test is narrow and is the whole point of the change: when a
daemon is running, `reindex`/`maintain`/`ingest` must not build a VaultManager.
A second VaultManager is a second ChromaDB writer against a directory the daemon
holds open, and a second ~2.4 GiB copy of BGE on the GPU — the exact pair the
HTTP transport was supposed to end, still reachable through the hooks, which
fire these three commands as detached processes.

So the tests do not check that "a call was made". They monkeypatch VaultManager
to explode and assert the command still succeeds, which fails loudly if anyone
later reintroduces a local path under a live daemon.
"""

import json
import socket

import pytest

from delegation_core import cli, daemon, jobs
from delegation_core.config import Config


def _cfg(**over):
    base = dict(vault_path="/tmp", server_host="127.0.0.1", server_port=8787,
                server_path="/mcp", server_token="tok-abc",
                # is_configured() gates every command; these make the fake config
                # a configured one without touching the machine's real config.
                llama_binary="/bin/true", llama_model="/tmp/model.gguf")
    base.update(over)
    return Config(**base)


class _Args:
    """argparse.Namespace stand-in — only the attributes a command reads."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ── result unwrapping ────────────────────────────────────────────────────────

class _Content:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, text):
        self.content = [_Content(text)]


def test_payload_parses_the_json_every_tool_returns():
    assert daemon._payload(_Result(json.dumps({"job_id": "abc"}))) == {"job_id": "abc"}


def test_payload_survives_a_tool_that_answers_plain_text():
    """Better a usable dict than an exception in a hook nobody is watching."""
    assert daemon._payload(_Result("not json")) == {"result": "not json"}
    assert daemon._payload(_Result("")) == {}


# ── liveness probe ───────────────────────────────────────────────────────────

def test_is_listening_is_false_when_nothing_holds_the_port():
    # Bind and release: the port is known-free rather than assumed-free.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert daemon.is_listening(_cfg(server_port=port)) is False


def test_is_listening_is_true_against_a_real_socket():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        assert daemon.is_listening(_cfg(server_port=s.getsockname()[1])) is True


# ── connection-error classification ──────────────────────────────────────────

class _FakeConnectError(Exception):
    """Stands in for httpx.ConnectError, matched by name like the real one."""
    def __init__(self):
        super().__init__("connection refused")
    __qualname__ = "ConnectError"


_FakeConnectError.__name__ = "ConnectError"


def test_connection_errors_are_recognised_through_wrapping():
    """FastMCP and anyio both re-wrap; a refused connection must stay
    recognisable as 'no daemon' rather than becoming a hard failure."""
    wrapped = RuntimeError("client failed")
    wrapped.__cause__ = _FakeConnectError()
    assert daemon._is_connection_error(wrapped)
    assert daemon._is_connection_error(ExceptionGroup("tg", [_FakeConnectError()]))
    assert not daemon._is_connection_error(ValueError("tool rejected the argument"))


# ── background job waiting ───────────────────────────────────────────────────

class _FakeClient:
    """One MCP session, scripted. Records the tool calls it was asked for.

    Counting sessions matters: the submit and every poll must share one, or a
    single `maintain` registers three separate rows in the daemon's
    connected-client tracking and pays three initialize/GET/DELETE round trips.
    """

    sessions_opened = 0

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        type(self).sessions_opened += 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return _Result(json.dumps(self.responses.pop(0)))


def _daemon_serving(monkeypatch, responses, sleeps=None):
    """A reachable daemon that answers with `responses`, without a socket."""
    client = _FakeClient(list(responses))
    _FakeClient.sessions_opened = 0
    recorded = sleeps if sleeps is not None else []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(daemon, "is_listening", lambda cfg, timeout=0.5: True)
    monkeypatch.setattr(daemon, "_build_client", lambda cfg, timeout: client)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)
    return client


def test_submit_and_wait_polls_until_the_job_is_done(monkeypatch):
    client = _daemon_serving(monkeypatch, [
        {"job_id": "j1", "status": "running"},
        {"job_id": "j1", "status": "running", "elapsed_seconds": 2},
        {"job_id": "j1", "status": "done", "result": 4211},
    ])
    job = daemon.submit_and_wait(_cfg(), "vault_reindex_bg", {"force": False})
    assert job["result"] == 4211
    assert [t for t, _ in client.calls] == [
        "vault_reindex_bg", "task_status", "task_status"]


def test_submit_and_its_polls_share_one_session(monkeypatch):
    _daemon_serving(monkeypatch, [
        {"job_id": "j1", "status": "running"},
        {"job_id": "j1", "status": "running"},
        {"job_id": "j1", "status": "done", "result": 1},
    ])
    daemon.submit_and_wait(_cfg(), "vault_reindex_bg", {})
    assert _FakeClient.sessions_opened == 1


def test_a_synchronous_tool_is_returned_as_is(monkeypatch):
    _daemon_serving(monkeypatch, [{"indexed": 3, "skipped": 0}])
    assert daemon.submit_and_wait(_cfg(), "ingest_folder", {})["indexed"] == 3


def test_a_healthy_job_carries_an_error_key_and_is_not_a_failure():
    """jobs.submit() seeds every job with error=None, so `"error" in status` is
    true for a perfectly healthy job. Reading the key instead of its value made
    the first real run report a *finished* reindex as a lost job, while the
    daemon's own log showed the work completing — this is that shape verbatim."""
    running = {"job_id": "j1", "task": "vault_reindex", "status": "running",
               "started": "2026-08-12T18:36:35", "finished": None, "result": None,
               "error": None, "elapsed_seconds": 1}
    done = dict(running, status="done", result=3627,
                finished="2026-08-12T18:36:36")
    poll = daemon._Poll(daemon.JOB_WAIT_TIMEOUT_SEC)
    assert daemon.next_poll_wait(running, "j1", "vault_reindex_bg", poll) is not None
    assert daemon.next_poll_wait(done, "j1", "vault_reindex_bg", poll) is None


def test_a_vanished_job_is_an_error_not_a_silent_success():
    """Job ids are in-memory: a daemon restart mid-run loses them. Returning
    normally there would report success for work that may never have finished."""
    poll = daemon._Poll(daemon.JOB_WAIT_TIMEOUT_SEC)
    with pytest.raises(daemon.DaemonCallFailed, match="lost job"):
        daemon.next_poll_wait({"error": "Job 'j1' not found."}, "j1",
                              "run_maintenance_bg", poll)


def test_the_enriched_not_found_payload_is_still_read_as_not_found():
    """task_status() explains a vanished job; next_poll_wait() must still see it.

    The two modules are coupled through the *absence* of a "status" key, which is
    easy to break from the server side without touching this file: any status
    value here reads as neither done nor error, so the CLI would poll a job that
    does not exist until it timed out. This pins the real payload, built the way
    server.task_status builds it, against the reader.
    """
    payload = {"error": "Job 'j1' not found.",
               "job_store_started": jobs.STARTED_AT.isoformat(),
               "hint": "Job ids live in this daemon's memory..."}
    poll = daemon._Poll(daemon.JOB_WAIT_TIMEOUT_SEC)
    with pytest.raises(daemon.DaemonCallFailed, match="lost job"):
        daemon.next_poll_wait(payload, "j1", "run_maintenance_bg", poll)


def test_a_failed_job_is_reported_as_a_failure():
    poll = daemon._Poll(daemon.JOB_WAIT_TIMEOUT_SEC)
    with pytest.raises(daemon.DaemonCallFailed, match="GPU out of memory"):
        daemon.next_poll_wait({"job_id": "j1", "status": "error",
                               "error": "GPU out of memory"},
                              "j1", "vault_reindex_bg", poll)


def test_a_job_that_outlives_the_timeout_says_it_is_still_running():
    """Left running on the daemon is the truth — the CLI just stopped waiting."""
    poll = daemon._Poll(timeout=0.0)
    with pytest.raises(daemon.DaemonCallFailed, match="still running"):
        daemon.next_poll_wait({"job_id": "j1", "status": "running"}, "j1",
                              "vault_reindex_bg", poll)


def test_a_fast_job_is_not_held_by_the_daemons_polling_hint(monkeypatch):
    """check_again_in_seconds has a 30s floor and is written for an agent that
    spends a turn per poll. Obeying it made a 70ms incremental reindex take
    10.6s of wall clock — measured against the live daemon, not supposed."""
    sleeps = []
    _daemon_serving(monkeypatch, [
        {"job_id": "j1", "status": "running"},
        {"job_id": "j1", "status": "running", "check_again_in_seconds": 30},
        {"job_id": "j1", "status": "done", "result": 1},
    ], sleeps)
    daemon.submit_and_wait(_cfg(), "vault_reindex_bg", {})
    assert sleeps == [daemon.POLL_INITIAL_SEC]


def test_a_long_job_backs_off_instead_of_hammering_the_daemon():
    """The other half: a job that really does run for minutes must not be
    polled four times a second for its whole duration."""
    poll = daemon._Poll(daemon.JOB_WAIT_TIMEOUT_SEC)
    running = {"job_id": "j1", "status": "running", "check_again_in_seconds": 300}
    waits = [daemon.next_poll_wait(running, "j1", "graph_build", poll)
             for _ in range(14)]
    assert waits[0] == daemon.POLL_INITIAL_SEC
    assert waits[-1] == daemon.POLL_MAX_SEC
    assert waits == sorted(waits), "the interval must grow, never shrink"


# ── the CLI commands ─────────────────────────────────────────────────────────

@pytest.fixture
def no_local_vault(monkeypatch):
    """Make any in-process index work fail loudly."""
    import delegation_core.vault as vault_module

    def explode(*a, **kw):
        raise AssertionError(
            "built a VaultManager while the daemon was up — that is the second "
            "ChromaDB writer and the second BGE copy this change removes")

    monkeypatch.setattr(vault_module, "VaultManager", explode)


@pytest.fixture
def daemon_up(monkeypatch):
    """A reachable daemon whose scripted tool results the test supplies."""
    calls = []

    def install(results):
        def fake_submit(cfg, tool, arguments=None, **kw):
            calls.append((tool, arguments))
            return results.pop(0)

        monkeypatch.setattr(daemon, "is_listening", lambda cfg, timeout=0.5: True)
        monkeypatch.setattr(daemon, "submit_and_wait", fake_submit)
        return calls

    return install


@pytest.fixture
def configured(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(Config, "load", staticmethod(lambda: cfg))
    return cfg


def test_reindex_under_a_live_daemon_never_opens_the_index(
        configured, daemon_up, no_local_vault, capsys):
    calls = daemon_up([{"status": "done", "result": 10121}])
    cli.cmd_reindex(_Args(force=True, local=False))
    assert calls == [("vault_reindex_bg", {"force": True})]
    assert "10121 notes indexed" in capsys.readouterr().out


def test_maintain_under_a_live_daemon_never_opens_the_index(
        configured, daemon_up, no_local_vault, capsys):
    calls = daemon_up([{"status": "done", "result": {"classified": ["a.md → Reference/"]}}])
    cli.cmd_maintain(_Args(local=False))
    assert calls == [("run_maintenance_bg", {})]
    # stdout stays parseable JSON — the hook logs it and a human reads it back.
    assert json.loads(capsys.readouterr().out) == {"classified": ["a.md → Reference/"]}


def test_ingest_sends_an_absolute_path(configured, daemon_up, no_local_vault, tmp_path,
                                       monkeypatch):
    """The daemon is a service with its own working directory: a relative path
    that resolves here would resolve somewhere else, or nowhere, there."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    calls = daemon_up([{"status": "done", "result": {"indexed": 2, "skipped": 0, "errors": []}}])
    cli.cmd_ingest(_Args(path="docs", no_recursive=False, local=False))
    tool, args = calls[0]
    assert tool == "ingest_folder_bg"
    assert args["source_path"] == str((tmp_path / "docs").resolve())
    assert args["recursive"] is True


def test_local_flag_bypasses_a_running_daemon(configured, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "is_listening", lambda cfg, timeout=0.5: True)
    monkeypatch.setattr(daemon, "submit_and_wait", lambda *a, **kw: pytest.fail(
        "--local still went to the daemon"))

    import delegation_core.vault as vault_module

    class FakeVault:
        def __init__(self, cfg): pass
        def reindex_vault(self, force=False): return 7

    monkeypatch.setattr(vault_module, "VaultManager", FakeVault)
    cli.cmd_reindex(_Args(force=False, local=True))
    assert "7 notes indexed" in capsys.readouterr().out


def test_without_a_daemon_the_work_still_happens_here(configured, monkeypatch, capsys):
    """A machine that never installed the service must still be able to reindex."""
    monkeypatch.setattr(daemon, "is_listening", lambda cfg, timeout=0.5: False)

    import delegation_core.vault as vault_module

    class FakeVault:
        def __init__(self, cfg): pass
        def reindex_vault(self, force=False): return 12

    monkeypatch.setattr(vault_module, "VaultManager", FakeVault)
    cli.cmd_reindex(_Args(force=False, local=False))
    out = capsys.readouterr().out
    assert "No daemon" in out and "12 notes indexed" in out


def test_a_daemon_that_fails_does_not_fall_back_to_a_second_writer(
        configured, monkeypatch, no_local_vault):
    """The tempting recovery is the bug: retrying locally starts the concurrent
    writer against an index a live daemon still holds open."""
    monkeypatch.setattr(daemon, "is_listening", lambda cfg, timeout=0.5: True)

    def boom(*a, **kw):
        raise daemon.DaemonCallFailed("vault_reindex_bg failed on the daemon: boom")

    monkeypatch.setattr(daemon, "submit_and_wait", boom)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_reindex(_Args(force=False, local=False))
    assert exc.value.code == 1


def test_a_daemon_that_disappears_mid_call_is_a_fallback_not_a_failure(
        configured, monkeypatch, capsys):
    """Restarting the service while a hook fires must not lose the reindex."""
    monkeypatch.setattr(daemon, "is_listening", lambda cfg, timeout=0.5: True)

    def gone(*a, **kw):
        raise daemon.DaemonUnavailable("daemon went away during vault_reindex_bg")

    monkeypatch.setattr(daemon, "submit_and_wait", gone)

    import delegation_core.vault as vault_module

    class FakeVault:
        def __init__(self, cfg): pass
        def reindex_vault(self, force=False): return 5

    monkeypatch.setattr(vault_module, "VaultManager", FakeVault)
    cli.cmd_reindex(_Args(force=False, local=False))
    assert "5 notes indexed" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["reindex", "maintain", "ingest"])
def test_every_index_writing_command_offers_the_local_escape_hatch(command):
    """Routing is the default, so each routed command needs a way to opt out.

    Checked through the real entry point rather than a parser built here: the
    parser lives inline in main(), so a flag added to a local ArgumentParser
    would prove nothing about what a user can actually type.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "from delegation_core.cli import main; main()",
         command, "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert "--local" in result.stdout, (
        f"`delegation-core {command}` routes to the daemon with no way to opt out")

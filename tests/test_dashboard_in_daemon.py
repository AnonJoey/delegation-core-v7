"""dashboard_api.serve_in_process() — the dashboard API served by the daemon.

The sidecar model built a VaultManager of its own, which is a resident copy of
BGE-m3 plus a second ChromaDB opener against the index the daemon already has
open. Measured on the dev machine: opening the dashboard took GPU use from 3826
to 6055 MiB, the extra 2314 MiB being that second copy. The daemon holds a warm
VaultManager already, so it serves these routes itself.

What matters here is not that the routes answer — test_dashboard_api_routes.py
covers the dispatch table — but that they answer *off the objects the caller
passed in*, and that nothing in this path constructs a VaultManager. The second
test is the one that fails if someone reintroduces the duplication.
"""

import http.client
import json
import socket
import threading

import pytest

from delegation_core import dashboard_api
from delegation_core.config import Config
from delegation_core.tracker import ProcessTracker

from .test_dashboard_api_routes import FakeVault


@pytest.fixture
def daemon_objects(monkeypatch, tmp_path):
    """The three objects a daemon would hand over, and a clean module state."""
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    cfg = Config(vault_path=str(vault_path), vault_folders=["reference"])
    # Restored by monkeypatch, so a test that sets the globals cannot leak them
    # into the next one.
    monkeypatch.setattr(dashboard_api, "_cfg", None)
    monkeypatch.setattr(dashboard_api, "_vault", None)
    monkeypatch.setattr(dashboard_api, "_tracker", None)
    return cfg, FakeVault(), ProcessTracker(tmp_path / "processes.json")


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    return res.status, body


def test_serves_routes_off_the_objects_it_was_given(daemon_objects):
    cfg, vault, tracker = daemon_objects
    server = dashboard_api.serve_in_process(cfg, vault, tracker, port=0)
    try:
        port = server.server_address[1]
        status, body = _get(port, "/api/vault/find?q=alpha&limit=3")
        assert status == 200
        # Answered by the very instance handed in, not one built here.
        assert vault.find_calls == [("alpha", 3)]
        assert body["results"][0]["title"] == "alpha"
        assert dashboard_api._vault is vault
        assert dashboard_api._cfg is cfg
        assert dashboard_api._tracker is tracker
    finally:
        server.shutdown()
        server.server_close()


def test_never_constructs_a_vault_manager(daemon_objects, monkeypatch):
    """The regression that matters: a second VaultManager is a second BGE.

    Constructing one is made an outright error, so the duplication cannot come
    back quietly — as a warm-up call, a "just to be safe" re-init, or a lazy
    import inside a handler.
    """
    import delegation_core.vault as vault_mod

    def explode(*a, **kw):
        raise AssertionError("serve_in_process built a VaultManager")

    monkeypatch.setattr(vault_mod, "VaultManager", explode)

    cfg, vault, tracker = daemon_objects
    server = dashboard_api.serve_in_process(cfg, vault, tracker, port=0)
    try:
        status, _ = _get(server.server_address[1], "/api/vault/tree")
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_returns_immediately_and_serves_on_a_background_thread(daemon_objects):
    """The daemon's main thread goes on to run the MCP transport, so this must
    not block it — a serve_forever() called inline would hang the daemon."""
    cfg, vault, tracker = daemon_objects
    before = {t.name for t in threading.enumerate()}
    server = dashboard_api.serve_in_process(cfg, vault, tracker, port=0)
    try:
        new = {t.name for t in threading.enumerate()} - before
        assert "dashboard-api" in new
        assert _get(server.server_address[1], "/api/vault/tree")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_bind_failure_propagates(daemon_objects):
    """A busy port usually means a second daemon. server.py catches OSError to
    keep MCP serving, but it has to be told — swallowing it here would leave a
    dashboard that silently answers from nowhere."""
    cfg, vault, tracker = daemon_objects
    squatter = socket.socket()
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(1)
    busy = squatter.getsockname()[1]
    try:
        with pytest.raises(OSError):
            dashboard_api.serve_in_process(cfg, vault, tracker, port=busy)
    finally:
        squatter.close()


def test_dashboard_port_defaults_and_round_trips(tmp_path, monkeypatch):
    """0 is the off switch the daemon checks; the default is a real port."""
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")

    assert Config().dashboard_port == 8788

    cfg = Config(vault_path=str(tmp_path), dashboard_port=0)
    cfg.save()
    assert Config.load().dashboard_port == 0

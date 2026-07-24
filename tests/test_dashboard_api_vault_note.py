"""dashboard_api.py's /api/vault/note path-containment check.

_handle_vault_note only reads _cfg.vault, not _vault (no BGE/ChromaDB needed),
so this is testable with a plain Config pointed at a tmp_path — no VaultManager
init required.

Regression test for a real bug found during code review (not from running
anything): the original check was `str(target).startswith(str(vault_root))`,
a plain string-prefix comparison. That's bypassable whenever a sibling
directory's name happens to start with the vault root's own directory name —
e.g. vault at tmp/vault, and tmp/vault-secrets exists: a path of
"../vault-secrets/x" resolves outside the vault, but the resolved string still
starts with the vault root's string, so the old check let it through. Fixed
with Path.relative_to(), which only succeeds for a genuine path-component
match. The identical bug (found via the same review) existed in server.py's
relink_folder tool too.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from delegation_core import dashboard_api
from delegation_core.config import Config


@pytest.fixture
def server(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(dashboard_api, "_cfg", Config(vault_path=str(vault)))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_api._Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port, vault, tmp_path
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    return res.status, body


def test_reads_a_real_note_inside_the_vault(server):
    port, vault, _ = server
    (vault / "note.md").write_text("hello", encoding="utf-8")
    status, body = _get(port, "/api/vault/note?path=note.md")
    assert status == 200
    assert body["content"] == "hello"


def test_rejects_traversal_into_a_sibling_dir_sharing_the_vault_names_prefix(server):
    """The exact bypass the old startswith() check missed: a sibling directory
    (tmp_path/vault-secrets) whose name starts with the vault dir's own name
    (tmp_path/vault)."""
    port, vault, tmp_path = server
    secrets = tmp_path / "vault-secrets"
    secrets.mkdir()
    (secrets / "leaked.md").write_text("should not be readable", encoding="utf-8")

    status, body = _get(port, "/api/vault/note?path=../vault-secrets/leaked.md")
    assert status == 400
    assert "error" in body


def test_rejects_plain_traversal_outside_vault(server):
    port, vault, tmp_path = server
    (tmp_path / "outside.md").write_text("nope", encoding="utf-8")
    status, body = _get(port, "/api/vault/note?path=../outside.md")
    assert status == 400


def test_missing_path_param_returns_400(server):
    port, _, _ = server
    status, body = _get(port, "/api/vault/note")
    assert status == 400


def test_nonexistent_note_inside_vault_returns_404(server):
    port, _, _ = server
    status, body = _get(port, "/api/vault/note?path=does-not-exist.md")
    assert status == 404

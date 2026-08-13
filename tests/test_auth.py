"""auth.py: the bearer token that replaced process ancestry as the trust boundary.

Under stdio the client spawned the server, so "who may call this" was answered by
the OS. Over HTTP it is a listening socket that every local process can reach —
including a web page in the user's browser, which is not hypothetical: v0.8.1
recorded exactly that hole in dashboard_api.py's unauthenticated port.

The property under test is therefore "fails closed", in every direction.
"""

import asyncio
import json

import pytest

from delegation_core.auth import LOCAL_CLIENT_ID, LocalTokenAuth
from delegation_core.config import Config


def _verify(provider, token):
    return asyncio.run(provider.verify_token(token))


def test_correct_token_is_accepted():
    provider = LocalTokenAuth("s3cret-token")
    granted = _verify(provider, "s3cret-token")
    assert granted is not None
    assert granted.client_id == LOCAL_CLIENT_ID


@pytest.mark.parametrize("presented", [
    "wrong-token",
    "",
    "s3cret",            # a prefix of the real token
    "s3cret-token-extra",  # the real token plus a suffix
    "S3CRET-TOKEN",      # case must matter
])
def test_bad_tokens_are_rejected(presented):
    assert _verify(LocalTokenAuth("s3cret-token"), presented) is None


def test_provider_without_a_token_rejects_everything():
    """An unconfigured provider must not degrade into an open server — the whole
    point of generating a token at startup rather than treating empty as 'off'."""
    provider = LocalTokenAuth("")
    assert _verify(provider, "anything") is None
    assert _verify(provider, "") is None


def test_no_oauth_routes_are_registered():
    """This is a loopback daemon with no authorization server behind it; the
    .well-known/OAuth surface would be dead weight and misleading."""
    provider = LocalTokenAuth("s3cret-token")
    assert provider.get_routes() == []
    assert provider.get_well_known_routes() == []


def test_ensure_server_token_generates_persists_and_is_stable(tmp_path, monkeypatch):
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")

    cfg = Config(vault_path=str(tmp_path))
    assert cfg.server_token == ""

    first = cfg.ensure_server_token()
    assert len(first) >= 32
    # Persisted, so a restart keeps the clients' configured token valid.
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["server_token"] == first
    # Idempotent: calling again must not rotate the secret out from under clients.
    assert cfg.ensure_server_token() == first


def test_saved_config_is_not_world_readable(tmp_path, monkeypatch):
    """config.json holds the token now, so its mode matters."""
    import os
    import stat
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")

    cfg = Config(vault_path=str(tmp_path))
    cfg.ensure_server_token()

    if os.name == "posix":
        mode = (tmp_path / "config.json").stat().st_mode
        assert not mode & stat.S_IRGRP
        assert not mode & stat.S_IROTH


def test_server_url_is_loopback_by_default():
    cfg = Config()
    assert cfg.server_url.startswith("http://127.0.0.1:")
    assert cfg.server_url.endswith("/mcp")

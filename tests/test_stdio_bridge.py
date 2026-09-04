"""stdio_bridge.py — a ponte que o Claude Desktop usa, com ZERO testes.

20 comandos, 0% de cobertura, escrita em 03/09/2026, no caminho que o Claude
Desktop percorre para falar com o daemon. O modulo raciocina bem sobre o proprio
proposito:

    "A bridge that starts up against nothing would present Desktop with a server
     that connects and then fails every call, which reads to the user as 'the
     tools are broken' rather than 'the service is down'."

Exatamente por isso ele recusa subir com o daemon fora do ar. Mas ha um segundo
jeito de cair no mesmo desfecho, e esse ele nao cobria.

## ACHADO 25 - a ponte conferia o daemon e nao a credencial

`Config.server_token` tem default `""`, e `ensure_server_token()` so gera um
"from the server's startup path". Uma config restaurada de backup, copiada entre
maquinas ou editada a mao chega aqui sem token, e a ponte monta
`Authorization: Bearer ` com nada depois.

MEDIDO contra o daemon desta maquina:
    header 'Bearer '        -> HTTP 401
    header 'Bearer errado'  -> HTTP 401
Ou seja: a ponte sobe, o Desktop conecta, e TODA chamada falha com 401 -- que e
palavra por palavra o desfecho que o docstring diz existir para evitar.

Nesta maquina o token tem 43 caracteres e nada disso acontece hoje. E latente,
e foi corrigido pelo mesmo criterio do downloader em 03/09: a guarda vizinha
existe, com o raciocinio ja escrito, e so nao foi estendida ao segundo caminho.
"""
from __future__ import annotations

import pytest

from delegation_core import stdio_bridge
from delegation_core.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(vault_path=str(tmp_path), server_token="um-token-de-verdade")


# ── recusa com o daemon fora do ar ──────────────────────────────────────────


def test_sem_daemon_recusa_e_nao_serve(cfg, monkeypatch, capsys):
    from delegation_core import service
    monkeypatch.setattr(service, "is_up", lambda *a, **k: False)
    monkeypatch.setattr(stdio_bridge, "build_proxy",
                        lambda c: pytest.fail("subiu a ponte sem daemon"))

    assert stdio_bridge.run(cfg) == 1
    assert "not answering" in capsys.readouterr().err


def test_a_recusa_diz_como_resolver(cfg, monkeypatch, capsys):
    """Sair com 1 calado deixa o usuario com "nao funciona" e nada mais."""
    from delegation_core import service
    monkeypatch.setattr(service, "is_up", lambda *a, **k: False)

    stdio_bridge.run(cfg)

    err = capsys.readouterr().err
    assert "delegation-core service install" in err
    assert "delegation-core status" in err


# ── e a credencial, que era a metade que faltava ────────────────────────────


def test_sem_token_recusa_em_vez_de_falhar_toda_chamada(tmp_path, monkeypatch, capsys):
    from delegation_core import service
    monkeypatch.setattr(service, "is_up", lambda *a, **k: True)
    monkeypatch.setattr(stdio_bridge, "build_proxy",
                        lambda c: pytest.fail("subiu a ponte sem token"))
    sem_token = Config(vault_path=str(tmp_path), server_token="")

    assert stdio_bridge.run(sem_token) == 1


def test_a_recusa_por_token_explica_o_401(tmp_path, monkeypatch, capsys):
    from delegation_core import service
    monkeypatch.setattr(service, "is_up", lambda *a, **k: True)
    # Sem este dublê, a primeira versao deste teste SUBIU um servidor MCP stdio
    # de verdade dentro da suite, porque a guarda que ele testa ainda nao
    # existia e o run() seguiu ate o build_proxy real.
    monkeypatch.setattr(stdio_bridge, "build_proxy",
                        lambda c: pytest.fail("subiu a ponte sem token"))
    sem_token = Config(vault_path=str(tmp_path), server_token="")

    stdio_bridge.run(sem_token)

    err = capsys.readouterr().err.lower()
    assert "token" in err
    assert "401" in err or "reject" in err or "recus" in err


def test_com_daemon_e_token_a_ponte_sobe(cfg, monkeypatch):
    from delegation_core import service
    subiu = []

    class _Proxy:
        def run(self, **kw):
            subiu.append(kw)

    monkeypatch.setattr(service, "is_up", lambda *a, **k: True)
    monkeypatch.setattr(stdio_bridge, "build_proxy", lambda c: _Proxy())

    assert stdio_bridge.run(cfg) == 0
    assert subiu == [{"show_banner": False}], (
        "numa ponte stdio todo byte do stdout e protocolo; o banner tem que sair"
    )


# ── o transporte ────────────────────────────────────────────────────────────


def test_o_token_vai_no_cabecalho_e_aponta_para_o_daemon(cfg, monkeypatch):
    capturado = {}

    class _Transporte:
        def __init__(self, url, headers=None):
            capturado["url"] = url
            capturado["headers"] = headers or {}

    monkeypatch.setitem(__import__("sys").modules, "fastmcp",
                        _modulo_fastmcp_falso(_Transporte))
    monkeypatch.setitem(__import__("sys").modules, "fastmcp.client.transports",
                        _modulo_transportes_falso(_Transporte))
    monkeypatch.setitem(__import__("sys").modules, "fastmcp.server",
                        _modulo_server_falso())

    stdio_bridge.build_proxy(cfg)

    assert capturado["url"] == cfg.server_url
    assert capturado["headers"]["Authorization"] == "Bearer um-token-de-verdade"


def _modulo_fastmcp_falso(transporte):
    import types
    m = types.ModuleType("fastmcp")
    m.Client = lambda t: ("cliente", t)
    return m


def _modulo_transportes_falso(transporte):
    import types
    m = types.ModuleType("fastmcp.client.transports")
    m.StreamableHttpTransport = transporte
    return m


def _modulo_server_falso():
    import types
    m = types.ModuleType("fastmcp.server")
    m.create_proxy = lambda cliente: ("proxy", cliente)
    return m

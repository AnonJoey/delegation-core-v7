"""A entrada do Claude Desktop nao pode carregar `url`, e o motivo nao e estetico.

Relatorio de campo, 03/09/2026: o Claude Desktop recusou a entrada
`delegation-core` com "As seguintes entradas em claude_desktop_config.json nao
sao configuracoes validas de servidor MCP e foram ignoradas".

Causa: `install_claude_desktop` escrevia `claude_code_entry(cfg)`, que e a
forma do Claude CODE, `{"type": "http", "url": ..., "headers": ...}`. Os dois
arquivos se parecem e validam diferente: `~/.claude.json` aceita `type: http`,
`claude_desktop_config.json` valida apenas stdio.

E o efeito nao para em "ignorada". Com um campo `url` presente, o Desktop
reescreve o arquivo na inicializacao e descarta a secao `mcpServers` INTEIRA
mais algumas chaves de `preferences`, sem erro. A entrada errada portanto nao
falha em conectar: ela pode apagar todos os outros servidores MCP que o usuario
configurou.

A correcao nao pode ser stdio ingenuo. `{"command": ..., "args": ["run"]}` sobe
um segundo daemon que disputa porta, indice e GPU com o primeiro, que e
exatamente o que a v0.11 eliminou. `mcp-stdio` e uma ponte que nao abre indice,
nao carrega modelo e repassa tudo para o unico daemon.

Verificado ao vivo antes destes testes: o Desktop, falando stdio com essa
entrada, enxergou as 54 ferramentas e uma chamada a `vault_stats` devolveu
`indexed_notes: 12947` do daemon, com um unico processo `delegation-core run`
na maquina.
"""
from __future__ import annotations

import json

import pytest

from delegation_core import clients
from delegation_core.config import Config


@pytest.fixture
def cfg():
    return Config(vault_path="/tmp/vault", server_token="tok", server_port=8787)


# ── a forma, que e o defeito ────────────────────────────────────────────────

def test_a_entrada_do_desktop_NAO_tem_url(cfg):
    """O gatilho da destruicao do arquivo. Este e o teste central."""
    entrada = clients.claude_desktop_entry(cfg)
    assert "url" not in entrada
    assert "serverUrl" not in entrada
    assert entrada.get("type") != "http"


def test_a_entrada_do_desktop_e_stdio(cfg):
    entrada = clients.claude_desktop_entry(cfg)
    assert "command" in entrada
    assert isinstance(entrada.get("args"), list)


def test_o_desktop_nao_recebe_a_entrada_do_code(cfg):
    """A confusao exata que causou o relatorio de campo."""
    assert clients.claude_desktop_entry(cfg) != clients.claude_code_entry(cfg)


def test_o_code_continua_recebendo_http(cfg):
    """A correcao do Desktop nao pode estragar o Code, que aceita http."""
    entrada = clients.claude_code_entry(cfg)
    assert entrada["type"] == "http"
    assert entrada["url"].endswith("/mcp")


def test_o_desktop_chama_a_ponte_e_nao_um_segundo_daemon(cfg):
    """`args: ["run"]` subiria outro FastMCP com seu proprio ChromaDB e seu
    proprio BGE na mesma GPU. A ponte nao abre nada."""
    entrada = clients.claude_desktop_entry(cfg)
    assert entrada["args"] == ["mcp-stdio"]
    assert "run" not in entrada["args"]


def test_o_token_nao_vai_para_o_arquivo_do_desktop(cfg):
    """Com a ponte, o token fica entre o processo e o loopback.

    A entrada HTTP antiga gravava `Authorization: Bearer <token>` dentro do
    claude_desktop_config.json, que e outro motivo para nao escrever aquilo ali.
    """
    texto = json.dumps(clients.claude_desktop_entry(cfg))
    assert "tok" not in texto
    assert "Authorization" not in texto


# ── a escrita, e o reparo que ela e ─────────────────────────────────────────

def _escrever(cfg, tmp_path, conteudo=None):
    alvo = tmp_path / "claude_desktop_config.json"
    if conteudo is not None:
        alvo.write_text(json.dumps(conteudo), encoding="utf-8")
    r = clients.install_claude_desktop(cfg, target_path=alvo)
    lido = json.loads(alvo.read_text(encoding="utf-8")) if alvo.exists() else {}
    return r, lido


def test_instalacao_nova_escreve_stdio(cfg, tmp_path):
    r, lido = _escrever(cfg, tmp_path)
    assert r["status"] == "installed"
    assert "url" not in lido["mcpServers"]["delegation-core"]


def test_substituir_a_entrada_perigosa_E_o_reparo(cfg, tmp_path):
    """Rodar `clients --claude-desktop` conserta um arquivo ja envenenado.

    Nao ha comando de limpeza separado de proposito: a operacao que instala
    corretamente e a mesma que remove o que estava errado, entao nao existe
    caminho em que o usuario conserte pela metade.
    """
    antigo = {
        "mcpServers": {
            "delegation-core": {"type": "http", "url": "http://127.0.0.1:8787/mcp",
                                "headers": {"Authorization": "Bearer segredo"}},
            "clickup": {"command": "npx", "args": ["clickup-mcp"]},
        },
        "preferences": {"sidebarMode": "epitaxy", "coworkWebSearchEnabled": True},
    }
    r, lido = _escrever(cfg, tmp_path, antigo)

    assert r["repaired_unsafe_url_entry"] is True
    assert "url" not in lido["mcpServers"]["delegation-core"]
    assert "segredo" not in json.dumps(lido), "o token ficou no arquivo"


def test_o_reparo_preserva_os_outros_servidores(cfg, tmp_path):
    """O Desktop apagaria mcpServers inteiro. O reparo nao pode imitar isso."""
    antigo = {
        "mcpServers": {
            "delegation-core": {"url": "http://127.0.0.1:8787/mcp"},
            "clickup": {"command": "npx", "args": ["clickup-mcp"]},
            "gmail": {"command": "uvx", "args": ["gmail-mcp"]},
        },
        "preferences": {"sidebarMode": "epitaxy"},
    }
    _, lido = _escrever(cfg, tmp_path, antigo)

    assert set(lido["mcpServers"]) == {"delegation-core", "clickup", "gmail"}
    assert lido["mcpServers"]["clickup"] == {"command": "npx", "args": ["clickup-mcp"]}
    assert lido["preferences"] == {"sidebarMode": "epitaxy"}


def test_instalacao_limpa_nao_reporta_reparo(cfg, tmp_path):
    _, _ = _escrever(cfg, tmp_path)
    r, _ = _escrever(cfg, tmp_path, {"mcpServers": {}})
    assert r["repaired_unsafe_url_entry"] is False


def test_rodar_duas_vezes_nao_muda_nada(cfg, tmp_path):
    alvo = tmp_path / "claude_desktop_config.json"
    clients.install_claude_desktop(cfg, target_path=alvo)
    r = clients.install_claude_desktop(cfg, target_path=alvo)
    assert r["status"] == "already-configured"


def test_json_invalido_nao_e_tocado(cfg, tmp_path):
    alvo = tmp_path / "claude_desktop_config.json"
    alvo.write_text("{nao e json", encoding="utf-8")
    r = clients.install_claude_desktop(cfg, target_path=alvo)
    assert r["status"] == "error"
    assert alvo.read_text(encoding="utf-8") == "{nao e json"


def test_faz_backup_antes_de_reescrever(cfg, tmp_path):
    alvo = tmp_path / "claude_desktop_config.json"
    alvo.write_text(json.dumps({"mcpServers": {"x": {"command": "y"}}}), encoding="utf-8")
    clients.install_claude_desktop(cfg, target_path=alvo)
    assert alvo.with_suffix(".json.dc-backup").exists()


# ── a guarda sistemica ──────────────────────────────────────────────────────

def test_nenhum_cliente_stdio_recebe_url(cfg):
    """A regra geral por tras do defeito.

    Cada cliente tem a sua funcao de entrada porque cada arquivo valida
    diferente. Esta guarda existe para que a proxima pessoa que acrescente um
    cliente nao reuse a entrada de outro sem conferir, que foi exatamente como
    o Desktop quebrou.
    """
    somente_stdio = {"claude_desktop_entry"}
    for nome in somente_stdio:
        entrada = getattr(clients, nome)(cfg)
        assert "url" not in entrada and "serverUrl" not in entrada, (
            f"{nome} devolveu uma entrada remota para um cliente que so aceita stdio"
        )


def test_a_ponte_existe_como_subcomando():
    """A entrada aponta para `mcp-stdio`; se o comando sumir, ela vira um erro
    de 'command not found' dentro do Desktop, onde ninguem olha."""
    from delegation_core import cli

    analisador = cli.build_parser() if hasattr(cli, "build_parser") else None
    if analisador is None:
        import inspect
        fonte = inspect.getsource(cli)
        assert '"mcp-stdio"' in fonte
        assert '"mcp-stdio": cmd_mcp_stdio' in fonte or "cmd_mcp_stdio" in fonte

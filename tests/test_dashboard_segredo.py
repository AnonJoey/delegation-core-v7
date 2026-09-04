"""O dashboard servia o bearer token do daemon sem autenticacao nenhuma.

MEDIDO contra esta maquina em 04/09/2026:

    GET http://127.0.0.1:8788/api/config      -> 200, sem credencial
    ...["config"]["server_token"]             -> 43 caracteres, o token inteiro

Esse e o token que autentica TODA chamada ao daemon na 8787. Com ele em maos,
qualquer um chama qualquer ferramenta MCP: ler o vault inteiro, escrever nota,
rodar manutencao, apagar linha do indice.

## O QUE JA ESTAVA CERTO, e foi conferido antes de mexer

**CORS.** Medido com `Origin: https://exemplo-qualquer.com`: a resposta NAO traz
`Access-Control-Allow-Origin`, entao o JS de uma pagina de outro site nao le o
corpo. O projeto ja tem `test_dashboard_api_cors.py` fixando isso, escrito com o
raciocinio certo.

**A escrita.** `/api/config/update` usa lista de PERMISSAO explicita, campo a
campo, e `server_token` nao esta nela.

## O QUE FALTAVA

`_handle_config_get` faz `asdict(_cfg)` e devolve a dataclass inteira. No mesmo
par de rotas, o lado que ESCREVE enumera o que aceita e o lado que LE despeja
tudo.

E ha uma fronteira de privilegio de verdade sendo cruzada. `Config.save()` faz
`chmod 0600` no arquivo, com o motivo escrito: "server_token lives in here, so
the file is a secret now". Mas o socket esta em 127.0.0.1, que QUALQUER usuario
local alcanca. Um segundo usuario da maquina nao le o arquivo 0600 e le o token
por HTTP.

Nada no `dashboard/` consome `server_token` dessa resposta: ele estava exposto
sem servir para nada.
"""
from __future__ import annotations

import dataclasses

import pytest

from delegation_core import dashboard_api
from delegation_core.config import Config

#: Nome de campo que nao pode sair inteiro por uma rota sem autenticacao.
_PARECE_SEGREDO = ("token", "secret", "password", "api_key", "apikey")

#: `max_tokens` casa com "token" pelo nome e e um numero de orcamento. A isencao
#: e por campo e com motivo, para um campo novo nao entrar de carona nela.
_NAO_E_SEGREDO = {"max_tokens", "embed_max_seq_length"}


def _campos_sensiveis() -> list[str]:
    return [f.name for f in dataclasses.fields(Config)
            if any(p in f.name.lower() for p in _PARECE_SEGREDO)
            and f.name not in _NAO_E_SEGREDO]


def test_a_varredura_de_campos_sensiveis_encontra_o_token():
    """Uma varredura quebrada aprovaria tudo em silencio."""
    assert "server_token" in _campos_sensiveis()


def test_nenhum_campo_de_segredo_sai_inteiro(tmp_path):
    cfg = Config(vault_path=str(tmp_path), server_token="segredo-de-verdade-43-chars")

    saida = dashboard_api._config_publico(cfg)

    for campo in _campos_sensiveis():
        assert saida.get(campo) != getattr(cfg, campo), (
            f"{campo} saiu inteiro por uma rota sem autenticacao"
        )


def test_o_token_e_redigido_e_nao_apagado(tmp_path):
    """Sumir com a chave faria o dashboard achar que nao ha token configurado.
    O que ele precisa saber e SE existe um, e nao qual e."""
    cfg = Config(vault_path=str(tmp_path), server_token="segredo-de-verdade")

    saida = dashboard_api._config_publico(cfg)

    assert "server_token" in saida
    assert "segredo" not in str(saida["server_token"])


def test_token_vazio_continua_distinguivel_de_token_presente(tmp_path):
    """"Nao ha token" e "ha um e nao te mostro" sao estados diferentes, e o
    stdio_bridge depende de saber qual e."""
    com = dashboard_api._config_publico(Config(vault_path=str(tmp_path), server_token="x"))
    sem = dashboard_api._config_publico(Config(vault_path=str(tmp_path), server_token=""))

    assert com["server_token"] != sem["server_token"]


def test_o_resto_da_config_continua_saindo(tmp_path):
    """Redigir nao pode virar esconder: o dashboard existe para mostrar isto."""
    cfg = Config(vault_path=str(tmp_path), engine_mode="agent", search_threshold=0.61)

    saida = dashboard_api._config_publico(cfg)

    assert saida["vault_path"] == str(tmp_path)
    assert saida["engine_mode"] == "agent"
    assert saida["search_threshold"] == 0.61
    assert len(saida) == len(dataclasses.fields(Config))


def test_a_rota_de_escrita_continua_com_lista_de_permissao():
    """A metade que ja estava certa. Se alguem trocar por um update generico,
    `server_token` volta a ser gravavel de fora."""
    import inspect
    fonte = inspect.getsource(dashboard_api._Handler._handle_config_update)
    assert "server_token" not in fonte
    assert fonte.count('in data:') >= 8, (
        "a lista de permissao campo a campo sumiu"
    )

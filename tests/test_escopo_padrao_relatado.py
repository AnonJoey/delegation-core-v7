"""`capabilities()` afirmava um escopo padrao fixo, e ele e adaptativo.

Tres fontes falam sobre o mesmo assunto, e duas estao erradas:

    capabilities()          "all": "everything (default)"
    docstring search_vault  "'notes' (hand-written only, THE DEFAULT)"
    _default_scope()        adaptativo: 'notes' quando artigo gerado passa de
                            metade do vault, 'all' quando nao passa, e uma
                            chave em config.json vence os dois

O codigo esta certo e o raciocinio dele esta escrito. O que esta errado e a
prosa, nos dois lugares, e ela se contradiz ate internamente: o proprio
docstring do `search_vault` diz "THE DEFAULT" num parenteses e explica a regra
adaptativa doze linhas abaixo.

MEDIDO neste vault: 8.246 artigos gerados de 8.593 notas, entao `_default_scope()`
resolve para `notes`, e toda chamada de `search_vault` sem escopo respondeu
`"scope": "notes"` a noite inteira.

CONSEQUENCIA: um agente que le `capabilities()` acredita que a busca sem escopo
cobre tudo. Aqui ela cobre as 347 notas escritas a mao e ignora os 8.246 artigos
gerados, sem dizer nada. E o mesmo defeito do `known_unwired` corrigido hoje: o
relatorio GERADO carregando uma afirmacao escrita a mao, sob um contrato que
manda preferi-lo "over any prose description of this server".

A correcao e a mesma: parar de afirmar e passar a CALCULAR.
"""
from __future__ import annotations

import json

import pytest

from delegation_core import capabilities


def _scopes(relatorio):
    return relatorio["search_scopes"]


def test_o_relatorio_nao_fixa_um_escopo_padrao_em_prosa():
    texto = json.dumps(capabilities.describe([{"name":"x","summary":"y"}]), ensure_ascii=False)
    assert "everything (default)" not in texto, (
        "o padrao e adaptativo; nomear um escopo fixo aqui faz um agente "
        "planejar em cima de algo que nao acontece"
    )


def test_o_relatorio_diz_qual_escopo_ESTA_valendo():
    r = capabilities.describe([{"name":"x","summary":"y"}])
    assert "default_scope" in r
    assert r["default_scope"]["resolved"] in ("notes", "all", "generated", "external")


def test_o_relatorio_explica_de_onde_o_padrao_veio():
    """Um agente que ve "notes" precisa saber se foi escolha do vault ou da
    config, porque so a segunda ele pode pedir ao usuario para mudar."""
    r = capabilities.describe([{"name":"x","summary":"y"}])
    assert r["default_scope"]["source"] in ("config", "vault-composition", "fallback")


def test_os_quatro_escopos_continuam_descritos():
    """Descrever menos nao e a correcao."""
    s = _scopes(capabilities.describe([{"name":"x","summary":"y"}]))
    assert set(s) == {"notes", "generated", "external", "all"}
    assert all(isinstance(v, str) and v for v in s.values())

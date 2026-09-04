"""O armazem de processos aceitava JSON valido do tipo errado e estourava.

Achado por injecao de falha. Tres armazens JSON neste projeto:

    jobs.py:71        return data if isinstance(data, dict) else {}
    localqueue.py:66  return data if isinstance(data, list) else []
    tracker.py        nenhuma checagem de tipo

O tracker trata "nao parseia" -- `[[[` cai no except e devolve `[]` -- e nao
trata "parseia com o tipo errado". MEDIDO com o arquivo contendo `{}`:

    ProcessTracker.create(...)  ->  AttributeError: 'dict' object has no
                                    attribute 'append'

E o `processes.json` desta maquina tem 129 KB do estado de processos que
atravessa sessoes, que e o mais valioso dos tres armazens.

De onde vem um arquivo assim: uma edicao a mao, um backup restaurado pela
metade, ou uma mudanca de formato futura. O desfecho e `process_create`
levantando AttributeError com uma mensagem que nao diz nada sobre o arquivo.
"""
from __future__ import annotations

import json

import pytest

from delegation_core.tracker import ProcessTracker


def test_um_objeto_onde_se_espera_lista_nao_estoura(tmp_path):
    loja = tmp_path / "processes.json"
    loja.write_text('{"nao": "e uma lista"}', encoding="utf-8")
    t = ProcessTracker(loja)

    proc = t.create("um processo", "descricao", "passo um, passo dois")

    assert proc["id"].startswith("proc_")
    assert len(t.list_processes(status="all")) == 1


@pytest.mark.parametrize("conteudo", ['{"a": 1}', '"uma string"', "42", "null", "true"])
def test_qualquer_json_valido_que_nao_seja_lista_e_descartado(tmp_path, conteudo):
    loja = tmp_path / "processes.json"
    loja.write_text(conteudo, encoding="utf-8")

    assert ProcessTracker(loja).list_processes(status="all") == []


def test_json_ilegivel_continua_sendo_tratado(tmp_path):
    """A metade que ja funcionava, pinada."""
    loja = tmp_path / "processes.json"
    loja.write_text("[[[", encoding="utf-8")

    assert ProcessTracker(loja).list_processes(status="all") == []


def test_uma_lista_de_verdade_continua_sendo_lida(tmp_path):
    """Descartar por tipo nao pode virar descartar tudo."""
    loja = tmp_path / "processes.json"
    t = ProcessTracker(loja)
    t.create("existente", "d", "")

    assert len(ProcessTracker(loja).list_processes(status="all")) == 1


def test_o_descarte_aparece_no_log(tmp_path, caplog):
    """Sobrescrever silenciosamente o estado de processos do usuario seria pior
    que estourar: pelo menos o AttributeError era visivel."""
    import logging

    loja = tmp_path / "processes.json"
    loja.write_text('{"processos": "que estavam aqui"}', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        ProcessTracker(loja).list_processes(status="all")

    assert any("list" in r.message.lower() or "lista" in r.message.lower()
               for r in caplog.records)

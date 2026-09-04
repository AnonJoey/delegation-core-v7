"""Dois handlers nunca testados que respondiam com um valor de aparencia segura.

Achados cruzando DUAS varreduras da noite: a de `except` que devolve valor
seguro (78 handlers) com a de cobertura (191 handlers nunca executados no
nucleo). A intersecao tem 18, e destes dois tem consequencia real.

## `ingest._load_registry` era o TERCEIRO armazem sem checagem de tipo

Ja corrigi o `tracker` hoje pelo mesmo motivo, comparando-o com `jobs` e
`localqueue`. Faltava contar este. MEDIDO:

    conteudo do arquivo   o que volta      o que os chamadores fazem
    ["uma","lista"]       list             TypeError: list indices must be integers
    "uma string"          str              TypeError: 'str' object does not support
    42                    int              TypeError: 'int' object does not support

Todo chamador faz `registry[caminho] = ...`. O `ingested_sources.json` desta
maquina tem 645 KB e 23 caminhos indexados.

## `repair._ja_tratada` mandava reescrever nota que nao conseguiu ler

    except OSError:
        return False        # "ainda nao tratada"

MEDIDO com uma nota sem permissao de leitura: devolve False, e o `repair`
entende que ela ainda precisa ser reescrita. Ou seja, ele passa por cima de um
arquivo que ele nem conseguiu abrir.

E a direcao errada da mesma regra que `_unindexed_notes` carrega no docstring:
degradar para "nao da para saber" e nunca para "esta tudo certo". Aqui o "tudo
certo" e destrutivo, entao a resposta segura e True: quem nao pode ser lido nao
pode ser reescrito.
"""
from __future__ import annotations

import json
import os

import pytest

from delegation_core import ingest, repair


# ── o registro de ingestao ──────────────────────────────────────────────────


@pytest.fixture
def registro(tmp_path, monkeypatch):
    alvo = tmp_path / "ingested_sources.json"
    monkeypatch.setattr(ingest, "_REGISTRY_FILE", alvo)
    return alvo


@pytest.mark.parametrize("conteudo", ['["uma","lista"]', '"uma string"', "42", "null", "true"])
def test_json_valido_do_tipo_errado_vira_registro_vazio(registro, conteudo):
    registro.write_text(conteudo, encoding="utf-8")

    r = ingest._load_registry()

    assert isinstance(r, dict)
    r["/um/caminho"] = {"indexed_count": 1}   # o que todo chamador faz


def test_json_ilegivel_continua_tratado(registro):
    """A metade que ja funcionava."""
    registro.write_text("{ nao e json", encoding="utf-8")
    assert ingest._load_registry() == {}


def test_arquivo_ausente_nao_e_erro(registro):
    assert ingest._load_registry() == {}


def test_um_registro_de_verdade_continua_sendo_lido(registro):
    registro.write_text(json.dumps({"/um/caminho": {"indexed_count": 3}}), encoding="utf-8")
    assert ingest._load_registry()["/um/caminho"]["indexed_count"] == 3


def test_o_descarte_por_tipo_aparece_no_log(registro, caplog):
    """Descartar 23 caminhos indexados em silencio seria pior que estourar."""
    import logging

    registro.write_text('["lista"]', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        ingest._load_registry()

    assert any("dict" in r.message or "registry" in r.message.lower()
               for r in caplog.records)


# ── a guarda do reparo ──────────────────────────────────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="permissao POSIX")
def test_nota_ilegivel_nao_e_reescrita(tmp_path):
    """False significa "ainda nao tratada", e o repair reescreve por cima."""
    nota = tmp_path / "nota.md"
    nota.write_text("---\ntitle: x\n---\n\ncorpo\n", encoding="utf-8")
    os.chmod(nota, 0o000)
    try:
        assert repair._ja_tratada(nota) is True, (
            "quem nao pode ser lido nao pode ser reescrito"
        )
    finally:
        os.chmod(nota, 0o644)


def test_nota_ja_tratada_continua_sendo_reconhecida(tmp_path):
    from delegation_core.repair import MARCA

    nota = tmp_path / "nota.md"
    nota.write_text(f"---\ntitle: x\n---\n\n{MARCA}\nresto\n", encoding="utf-8")

    assert repair._ja_tratada(nota) is True


def test_nota_intocada_continua_elegivel(tmp_path):
    """A guarda nao pode virar "nunca reescreve nada"."""
    nota = tmp_path / "nota.md"
    nota.write_text("---\ntitle: x\n---\n\nresumo inventado\n", encoding="utf-8")

    assert repair._ja_tratada(nota) is False

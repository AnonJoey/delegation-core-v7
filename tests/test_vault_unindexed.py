"""Nota no disco e fora do indice tem que aparecer na saude do vault.

Contexto medido em 02/09/2026 neste vault: 8.581 notas no disco contra 8.576
paths distintos no indice. As cinco faltantes (tres extratos de Daily e duas
transcricoes de sessao) estavam invisiveis a search_vault, sem carimbo em
.chroma_index.json e sem UMA linha de log nomeando qualquer uma delas.

A causa e estrutural: `index_note()` devolve False quando a escrita nao entra,
e das suas 21 expressoes de chamada exatamente uma, `reindex_vault`, le esse
retorno. As outras vinte descartam. Alem disso um dos dois caminhos de falha
dentro de `index_note` nao logava nada, e o outro logava sem dizer qual nota.

Achar as cinco exigiu uma consulta sqlite escrita a mao contra o Chroma, que e
precisamente o que `vault_health_detail` existe para tornar desnecessario.
"""
from __future__ import annotations

import logging

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


class _ColecaoFalsa:
    """Indice em memoria com a forma que _paged_get espera."""

    def __init__(self, metadatas: list[dict]):
        self._metas = metadatas
        self.explode = False

    def get(self, limit=None, offset=0, **kw):
        if self.explode:
            raise RuntimeError("index unreadable")
        fatia = self._metas[offset: offset + (limit or len(self._metas))]
        return {"ids": [str(i) for i in range(len(fatia))], "metadatas": fatia}

    def count(self):
        return len(self._metas)


def _vault_com(tmp_path, arquivos: dict[str, str], indexados: list[dict]) -> VaultManager:
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Sessions", "Decisions"])
    for rel, texto in arquivos.items():
        destino = tmp_path / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
    vault = VaultManager(cfg)
    vault.collection = _ColecaoFalsa(indexados)
    vault._ensure_ready = lambda: None
    return vault


def test_nota_ausente_do_indice_e_listada(tmp_path):
    vault = _vault_com(
        tmp_path,
        {"Sessions/presente.md": "# a\n", "Sessions/sumida.md": "# b\n"},
        [{"path": "Sessions/presente.md"}],
    )
    notas = [{"stem": "presente", "folder": "Sessions", "rel": "Sessions/presente.md"},
             {"stem": "sumida", "folder": "Sessions", "rel": "Sessions/sumida.md"}]

    faltando = vault._unindexed_notes(notas)

    assert [n["rel"] for n in faltando] == ["Sessions/sumida.md"]


def test_vault_todo_indexado_nao_reporta_nada(tmp_path):
    vault = _vault_com(tmp_path, {"Sessions/a.md": "# a\n"},
                       [{"path": "Sessions/a.md"}])
    notas = [{"stem": "a", "folder": "Sessions", "rel": "Sessions/a.md"}]
    assert vault._unindexed_notes(notas) == []


def test_arquivos_externos_nao_contam_como_notas_faltando(tmp_path):
    """Linhas externas usam caminho absoluto: compara-las com um walk do vault
    reportaria cada arquivo ingerido como ausente."""
    vault = _vault_com(
        tmp_path,
        {"Sessions/a.md": "# a\n"},
        [{"path": "Sessions/a.md"},
         {"path": "/fora/do/vault/doc.pdf", "is_external": "true"}],
    )
    notas = [{"stem": "a", "folder": "Sessions", "rel": "Sessions/a.md"}]
    assert vault._unindexed_notes(notas) == []


def test_linha_externa_nao_mascara_nota_do_vault_faltando(tmp_path):
    """O caso que o filtro de is_external realmente protege.

    O teste acima passa com ou sem o filtro: uma linha externa com caminho
    absoluto entra no conjunto de indexados e nenhuma nota do vault casa com
    ele, entao o resultado e vazio de qualquer jeito. Uma mutacao provou isso.

    O que o filtro impede e uma linha externa cujo `path` COINCIDE com o
    caminho relativo de uma nota do vault: sem o filtro ela entra no conjunto,
    a nota do vault passa a parecer indexada, e a unica nota realmente sumida
    da busca deixa de ser reportada. Falso negativo silencioso, na funcao
    escrita para acabar com falso negativo silencioso.
    """
    vault = _vault_com(
        tmp_path,
        {"Sessions/a.md": "# a\n"},
        [{"path": "Sessions/a.md", "is_external": "true"}],
    )
    notas = [{"stem": "a", "folder": "Sessions", "rel": "Sessions/a.md"}]

    faltando = vault._unindexed_notes(notas)

    assert [n["rel"] for n in faltando] == ["Sessions/a.md"]


def test_uma_nota_por_chunk_nao_vira_falso_positivo(tmp_path):
    """Desde a v0.12 uma nota ocupa uma linha por chunk, todas com o mesmo path."""
    vault = _vault_com(
        tmp_path, {"Sessions/longa.md": "# a\n"},
        [{"path": "Sessions/longa.md", "chunk": 0},
         {"path": "Sessions/longa.md", "chunk": 1},
         {"path": "Sessions/longa.md", "chunk": 2}],
    )
    notas = [{"stem": "longa", "folder": "Sessions", "rel": "Sessions/longa.md"}]
    assert vault._unindexed_notes(notas) == []


def test_indice_ilegivel_nao_reporta_saude_perfeita(tmp_path):
    """Degradar para 'nao da para saber', nunca para 'esta tudo certo'.

    Devolver [] aqui reportaria `unindexed: 0` para um indice que nao pode nem
    ser lido, que e exatamente o valor inventado que esta funcao existe para
    pegar.
    """
    vault = _vault_com(tmp_path, {"Sessions/a.md": "# a\n"}, [])
    vault.collection.explode = True
    notas = [{"stem": "a", "folder": "Sessions", "rel": "Sessions/a.md"}]

    resultado = vault._unindexed_notes(notas)

    assert len(resultado) == 1
    assert "index unreadable" in resultado[0]["error"]


def test_sem_colecao_nao_explode(tmp_path):
    vault = _vault_com(tmp_path, {"Sessions/a.md": "# a\n"}, [])
    vault.collection = None
    assert vault._unindexed_notes([{"stem": "a", "folder": "Sessions",
                                    "rel": "Sessions/a.md"}]) == []


# ── o barulho que index_note passou a fazer ─────────────────────────────────

def test_index_note_sem_colecao_loga_erro_com_o_caminho(tmp_path, caplog):
    """Este caminho era completamente mudo: nem uma linha de log."""
    cfg = Config(vault_path=str(tmp_path))
    vault = VaultManager(cfg)
    vault._ensure_ready = lambda: None
    vault.collection = None

    with caplog.at_level(logging.ERROR):
        ok = vault.index_note("conteudo", {"path": "Sessions/perdida.md"})

    assert ok is False
    assert "Sessions/perdida.md" in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_index_note_com_falha_de_escrita_nomeia_a_nota(tmp_path, caplog):
    """Antes: logger.warning("Index error: %s", e), sem dizer qual nota."""
    cfg = Config(vault_path=str(tmp_path))
    vault = VaultManager(cfg)
    vault._ensure_ready = lambda: None

    class _Explode:
        def get(self, **kw):
            raise RuntimeError("chromadb caiu")

        def upsert(self, **kw):
            raise RuntimeError("chromadb caiu")

        def delete(self, **kw):
            raise RuntimeError("chromadb caiu")

    vault.collection = _Explode()

    with caplog.at_level(logging.ERROR):
        ok = vault.index_note("conteudo", {"path": "Sessions/perdida.md"})

    assert ok is False
    assert "Sessions/perdida.md" in caplog.text


# ── o detalhe nao pode perder campo novo ────────────────────────────────────

def test_health_detail_expoe_qualquer_campo_novo(tmp_path, monkeypatch):
    """A lista de chaves era fixa: um campo novo era computado e descartado."""
    cfg = Config(vault_path=str(tmp_path))
    vault = VaultManager(cfg)

    monkeypatch.setattr(vault, "_force_health_recompute", lambda: None)
    vault._last_health_detail = {
        "_vault": str(tmp_path),
        "total_notes": 3,
        "unindexed": 2,
        "unindexed_items": [{"rel": "a.md"}, {"rel": "b.md"}],
        "um_campo_que_ainda_nao_existe": 7,
        "outros_items": [{"x": 1}] * 80,
    }

    detalhe = vault.health_detail(limit=10)

    assert detalhe["unindexed"] == 2
    assert detalhe["unindexed_items_total"] == 2
    assert detalhe["um_campo_que_ainda_nao_existe"] == 7
    assert len(detalhe["outros_items"]) == 10
    assert detalhe["outros_items_total"] == 80
    assert "_vault" not in detalhe

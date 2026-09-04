"""Falha de contagem nao pode virar numero cacheado.

`cached_word_count` guarda o resultado contra (size, mtime_ns) para nao reabrir
todo PDF a cada varredura (#1656). `count_words` devolvia 0 em QUALQUER
excecao -- ImportError da pypdf/python-docx/openpyxl, PDF corrompido, arquivo em
uso -- e 0 e indistinguivel de um arquivo realmente vazio. O cache entao gravava
a falha numa chave que so muda se alguem editar o arquivo, entao o erro
transitorio virava permanente.

O docstring de `cached_word_count` raciocina sobre a falha do STAT ("a file that
can't be stat'd simply recomputes and isn't cached -- correct, just not
accelerated") e nao sobre a falha do COMPUTE, que e a que persiste.
"""
from __future__ import annotations

from pathlib import Path

from delegation_core.graph import cache as _cache
from delegation_core.graph.detect import count_words


def test_falha_na_contagem_nao_e_cacheada(tmp_path: Path):
    doc = tmp_path / "relatorio.pdf"
    doc.write_bytes(b"%PDF-1.4 conteudo real")

    chamadas = {"n": 0}

    def falha(_p):
        chamadas["n"] += 1
        return None          # count_words nao conseguiu contar

    def sucesso(_p):
        chamadas["n"] += 1
        return 4200

    assert _cache.cached_word_count(doc, tmp_path, falha, cache_root=tmp_path) == 0
    # arquivo NAO tocado: mesmos size e mtime_ns
    assert _cache.cached_word_count(doc, tmp_path, sucesso, cache_root=tmp_path) == 4200
    assert chamadas["n"] == 2, "a segunda passagem tem que recomputar, nao ler o cache"


def test_zero_legitimo_continua_sendo_cacheado(tmp_path: Path):
    """A correcao nao pode desligar o cache para arquivo vazio de verdade, que e
    o caso que #1656 existe para acelerar."""
    vazio = tmp_path / "vazio.txt"
    vazio.write_text("")

    chamadas = {"n": 0}

    def conta_zero(_p):
        chamadas["n"] += 1
        return 0

    assert _cache.cached_word_count(vazio, tmp_path, conta_zero, cache_root=tmp_path) == 0
    assert _cache.cached_word_count(vazio, tmp_path, conta_zero, cache_root=tmp_path) == 0
    assert chamadas["n"] == 1, "zero legitimo tem que vir do cache na segunda vez"


def test_contagem_boa_continua_cacheada(tmp_path: Path):
    arq = tmp_path / "texto.txt"
    arq.write_text("uma duas tres")
    chamadas = {"n": 0}

    def conta(_p):
        chamadas["n"] += 1
        return 3

    assert _cache.cached_word_count(arq, tmp_path, conta, cache_root=tmp_path) == 3
    assert _cache.cached_word_count(arq, tmp_path, conta, cache_root=tmp_path) == 3
    assert chamadas["n"] == 1


def test_count_words_devolve_none_quando_falha(tmp_path: Path, monkeypatch):
    """A ponta que produz o None: uma biblioteca ausente e um ImportError, que e
    Exception, e era engolido como 0."""
    import delegation_core.graph.detect as detect

    def sem_biblioteca(_p):
        raise ImportError("No module named 'pypdf'")

    monkeypatch.setattr(detect, "extract_pdf_text", sem_biblioteca)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert count_words(pdf) is None


def test_count_words_conta_texto_normal(tmp_path: Path):
    arq = tmp_path / "a.txt"
    arq.write_text("uma duas tres quatro")
    assert count_words(arq) == 4


def test_count_words_devolve_zero_para_arquivo_vazio(tmp_path: Path):
    """Zero de verdade continua sendo zero, e nao None."""
    arq = tmp_path / "vazio.txt"
    arq.write_text("")
    assert count_words(arq) == 0

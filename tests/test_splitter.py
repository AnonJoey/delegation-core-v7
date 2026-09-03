"""Dividir uma nota longa nao pode perder texto, e nao havia teste nenhum.

`splitter.py` pega um arquivo grande do inbox e devolve varias secoes, que o
organizer transforma em varias notas. Errar aqui e destrutivo do mesmo jeito
que o merger: o arquivo original vai para `_processed/` e o que ficou no vault
e o que o splitter devolveu. Texto que ele nao devolver esta perdido para a
busca.

Medido em 03/09/2026: `splitter.py` era um dos nove modulos do nucleo que
nenhum arquivo de teste importava.

O teste central deste arquivo e o de invariante: **todo paragrafo da entrada
tem que aparecer em alguma secao da saida**, nos tres caminhos (cabecalho,
paragrafo, pagina de PDF). Os comentarios do proprio modulo dizem que os tres
preservam conteudo; aqui isso passa a ser verificado.
"""
from __future__ import annotations

import re

import pytest

from delegation_core import splitter
from delegation_core.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(vault_path=str(tmp_path), split_min_chars=3000, split_max_notes=10)


def _paragrafos(texto: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]


def _nada_se_perdeu(entrada: str, secoes: list[tuple[str, str]]) -> None:
    """Todo paragrafo da entrada aparece em alguma secao. O invariante."""
    junto = "\n\n".join(c for _, c in secoes)
    faltando = [p for p in _paragrafos(entrada) if p not in junto]
    assert not faltando, (
        f"{len(faltando)} paragrafo(s) sumiram na divisao. Primeiro: "
        f"{faltando[0][:120]!r}"
    )


# ── divisao por cabecalho ───────────────────────────────────────────────────

def test_menos_de_dois_cabecalhos_nao_divide():
    assert splitter._split_by_headings("# So um\n\ncorpo\n") == []
    assert splitter._split_by_headings("sem cabecalho nenhum") == []


def test_preambulo_vira_Introduction():
    texto = "texto antes de tudo\n\n# Um\n\na\n\n# Dois\n\nb\n"
    secoes = splitter._split_by_headings(texto)
    assert secoes[0][0] == "Introduction"
    assert "texto antes de tudo" in secoes[0][1]
    assert [t for t, _ in secoes[1:]] == ["Um", "Dois"]


def test_sem_preambulo_nao_inventa_secao():
    secoes = splitter._split_by_headings("# Um\n\na\n\n# Dois\n\nb\n")
    assert [t for t, _ in secoes] == ["Um", "Dois"]


def test_h1_e_h2_contam_h3_nao():
    texto = "# Um\n\na\n\n## Dois\n\nb\n\n### Tres\n\nc\n"
    assert [t for t, _ in splitter._split_by_headings(texto)] == ["Um", "Dois"]


def test_divisao_por_cabecalho_preserva_tudo():
    texto = "antes\n\n# Um\n\ncorpo um\n\n## Dois\n\ncorpo dois\n\n# Tres\n\ncorpo tres\n"
    _nada_se_perdeu(texto, splitter._split_by_headings(texto))


# ── paginas de PDF ──────────────────────────────────────────────────────────

def test_paginas_agrupadas_respeitam_o_teto():
    paginas = [f"pagina {i}" for i in range(1, 26)]
    secoes = splitter._split_pdf_pages(paginas, max_notes=10)
    assert len(secoes) <= 10


def test_titulo_de_pagina_unica_nao_vira_intervalo():
    secoes = splitter._split_pdf_pages(["a", "b", "c"], max_notes=3)
    assert [t for t, _ in secoes] == ["Page 1", "Page 2", "Page 3"]


def test_titulo_de_intervalo_mostra_inicio_e_fim():
    secoes = splitter._split_pdf_pages([f"p{i}" for i in range(6)], max_notes=3)
    assert secoes[0][0] == "Pages 1–2"


def test_divisao_por_pagina_preserva_todas_as_paginas():
    paginas = [f"conteudo exclusivo da pagina {i}" for i in range(1, 24)]
    secoes = splitter._split_pdf_pages(paginas, max_notes=10)
    junto = "\n\n".join(c for _, c in secoes)
    for p in paginas:
        assert p in junto, f"pagina perdida: {p!r}"


# ── divisao por paragrafo ───────────────────────────────────────────────────

def test_um_paragrafo_so_nao_divide():
    assert splitter._paragraph_chunks("um paragrafo unico", 100, 10) == []


def test_texto_que_cabe_num_pedaco_nao_divide():
    assert splitter._paragraph_chunks("a\n\nb\n\nc", 10_000, 10) == []


def test_divisao_por_paragrafo_respeita_o_teto_de_secoes():
    texto = "\n\n".join(f"paragrafo numero {i} " + "x" * 200 for i in range(50))
    secoes = splitter._paragraph_chunks(texto, 300, 5)
    assert 0 < len(secoes) <= 5


def test_divisao_por_paragrafo_preserva_tudo():
    texto = "\n\n".join(f"paragrafo exclusivo {i} " + "y" * 200 for i in range(40))
    _nada_se_perdeu(texto, splitter._paragraph_chunks(texto, 300, 5))


def test_o_excedente_vai_para_o_ultimo_pedaco_e_nao_para_o_lixo():
    """Quando o orcamento de secoes acaba, o resto empilha no ultimo.

    E a mesma politica do caminho de cabecalho, e existe para nao descartar
    conteudo em silencio.
    """
    texto = "\n\n".join(f"p{i} " + "z" * 400 for i in range(30))
    secoes = splitter._paragraph_chunks(texto, 500, 3)
    assert len(secoes) == 3
    _nada_se_perdeu(texto, secoes)
    assert len(secoes[-1][1]) > len(secoes[0][1]), "o ultimo devia ter acumulado"


# ── a decisao ───────────────────────────────────────────────────────────────

def test_abaixo_do_limiar_nao_divide(cfg, tmp_path):
    src = tmp_path / "curto.md"
    src.write_text("# Um\n\na\n\n# Dois\n\nb\n", encoding="utf-8")
    assert splitter.should_split("# Um\n\na\n\n# Dois\n\nb\n", src, cfg) == []


def test_acima_do_limiar_com_cabecalho_divide_por_cabecalho(cfg, tmp_path):
    texto = "# Um\n\n" + "a" * 2000 + "\n\n# Dois\n\n" + "b" * 2000
    src = tmp_path / "longo.md"
    src.write_text(texto, encoding="utf-8")

    secoes = splitter.should_split(texto, src, cfg)

    assert [t for t, _ in secoes] == ["Um", "Dois"]
    _nada_se_perdeu(texto, secoes)


def test_acima_do_limiar_sem_cabecalho_cai_em_paragrafo(cfg, tmp_path):
    texto = "\n\n".join("paragrafo " + "c" * 400 for _ in range(20))
    src = tmp_path / "longo.txt"
    src.write_text(texto, encoding="utf-8")

    secoes = splitter.should_split(texto, src, cfg)

    assert len(secoes) > 1
    assert all(t.startswith("Section ") for t, _ in secoes)
    _nada_se_perdeu(texto, secoes)


def test_extensao_desconhecida_nao_usa_cabecalho_mas_usa_paragrafo(cfg, tmp_path):
    """Um .docx extraido nao passa pelo caminho de cabecalho, e ainda assim
    nao pode voltar inteiro se estourou o limiar."""
    texto = "# Parece cabecalho\n\n" + "\n\n".join("p " + "d" * 400 for _ in range(20))
    src = tmp_path / "doc.docx"
    src.write_text("", encoding="utf-8")

    secoes = splitter.should_split(texto, src, cfg)

    assert all(t.startswith("Section ") for t, _ in secoes)
    _nada_se_perdeu(texto, secoes)


def test_excedente_de_cabecalhos_e_fundido_e_nao_descartado(cfg, tmp_path):
    """O caminho que o proprio comentario do modulo diz preservar conteudo."""
    texto = "".join(f"# Secao {i}\n\n" + "e" * 300 + f" marca{i}\n\n" for i in range(25))
    src = tmp_path / "muitos.md"
    src.write_text(texto, encoding="utf-8")

    secoes = splitter.should_split(texto, src, cfg)

    assert len(secoes) == cfg.split_max_notes
    _nada_se_perdeu(texto, secoes)
    # Toda marca tem que sobreviver, inclusive as das secoes que excederam.
    junto = "\n\n".join(c for _, c in secoes)
    for i in range(25):
        assert f"marca{i}" in junto, f"secao {i} foi descartada"

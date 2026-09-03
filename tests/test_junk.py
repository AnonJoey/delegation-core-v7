"""O filtro de boilerplate descartava notas reais do inbox.

`junk.is_junk` roda ANTES do classificador: o que ele marca vai para
`_processed/` e nunca vira nota. Um falso positivo aqui nao devolve resultado
errado, joga fora o que a pessoa escreveu.

Medido em 03/09/2026: o sufixo do padrao era `[._-].*`, ou seja qualquer frase
depois de um hifen. Dez nomes que um usuario deste vault escreveria eram
descartados como boilerplate, entre eles `todo-lista-do-projeto.md`,
`install-do-cliente.md` e `notice-de-reuniao.md`.

`junk.py` era um dos nove modulos do nucleo sem nenhum teste.
"""
from __future__ import annotations

import pytest

from delegation_core.junk import is_junk


# ── o que TEM que ser descartado ────────────────────────────────────────────

@pytest.mark.parametrize("nome", [
    "LICENSE", "LICENSE.txt", "licence", "COPYING", "NOTICE",
    "README.md", "readme.rst", "CHANGELOG.md", "CHANGES",
    "CONTRIBUTING.md", "CONTRIBUTORS", "AUTHORS",
    "requirements.txt", "requirements-dev.txt", "requirements_test.txt",
    "Makefile", "Dockerfile", ".gitignore", "CODEOWNERS",
    "LICENSE-MIT", "CHANGELOG-2024.md", "install.sh", "TODO.md",
])
def test_boilerplate_continua_sendo_descartado(nome):
    assert is_junk(nome) is not None, f"{nome} deixou de ser filtrado"


def test_arquivo_de_lock_do_office():
    assert "Office lock file" in is_junk("~$relatorio.docx")


def test_texto_de_licenca_no_conteudo():
    mit = "Permission is hereby granted, free of charge, to any person obtaining"
    assert is_junk("qualquer.txt", mit) is not None


# ── o que NAO pode ser descartado ───────────────────────────────────────────

@pytest.mark.parametrize("nome", [
    "todo-lista-do-projeto.md",
    "changes-que-precisamos-fazer.md",
    "install-do-cliente.md",
    "version-final-do-contrato.md",
    "authors-do-artigo.md",
    "notice-de-reuniao.md",
    "help-para-o-abner.md",
    "manifest-de-entrega.md",
    "license-do-cliente-gazin.md",
    "readme-que-escrevi-para-o-time.md",
])
def test_nota_real_com_prefixo_parecido_nao_e_descartada(nome):
    """O defeito. Todos estes sumiam do inbox sem virar nota."""
    assert is_junk(nome) is None, f"{nome} foi descartado como boilerplate"


@pytest.mark.parametrize("nome", [
    "reuniao-com-o-max.md", "notas.md", "2026-09-03-decisao.md",
    "relatorio-mensal.pdf", "planilha-de-horas.xlsx",
])
def test_nome_comum_de_nota_passa(nome):
    assert is_junk(nome) is None


def test_sem_conteudo_nao_levanta():
    assert is_junk("qualquer.md") is None
    assert is_junk("qualquer.md", "") is None


# ── o vies declarado ────────────────────────────────────────────────────────

def test_o_aperto_deixa_passar_variante_rara_de_boilerplate():
    """A troca e deliberada e esta documentada no modulo.

    Um `requirements-dev-test.txt` agora escapa e vira nota. Arquivar um
    boilerplate a mais custa uma nota inutil; descartar a nota de alguem custa
    a nota. Este teste existe para que a troca seja uma decisao visivel e nao
    uma surpresa para quem vier depois.
    """
    assert is_junk("requirements-dev-test.txt") is None


def test_o_motivo_devolvido_nomeia_o_que_casou():
    """Quem le o relatorio de maintenance precisa saber POR QUE foi pulado."""
    motivo = is_junk("README.md")
    assert "readme" in motivo.lower()


# ── dotfiles: seis entradas do padrao eram inalcancaveis ────────────────────

@pytest.mark.parametrize("nome", [
    ".gitignore", ".gitattributes", ".editorconfig", ".pylintrc", ".flake8",
])
def test_dotfile_de_configuracao_e_descartado(nome):
    """`Path(".gitignore").stem` e ".gitignore", COM o ponto.

    O padrao lista `gitignore`, `gitattributes`, `editorconfig`, `pylintrc`,
    `flake8` e `mypy`, que sao todas convencionalmente dotfiles, e nenhuma
    dessas seis entradas podia casar. Estavam ali como intencao. E o organizer
    nao pula arquivo oculto, entao um `.gitignore` no inbox virava nota.
    """
    assert is_junk(nome) is not None, f"{nome} vira nota no vault"


def test_o_ponto_nao_transforma_nota_real_em_lixo():
    """Tirar o ponto nao pode passar a descartar o que nao e boilerplate."""
    assert is_junk(".notas-da-reuniao.md") is None
    assert is_junk(".uma-nota-oculta.md") is None

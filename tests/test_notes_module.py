"""notes.py saiu de vault.py, e a reexportacao nao pode sumir sem alguem notar.

vault.py tinha 2020 linhas fazendo duas coisas sem relacao: o ciclo de vida do
indice vetorial, e as regras de como uma nota se chama e como seu frontmatter e
montado. As segundas sao funcoes puras, sem ChromaDB, sem GPU e sem disco, e
sao as que o resto do projeto mais importa.

O corte foi onde as dependencias mudam: notes.py depende so da biblioteca
padrao mais linker.frontmatter_aliases, e nao importa chromadb, embeddings nem
gpu. Por isso o bloco saiu inteiro sem alterar uma linha do que ficou.

Nove modulos fazem `from .vault import safe_filename` e afins. A reexportacao
existe para nao quebra-los, e este arquivo e o que impede alguem de "limpar" a
reexportacao achando que ninguem usa.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src" / "delegation_core"

#: O que vault.py precisa continuar oferecendo. Cada nome tem chamador real.
REEXPORTADOS = [
    "compose_note",
    "client_from_path",
    "client_slug",
    "link_names_for_stem",
    "resolve_in_vault",
    "resolve_vault_folder",
    "safe_filename",
    "unique_note_path",
    "yaml_quote_scalar",
    "yaml_unquote_scalar",
]


@pytest.mark.parametrize("nome", REEXPORTADOS)
def test_vault_ainda_exporta(nome):
    from delegation_core import notes, vault

    assert hasattr(vault, nome), (
        f"`from .vault import {nome}` quebrou. Nove modulos importam daqui."
    )
    assert getattr(vault, nome) is getattr(notes, nome), (
        f"{nome} em vault nao e o mesmo objeto de notes: virou uma segunda copia"
    )


def test_notes_nao_conhece_o_indice():
    """O corte so vale enquanto notes.py nao arrastar o indice de volta."""
    arvore = ast.parse((SRC / "notes.py").read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom):
            importados.add((no.module or "").split(".")[0])

    proibidos = {"chromadb", "embeddings", "gpu", "vault", "torch",
                 "sentence_transformers"}
    assert not (importados & proibidos), (
        f"notes.py voltou a depender do indice: {importados & proibidos}"
    )


def test_notes_nao_importa_vault_de_volta():
    """Reexportacao em um sentido so: o inverso e ciclo de import.

    Conferido por AST e nao por substring. A primeira versao procurava
    "from .vault" no texto e falhava na propria docstring do modulo, que cita
    `from .vault import safe_filename` para explicar por que a reexportacao
    existe. Um teste que le prosa como se fosse codigo falha pelo motivo
    errado, e o proximo a ver isso perde tempo procurando um import que nunca
    houve.
    """
    arvore = ast.parse((SRC / "notes.py").read_text(encoding="utf-8"))
    alvos = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom):
            alvos.add(no.module or "")
        elif isinstance(no, ast.Import):
            alvos.update(a.name for a in no.names)
    assert not {a for a in alvos if a.endswith("vault")}, (
        f"notes.py importa vault de volta: {alvos}"
    )


def test_o_modulo_de_indice_encolheu_de_verdade():
    """Um 'refactor' que so move linhas para o lado nao vale o risco.

    Numeros do dia: vault.py de 2020 para cerca de 1660 linhas, com 409 em
    notes.py. Testado por limite superior e nao por igualdade, para nao falhar
    a cada linha escrita.
    """
    vault_linhas = len((SRC / "vault.py").read_text(encoding="utf-8").split("\n"))
    notes_linhas = len((SRC / "notes.py").read_text(encoding="utf-8").split("\n"))

    assert vault_linhas < 1800, f"vault.py voltou a crescer: {vault_linhas} linhas"
    assert notes_linhas > 300, f"notes.py encolheu demais: {notes_linhas} linhas"


def test_as_funcoes_puras_rodam_sem_config_nem_indice():
    """A razao de existirem separadas: dao para testar sem montar nada."""
    from delegation_core.notes import (
        compose_note, link_names_for_stem, safe_filename, yaml_quote_scalar,
    )

    assert safe_filename("um titulo") == "um titulo"
    assert yaml_quote_scalar('com "aspas"') .startswith('"')
    assert "2026-01-01-x" in link_names_for_stem("2026-01-01-x")
    assert compose_note("t", "corpo\n", "2026-01-01").startswith("---\n")

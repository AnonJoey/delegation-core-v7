"""`resolve_in_vault` promete None-ou-caminho e devolvia uma terceira coisa.

O docstring e explicito: "Resolve *rel_path* under *vault_root*, or None if it
escapes". Quem chama trata dois casos. Achado por teste de propriedade sobre
`st.text()`: um `\\x00` no caminho faz `Path.resolve()` levantar ValueError, e o
chamador recebe uma excecao onde o contrato prometia `None`.

MEDIDO ponta a ponta contra o dashboard em execucao, que passa `dir` da query
string direto para `list_notes_in`:

    dir=Procedures        ->  HTTP 200
    dir=../../etc         ->  HTTP 400  {"error": "Path outside the vault: ../../etc"}
    dir=Proc%00edures     ->  HTTP 500  {"error": "lstat: embedded null character in path"}

A MESMA pergunta -- este caminho serve? -- com duas respostas, e a segunda e um
500 que vaza detalhe de implementacao. Um caminho com byte nulo nao e mais
utilizavel que um `../../etc`, e merece a mesma resposta limpa.

Quatro chamadores dependem do contrato, e dois deles recebem o caminho de fora:
`vault.list_notes_in` (dashboard) e `vault.note_links` / `notewriter.save_note`
(ferramentas MCP). Uma string JSON pode conter `\\u0000`, entao a entrada e
alcancavel por qualquer cliente MCP.
"""
from __future__ import annotations

import pytest

from delegation_core.notes import resolve_in_vault


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Procedures").mkdir(parents=True)
    return v


@pytest.mark.parametrize("rel", [
    "a\x00b",
    "\x00",
    "Procedures/nota\x00.md",
])
def test_byte_nulo_devolve_none_e_nao_levanta(vault, rel):
    assert resolve_in_vault(vault, rel) is None


def test_o_caminho_que_escapa_continua_devolvendo_none(vault):
    """A metade que ja funcionava, pinada."""
    assert resolve_in_vault(vault, "../../etc") is None


def test_o_caminho_de_verdade_continua_resolvendo(vault):
    alvo = resolve_in_vault(vault, "Procedures")
    assert alvo is not None
    assert alvo == (vault / "Procedures").resolve()


def test_uma_string_vazia_nao_levanta(vault):
    """`vault / ""` e o proprio vault: resolve, e nao escapa."""
    assert resolve_in_vault(vault, "") == vault.resolve()


def test_um_erro_do_sistema_operacional_tambem_devolve_none(vault, monkeypatch):
    """O contrato vale para OSError, e nao so para ValueError.

    HONESTIDADE SOBRE O QUE ESTE TESTE PROVA. A primeira versao mandava um
    componente de 5.000 caracteres e afirmava `is not None or True`, que e
    tautologia: passaria com qualquer coisa. Medido depois, nesta plataforma,
    `Path.resolve()` NAO levanta OSError para componente longo nem para caminho
    total longo, so ValueError para o byte nulo. A mutacao que remove OSError da
    captura sobreviveu, e foi ela que expos a tautologia.

    A captura fica: o Windows e plataforma suportada e `resolve()` levanta
    OSError la para caracteres que o sistema recusa, e um caminho que o SO se
    nega a olhar e, por qualquer leitura, "nao utilizavel neste vault". O que
    muda e a honestidade do teste: ele FORCA o OSError em vez de fingir que o
    reproduziu aqui.
    """
    from pathlib import Path

    def _recusa(self, *a, **k):
        raise OSError(36, "File name too long")

    monkeypatch.setattr(Path, "resolve", _recusa)

    assert resolve_in_vault(vault, "qualquer") is None


def test_a_lista_de_notas_responde_erro_limpo_para_byte_nulo(tmp_path):
    """O caminho que o dashboard percorre: erro de negocio, nao excecao."""
    from delegation_core.config import Config
    from delegation_core.vault import VaultManager

    (tmp_path / "Procedures").mkdir()
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Procedures"])
    v = VaultManager(cfg)
    v._ensure_ready = lambda: None
    v.collection = None

    r = v.list_notes_in("Proc\x00edures")

    assert "error" in r
    assert "null" not in r["error"].lower() and "lstat" not in r["error"].lower(), (
        f"a mensagem vaza detalhe de implementacao: {r['error']}"
    )

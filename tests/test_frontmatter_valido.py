"""Frontmatter que nenhum leitor de YAML abre, reportado como saudavel.

MEDIDO no vault desta maquina em 03/09/2026: 8.596 notas com frontmatter, DUAS
cujo bloco nao passa por `yaml.safe_load`, e `vault_health` reportando
`needs_repair: 0`, `truncated: 0`, `broken_links: 0`.

    Sessions/2026-07-21-transcript-26ef1c83.md
    Sessions/2026-07-27-transcript-ab9f777e.md
    title: Raw transcript - <local-command-caveat>Caveat: The messages below...
                                                        ^ este dois-pontos

O `Caveat:` no meio de um escalar sem aspas faz o YAML ler a linha como um mapa
aninhado, e o bloco inteiro deixa de ser legivel: title, date e
`type: session-transcript` somem para qualquer consumidor de YAML, Obsidian
incluido.

O defeito que gerou essas duas JA foi corrigido no hook, que hoje escreve
`title: "Raw transcript - ..."` com aspas. Foi corrigido no CODIGO e nunca no
DADO, e nada avisou, porque:

`_parse_frontmatter` e um divisor de linhas (`line.split(":", 1)`) que NAO
CONSEGUE FALHAR. Ele le essas duas notas sem reclamar. Uma verificacao de saude
construida sobre um parser que nao falha nunca reporta um bloco malformado.

Custo medido de validar de verdade: `yaml.safe_load` sobre os 8.596 blocos leva
1,013s, ou 0,118 ms por nota, num scan que ja le os 8.596 arquivos do disco e
fica cinco minutos em cache. Uma heuristica mais barata (procurar ": " em valor
sem aspas) pegaria estas duas e nao pegaria colchete desbalanceado nem
indentacao errada, ou seja, seria de novo uma checagem que nao enxerga o que
afirma enxergar.
"""
from __future__ import annotations

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


def _vault(tmp_path, notas: dict[str, str]) -> VaultManager:
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Sessions"])
    (tmp_path / "Sessions").mkdir(exist_ok=True)
    for nome, texto in notas.items():
        (tmp_path / "Sessions" / nome).write_text(texto, encoding="utf-8")
    v = VaultManager(cfg)
    v._ensure_ready = lambda: None
    v.collection = None
    return v


_QUEBRADA = (
    "---\n"
    "title: Raw transcript - <local-command-caveat>Caveat: The messages below\n"
    "date: 2026-07-21\n"
    "type: session-transcript\n"
    "---\n\n"
    "corpo\n"
)

_BOA = (
    '---\ntitle: "Raw transcript - <local-command-caveat>Caveat: The messages"\n'
    "date: 2026-07-21\ntype: session-transcript\n---\n\ncorpo\n"
)


def test_a_nota_com_dois_pontos_sem_aspas_e_contada(tmp_path):
    v = _vault(tmp_path, {"quebrada.md": _QUEBRADA, "boa.md": _BOA})

    saude = v.get_health_summary(force=True)

    assert saude["malformed_frontmatter"] == 1


def test_um_vault_so_de_notas_boas_reporta_zero(tmp_path):
    v = _vault(tmp_path, {"boa.md": _BOA, "outra.md": _BOA})
    assert v.get_health_summary(force=True)["malformed_frontmatter"] == 0


def test_nota_sem_frontmatter_nenhum_nao_e_malformada(tmp_path):
    """Nota sem bloco nao tem bloco quebrado. Contar seria inventar defeito."""
    v = _vault(tmp_path, {"nua.md": "# so um titulo\n\ncorpo\n"})
    assert v.get_health_summary(force=True)["malformed_frontmatter"] == 0


def test_colchete_desbalanceado_tambem_e_pego(tmp_path):
    """O caso que uma heuristica de dois-pontos deixaria passar, e que
    `_merge_alias` sabe produzir a partir de um titulo com `]`."""
    nota = "---\naliases: [Primeiro, Fecha] colchete]\ndate: 2026-09-04\n---\n\nx\n"
    v = _vault(tmp_path, {"colchete.md": nota})

    assert v.get_health_summary(force=True)["malformed_frontmatter"] == 1


def test_o_detalhe_nomeia_o_arquivo(tmp_path):
    """Contar sem dizer qual obriga a escrever um script para achar, que e
    exatamente o que `vault_health_detail` existe para evitar."""
    v = _vault(tmp_path, {"quebrada.md": _QUEBRADA, "boa.md": _BOA})
    v.get_health_summary(force=True)

    detalhe = v.health_detail(limit=10)

    stems = [i["stem"] for i in detalhe["malformed_frontmatter_items"]]
    assert stems == ["quebrada"]


def test_o_parser_tolerante_do_projeto_continua_lendo_a_nota_quebrada(tmp_path):
    """A deteccao nao pode virar recusa: essas notas seguem sendo lidas e
    indexadas como sempre foram. O que muda e o vault passar a AVISAR."""
    v = _vault(tmp_path, {"quebrada.md": _QUEBRADA})

    fm = v._parse_frontmatter(_QUEBRADA)

    assert fm["date"] == "2026-07-21"
    assert fm["type"] == "session-transcript"

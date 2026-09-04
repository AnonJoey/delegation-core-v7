"""A saida do dedup nao pode depender da ordem em que os nos chegam.

`_collision_rank` ja declara esse contrato para a passagem de ID exato ("fully
deterministic regardless of order", #1851) e o cumpre desempatando ate a chave
ficar unica. `_pick_winner`, na mesma funcao, parava em (sufixo, len(id)) e
empatava em todo par de IDs de mesmo comprimento; `min` entao caia na posicao da
lista, que num graph_build e a ordem do chunk.

O caso nao e artificial: modulos irmaos versionados empatam por construcao.
src_api_v1_billing_retry_policy e src_api_v2_billing_retry_policy tem 31
caracteres cada um, e o perdedor tem as arestas religadas no vencedor, de modo
que as relacoes da v2 eram reportadas como da v1 ou o contrario conforme o chunk
lido primeiro.
"""
from __future__ import annotations

import itertools

import pytest

from delegation_core.graph.dedup import _pick_winner, deduplicate_entities


def _no(nid: str, rotulo: str, arquivo: str = "docs/arq.md") -> dict:
    return {"id": nid, "label": rotulo, "source_file": arquivo, "file_type": "concept"}


def test_pick_winner_nao_depende_da_posicao_na_lista():
    a = _no("src_api_v1_billing_retry_policy", "Retry Policy")
    b = _no("src_api_v2_billing_retry_policy", "Retry Policy")
    assert len(a["id"]) == len(b["id"]), "o caso so vale se os IDs empatarem em comprimento"

    assert _pick_winner([a, b])["id"] == _pick_winner([b, a])["id"]


def test_pick_winner_mantem_as_chaves_anteriores_em_prioridade():
    """O desempate novo e o ULTIMO criterio: nao pode passar na frente dos dois
    que ja existiam, senao o ID lexicamente menor vence um ID mais curto."""
    curto = _no("zz_alpha", "Alpha")
    longo = _no("aa_alpha_extended", "Alpha")
    assert _pick_winner([curto, longo])["id"] == "zz_alpha"

    sem_sufixo = _no("mm_beta_c9", "Beta")   # tem sufixo de chunk
    com_sufixo = _no("aa_beta_c1", "Beta")   # tambem tem
    limpo = _no("nn_beta", "Beta")
    assert _pick_winner([sem_sufixo, com_sufixo, limpo])["id"] == "nn_beta"


def test_arestas_do_perdedor_nao_mudam_de_dono_conforme_a_ordem():
    a = _no("src_api_v1_billing_retry_policy", "Retry Policy")
    b = _no("src_api_v2_billing_retry_policy", "Retry Policy")
    arestas = [{"source": a["id"], "target": "cobranca", "type": "define"},
               {"source": b["id"], "target": "reembolso", "type": "define"}]

    saidas = set()
    for ordem in ([a, b], [b, a]):
        nos, eds = deduplicate_entities([dict(x) for x in ordem],
                                        [dict(x) for x in arestas], communities={})
        saidas.add((tuple(sorted(n["id"] for n in nos)),
                    tuple(sorted((e["source"], e["target"]) for e in eds))))
    assert len(saidas) == 1, f"a ordem de chegada mudou o grafo: {saidas}"


def test_toda_permutacao_produz_o_mesmo_conjunto_de_sobreviventes():
    """A garantia vale para o conjunto inteiro, nao so para um par."""
    nos = [
        _no("n0", "Validater Schedular", "b.py"),
        _no("n1", "Managor Coordinater Validater", "c.py"),
        _no("n2", "Resolver Processor", "c.py"),
        _no("n3", "Validater Schedular", "b.py"),
    ]
    resultados = set()
    for perm in itertools.permutations(nos):
        saida, _ = deduplicate_entities([dict(x) for x in perm], [], communities={})
        resultados.add(tuple(sorted(x["id"] for x in saida)))
    assert len(resultados) == 1, f"{len(resultados)} resultados distintos: {resultados}"


def test_pick_winner_recusa_lista_vazia():
    with pytest.raises(ValueError):
        _pick_winner([])

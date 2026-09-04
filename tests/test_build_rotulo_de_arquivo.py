"""O rotulo dos nos de arquivo: o contrato de #2032, medido.

#2032 existe para que dois arquivos de mesmo basename nao apareçam com o mesmo
rotulo no dashboard. `_shortest_unique_suffix` promete no docstring "the shortest
trailing path suffix that is UNIQUE among all_sfs" e tinha um caminho de saida
que devolvia valor NAO unico em silencio: quando nenhum sufixo distingue, ele
refazia "/".join(parts), e `parts` descarta justamente o que separava os dois
caminhos.

Os testes tambem prendem a razao de o caso nao chegar la pelo build: o
`build_from_json` normaliza `source_file` antes. Essa e a diferenca entre as duas
portas de entrada, e ela nao estava escrita em lugar nenhum.
"""
from __future__ import annotations

import pytest

from delegation_core.graph.build import (_is_file_node_label, _shortest_unique_suffix,
                                         build_from_json,
                                         disambiguate_file_labels_in_nodes)


# ── o contrato do helper ──────────────────────────────────────────────────────

def test_o_sufixo_devolvido_e_unico_no_conjunto():
    """A propriedade que da nome a funcao, sobre pares que so diferem no que a
    divisao por barra descarta."""
    for a, b in (("src//api/index.ts", "src/api/index.ts"),
                 ("./src/api/index.ts", "src/api/index.ts"),
                 ("src/api//index.ts", "src//api/index.ts")):
        conjunto = {a, b}
        ra = _shortest_unique_suffix(a, conjunto)
        rb = _shortest_unique_suffix(b, conjunto)
        assert ra != rb, f"mesmo rotulo {ra!r} para {a!r} e {b!r}"


def test_os_exemplos_do_docstring_continuam_valendo():
    d = {"a/b/index.ts", "c/b/index.ts"}
    assert _shortest_unique_suffix("a/b/index.ts", d) == "a/b/index.ts"
    d = {"x/index.ts", "y/index.ts"}
    assert _shortest_unique_suffix("x/index.ts", d) == "x/index.ts"


def test_sem_colisao_devolve_so_o_basename():
    """O caso comum nao pode ficar mais verboso por causa da correcao."""
    assert _shortest_unique_suffix("src/api/unico.ts", {"src/api/unico.ts"}) == "unico.ts"


def test_um_caminho_sufixo_do_outro_ainda_se_distingue():
    d = {"b/index.ts", "a/b/index.ts"}
    assert _shortest_unique_suffix("b/index.ts", d) != _shortest_unique_suffix("a/b/index.ts", d)


# ── as duas portas de entrada nao normalizam igual ────────────────────────────

def test_build_from_json_normaliza_o_separador_antes_de_rotular():
    """Pelo build, "src\\api\\x.ts" e "src/api/x.ts" sao o MESMO arquivo, entao o
    rotulo igual esta certo. E esse fato que torna a colisao inalcancavel por
    aqui, e ele nao estava preso em teste nenhum."""
    G = build_from_json({"nodes": [
        {"id": "a", "label": "index.ts", "source_file": "src\\api\\index.ts", "type": "file"},
        {"id": "b", "label": "index.ts", "source_file": "src/api/index.ts", "type": "file"},
        {"id": "c", "label": "index.ts", "source_file": "src/web/index.ts", "type": "file"},
    ], "edges": []})
    por_id = {nid: at for nid, at in G.nodes(data=True)}
    assert por_id["a"]["source_file"] == por_id["b"]["source_file"] == "src/api/index.ts"
    assert por_id["a"]["label"] == por_id["b"]["label"]
    assert por_id["c"]["label"] != por_id["a"]["label"]


def test_a_lista_crua_nao_normaliza_e_por_isso_precisa_do_sufixo_unico():
    """`disambiguate_file_labels_in_nodes` recebe lista crua e nao normaliza
    nada, entao e por ela que o caminho de saida do helper e alcancavel."""
    nos = [
        {"id": "n1", "label": "index.ts", "source_file": "src//api/index.ts"},
        {"id": "n2", "label": "index.ts", "source_file": "src/api/index.ts"},
    ]
    disambiguate_file_labels_in_nodes(nos)
    assert nos[0]["label"] != nos[1]["label"], "dois nos com o mesmo rotulo"


def test_rotular_duas_vezes_nao_muda_nada():
    """Idempotencia, que o docstring afirma e nenhum teste conferia."""
    nos = [
        {"id": "n1", "label": "index.ts", "source_file": "a/b/index.ts"},
        {"id": "n2", "label": "index.ts", "source_file": "c/b/index.ts"},
    ]
    disambiguate_file_labels_in_nodes(nos)
    primeira = [n["label"] for n in nos]
    disambiguate_file_labels_in_nodes(nos)
    assert [n["label"] for n in nos] == primeira


def test_o_predicado_aceita_o_rotulo_qualificado_que_ele_mesmo_gera():
    """Sem isso a segunda passagem nao reconheceria os nos que a primeira
    renomeou, e a idempotencia acima cairia."""
    assert _is_file_node_label("index.ts", "a/b/index.ts")
    assert _is_file_node_label("b/index.ts", "a/b/index.ts")
    assert not _is_file_node_label("outro.ts", "a/b/index.ts")
    assert not _is_file_node_label("", "a/b/index.ts")
    assert not _is_file_node_label("index.ts", "")

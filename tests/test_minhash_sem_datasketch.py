"""O que o `_minhash.py` promete, medido.

O modulo e uma reimplementacao a mao de datasketch.MinHash/MinHashLSH, escrita
para nao arrastar scipy (o docstring conta o porque: um import que sob EDR em
Windows corporativo trava por minutos). A frase que ele assina e "hash family
and LSH band structure are equivalent to datasketch so dedup quality is
unchanged" -- e ate agora nenhum teste tocava neste arquivo, entao a frase era
so uma frase.

Aqui a qualidade e medida em vez de afirmada: o estimador tem que ser nao
enviesado, a recuperacao do LSH tem que subir com o Jaccard e acompanhar
1-(1-s^r)^b, e os parametros tem que cobrir as permutacoes disponiveis.

O que estes testes NAO afirmam: que os coeficientes sejam bit a bit os do
datasketch. Nao sao -- o datasketch sorteia (a,b) intercalados num unico fluxo e
aqui os `a` saem todos antes dos `b`, o que da outra familia de permutacoes com
a mesma qualidade. Sketches gravados por uma das duas implementacoes nao sao
comparaveis com os da outra.
"""
from __future__ import annotations

import random

import pytest

from delegation_core.graph._minhash import (MinHash, MinHashLSH, _lsh_integrate,
                                            _optimal_lsh_params)


def _conjuntos_com_jaccard(alvo: float, semente: int) -> tuple[list[bytes], list[bytes]]:
    """Dois conjuntos cujo Jaccard e aproximadamente `alvo`."""
    rng = random.Random(semente)
    comuns = 200
    exclusivos = round(comuns * (1 - alvo) / alvo / 2)
    base = [f"c{rng.randrange(10**9)}".encode() for _ in range(comuns)]
    a = base + [f"a{rng.randrange(10**9)}".encode() for _ in range(exclusivos)]
    b = base + [f"b{rng.randrange(10**9)}".encode() for _ in range(exclusivos)]
    return a, b


def _sketch(itens: list[bytes], num_perm: int = 128) -> MinHash:
    m = MinHash(num_perm=num_perm)
    for x in itens:
        m.update(x)
    return m


def _jaccard_real(a: list[bytes], b: list[bytes]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


# ── o estimador ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("alvo", [0.3, 0.5, 0.7, 0.9])
def test_o_estimador_nao_e_enviesado(alvo):
    """A fracao de posicoes iguais entre dois sketches estima o Jaccard.

    Esta e a propriedade que o dedup inteiro usa. Com 128 permutacoes o desvio
    padrao do estimador e ~sqrt(s(1-s)/128), no pior caso ~0.044, entao a media
    de 30 pares tem que cair bem dentro de 0.05.
    """
    erros = []
    for semente in range(30):
        a, b = _conjuntos_com_jaccard(alvo, semente)
        est = float((_sketch(a).hashvalues == _sketch(b).hashvalues).mean())
        erros.append(est - _jaccard_real(a, b))
    media = sum(erros) / len(erros)
    assert abs(media) < 0.05, f"vies de {media:+.3f} em Jaccard {alvo}"


def test_sketches_identicos_para_o_mesmo_conjunto():
    """Sem isso, dois builds do mesmo repo dariam grafos diferentes."""
    itens = [f"x{i}".encode() for i in range(50)]
    assert (_sketch(itens).hashvalues == _sketch(list(reversed(itens))).hashvalues).all()


def test_conjuntos_disjuntos_quase_nao_colidem():
    a = [f"a{i}".encode() for i in range(200)]
    b = [f"b{i}".encode() for i in range(200)]
    est = float((_sketch(a).hashvalues == _sketch(b).hashvalues).mean())
    assert est < 0.05, f"estimou {est:.3f} para conjuntos disjuntos"


# ── o LSH ─────────────────────────────────────────────────────────────────────

def test_parametros_cobrem_quase_todas_as_permutacoes():
    """b*r ocioso e permutacao calculada e jogada fora."""
    for limiar in (0.5, 0.7, 0.9):
        b, r = _optimal_lsh_params(limiar, 128)
        assert b * r <= 128
        assert b * r >= 120, f"limiar {limiar}: so {b * r} de 128 permutacoes usadas"


def test_a_recuperacao_sobe_com_o_jaccard():
    """Monotonicidade e o unico jeito de o bloqueio fazer sentido."""
    b, r = _optimal_lsh_params(0.7, 128)
    medidas = []
    for alvo in (0.5, 0.7, 0.85, 0.95):
        achou = 0
        N = 60
        for semente in range(N):
            xa, xb = _conjuntos_com_jaccard(alvo, semente + 1000)
            ma, mb = _sketch(xa), _sketch(xb)
            lsh = MinHashLSH(threshold=0.7, num_perm=128)
            lsh.insert("a", ma)
            lsh.insert("b", mb)
            achou += "b" in lsh.query(ma)
        medidas.append(achou / N)
    assert medidas == sorted(medidas), f"recuperacao nao monotona: {medidas}"
    assert medidas[0] < 0.35, f"recuperacao alta demais abaixo do limiar: {medidas[0]:.2f}"
    assert medidas[-1] > 0.95, f"recuperacao baixa demais em 0.95: {medidas[-1]:.2f}"


def test_o_limiar_configurado_nao_e_o_ponto_de_operacao():
    """Medido, e nao suposto: em Jaccard 0.7, que e o `_LSH_THRESHOLD` do dedup,
    o bloqueio DEIXA PASSAR mais da metade dos pares. Quem le a constante como
    "pares a partir de 0.7 sao comparados" le errado; o ponto onde a recuperacao
    passa de 85 por cento fica perto de 0.80. Nada no dedup.py diz isso, e este
    teste existe para que a caracterizacao apareca se alguem mexer nos numeros.
    """
    achou = 0
    N = 120
    for semente in range(N):
        xa, xb = _conjuntos_com_jaccard(0.7, semente + 5000)
        lsh = MinHashLSH(threshold=0.7, num_perm=128)
        lsh.insert("a", _sketch(xa))
        lsh.insert("b", _sketch(xb))
        achou += "b" in lsh.query(_sketch(xa))
    assert 0.30 < achou / N < 0.60, f"recuperacao no proprio limiar: {achou / N:.2f}"


def test_insert_recusa_chave_repetida():
    """O dedup depende disso: ele captura ValueError para nao inserir duas vezes."""
    lsh = MinHashLSH(threshold=0.7, num_perm=128)
    m = _sketch([b"x"])
    lsh.insert("k", m)
    with pytest.raises(ValueError, match="already exists"):
        lsh.insert("k", m)


def test_query_devolve_a_propria_chave():
    """O dedup filtra `neighbor_id == node_id`; se isso mudasse, ele se fundiria
    consigo mesmo."""
    lsh = MinHashLSH(threshold=0.7, num_perm=128)
    m = _sketch([f"x{i}".encode() for i in range(20)])
    lsh.insert("eu", m)
    assert lsh.query(m) == ["eu"]


# ── a integracao numerica que substituiu o scipy ──────────────────────────────

def test_a_integracao_bate_com_o_valor_fechado():
    """_lsh_integrate substitui scipy.integrate.quad. Em integrandos com
    primitiva conhecida o erro tem que ser pequeno, senao a escolha de (b, r)
    esta sendo feita sobre numeros errados."""
    assert _lsh_integrate(lambda s: s, 0.0, 1.0) == pytest.approx(0.5, abs=0.01)
    assert _lsh_integrate(lambda s: s ** 2, 0.0, 1.0) == pytest.approx(1 / 3, abs=0.01)
    assert _lsh_integrate(lambda s: 1.0, 0.0, 0.7) == pytest.approx(0.7, abs=1e-9)


def test_intervalo_degenerado_nao_quebra():
    """threshold 0.0 e 1.0 zeram um dos dois lados da busca de parametros."""
    assert _lsh_integrate(lambda s: s, 0.5, 0.5) == 0.0
    assert _optimal_lsh_params(1.0, 32)[0] >= 1
    assert _optimal_lsh_params(0.0, 32)[0] >= 1

"""As funcoes puras do organizer, que decidem o que entra e o que e revisado.

`organizer.py` tem 554 linhas e era o maior dos nove modulos do nucleo que
nenhum arquivo de teste importava. O grosso dele (`run`, `heal`) precisa de um
modelo para dizer alguma coisa util, e o README ja registra isso como a lacuna
conhecida da suite. Mas os quatro auxiliares puros nao precisam de modelo
nenhum, e sao eles que decidem:

- se uma nota nova pode ser fundida numa existente (`_merge_forbidden`);
- se um titulo de secao e generico e precisa ser reescrito (`_is_placeholder`);
- que nota vale 0.0 e qual vale 1.0 (`_synthesis_quality`);
- como esse numero e gravado na nota (`_inject_quality_frontmatter`).

O terceiro e da familia que mais mordeu nesta noite: devolve um numero
plausivel, e o `quality_threshold` decide em cima dele sem nada levantar erro.

Nenhum defeito foi encontrado aqui, e por isso nenhuma linha de organizer.py
foi alterada. Estes testes prendem o comportamento que ja estava certo, que e
o que faltava.
"""
from __future__ import annotations

import json

import pytest

from delegation_core import organizer


# ── _merge_forbidden: o opt-out de fusao ────────────────────────────────────

@pytest.mark.parametrize("valor", ["true", "True", "TRUE", "yes", "on", "1", " true "])
def test_sidecar_no_merge_proibe(valor):
    assert organizer._merge_forbidden({"no_merge": valor}, "corpo") is True


@pytest.mark.parametrize("valor", ["false", "no", "off", "0", "", "talvez"])
def test_sidecar_no_merge_com_valor_falso_nao_proibe(valor):
    assert organizer._merge_forbidden({"no_merge": valor}, "corpo") is False


def test_sem_sidecar_nao_proibe():
    assert organizer._merge_forbidden(None, "corpo") is False
    assert organizer._merge_forbidden({}, "corpo") is False


def test_no_merge_booleano_do_yaml():
    """`no_merge: true` no YAML vira bool Python, nao string."""
    assert organizer._merge_forbidden({"no_merge": True}, "corpo") is True
    assert organizer._merge_forbidden({"no_merge": False}, "corpo") is False


@pytest.mark.parametrize("valor", ["false", "no", "off", "0", '"false"', "'no'"])
def test_frontmatter_vault_merge_falso_proibe(valor):
    """O proprio arquivo pode recusar fusao, sem sidecar."""
    texto = f"---\ntitle: uma nota\nvault_merge: {valor}\n---\n\ncorpo\n"
    assert organizer._merge_forbidden(None, texto) is True


def test_frontmatter_vault_merge_verdadeiro_nao_proibe():
    texto = "---\nvault_merge: true\n---\n\ncorpo\n"
    assert organizer._merge_forbidden(None, texto) is False


def test_frontmatter_sem_a_chave_nao_proibe():
    assert organizer._merge_forbidden(None, "---\ntitle: x\n---\n\ncorpo\n") is False


def test_frontmatter_aberto_e_nunca_fechado_nao_explode():
    """Saida de modelo truncada no meio do bloco e entrada realista."""
    assert organizer._merge_forbidden(None, "---\nvault_merge: false\n") is False


def test_vault_merge_no_corpo_nao_conta():
    """So o frontmatter decide: a mesma linha no corpo e prosa."""
    texto = "corpo que fala sobre\nvault_merge: false\nno meio do texto\n"
    assert organizer._merge_forbidden(None, texto) is False


def test_sidecar_vence_mesmo_com_frontmatter_permissivo():
    texto = "---\nvault_merge: true\n---\n\ncorpo\n"
    assert organizer._merge_forbidden({"no_merge": "true"}, texto) is True


# ── _is_placeholder: titulo generico que sera reescrito ─────────────────────

@pytest.mark.parametrize("titulo", [
    "Section 1", "Section 12", "section 3", "SECTION 4",
    "Page 1", "Pages 1-2", "Pages 3–4", "page 7",
    "Part 2", "5", "10-12", "  Section 2  ",
])
def test_titulo_generico_e_reconhecido(titulo):
    assert organizer._is_placeholder(titulo) is True


@pytest.mark.parametrize("titulo", [
    "Introduction", "Decisao sobre o vendor", "Section de abertura",
    "Pagina inicial", "Part of the plan", "Capitulo 3", "3 decisoes tomadas",
    "", "Section", "Page",
])
def test_titulo_descritivo_nao_e_generico(titulo):
    assert organizer._is_placeholder(titulo) is False


def test_o_generico_tem_que_casar_o_titulo_INTEIRO():
    """Ancorado nas duas pontas: senao 'Section 1 do contrato' seria reescrito
    e o titulo que o autor deu se perderia."""
    assert organizer._is_placeholder("Section 1 do contrato") is False
    assert organizer._is_placeholder("sobre a Section 1") is False


# ── _synthesis_quality: o numero que decide o que precisa de revisao ────────

def test_saida_boa_vale_um():
    saida = "## Resumo\n\nO cliente aprovou o escopo e pediu entrega em outubro."
    entrada = "x" * 4000
    assert organizer._synthesis_quality(saida, entrada) == (1.0, [])


def test_saida_curta_demais_vale_zero():
    score, issues = organizer._synthesis_quality("ok", "x" * 4000)
    assert score == 0.0
    assert issues == ["output_too_short"]


def test_saida_curta_sai_cedo_e_nao_acumula_outros_problemas():
    """Menos de 30 chars devolve na hora, com um motivo so."""
    _, issues = organizer._synthesis_quality("john doe", "x" * 4000)
    assert issues == ["output_too_short"]


def test_compressao_que_nao_comprimiu_e_apontada():
    entrada = "y" * 1000
    saida = "z" * 950                      # 95% do original
    score, issues = organizer._synthesis_quality(saida, entrada)
    assert "compression_failed" in issues
    assert score == 0.7


def test_entrada_curta_pode_expandir_sem_penalidade():
    """Conteudo curto legitimamente cresce ao virar nota estruturada."""
    entrada = "y" * 400                     # abaixo do corte de 500
    saida = "z" * 900
    assert organizer._synthesis_quality(saida, entrada) == (1.0, [])


def test_nome_alucinado_e_apontado():
    saida = "## Resumo\n\nA reuniao com John Doe definiu o proximo passo."
    score, issues = organizer._synthesis_quality(saida, "x" * 4000)
    assert any(i.startswith("hallucinated_name:") for i in issues)
    assert score == 0.7


def test_vazamento_de_prompt_e_apontado():
    saida = "## Resumo\n\nYou are an AI assistant. O cliente aprovou o escopo."
    _, issues = organizer._synthesis_quality(saida, "x" * 4000)
    assert "prompt_leak" in issues


def test_vazamento_de_prompt_conta_uma_vez_so():
    """Varios padroes de vazamento na mesma saida nao multiplicam a punicao."""
    saida = ("## Resumo\n\nYou are an AI. As an AI, summarize the following. "
             "No preamble. O cliente aprovou.")
    _, issues = organizer._synthesis_quality(saida, "x" * 4000)
    assert issues.count("prompt_leak") == 1


def test_cada_problema_custa_tres_decimos():
    entrada = "x" * 4000
    limpa = "## Resumo\n\nO cliente aprovou o escopo e pediu entrega em outubro."
    um = "## Resumo\n\nA reuniao com John Doe definiu o proximo passo do projeto."
    dois = "## Resumo\n\nJohn Doe e Jane Doe definiram o proximo passo do projeto."

    assert organizer._synthesis_quality(limpa, entrada)[0] == 1.0
    assert organizer._synthesis_quality(um, entrada)[0] == 0.7
    assert organizer._synthesis_quality(dois, entrada)[0] == 0.4


def test_a_nota_nunca_vale_menos_que_zero():
    """Quatro problemas dariam -0.2 sem o piso."""
    saida = ("You are an AI. John Doe, Jane Doe e Sarah Chen estiveram na "
             "reuniao com Maria Oliveira sobre o escopo do projeto.")
    score, issues = organizer._synthesis_quality(saida, "x" * 4000)
    assert len(issues) >= 4
    assert score == 0.0


def test_o_limiar_padrao_separa_um_problema_de_dois():
    """A consequencia pratica da escala: com quality_threshold 0.5, uma saida
    com UM problema passa e com DOIS vai para revisao. Este teste existe para
    que mudar o passo de 0.3 seja uma decisao consciente."""
    from delegation_core.config import Config

    limiar = Config().quality_threshold
    entrada = "x" * 4000
    um = "## Resumo\n\nA reuniao com John Doe definiu o proximo passo do projeto."
    dois = "## Resumo\n\nJohn Doe e Jane Doe definiram o proximo passo do projeto."

    assert organizer._synthesis_quality(um, entrada)[0] >= limiar
    assert organizer._synthesis_quality(dois, entrada)[0] < limiar


# ── _inject_quality_frontmatter: como o numero e gravado ────────────────────

def _frontmatter(texto: str) -> str:
    assert texto.startswith("---\n")
    return texto[4:texto.index("\n---\n", 4)]


def test_nota_sem_frontmatter_ganha_um():
    saida = organizer._inject_quality_frontmatter("## Corpo\n", 0.7, ["prompt_leak"], True)
    fm = _frontmatter(saida)
    assert "quality_score: 0.7" in fm
    assert 'quality_issues: ["prompt_leak"]' in fm
    assert "needs_review: true" in fm
    assert "## Corpo" in saida


def test_frontmatter_existente_e_preservado():
    conteudo = '---\ntitle: "uma nota"\ntags: [a, b]\n---\n\n## Corpo\n'
    fm = _frontmatter(organizer._inject_quality_frontmatter(conteudo, 1.0, [], False))
    assert 'title: "uma nota"' in fm
    assert "tags: [a, b]" in fm
    assert "quality_score: 1.0" in fm


def test_um_unico_bloco_de_frontmatter():
    """Blocos empilhados ja custaram caro neste projeto: o Obsidian le so o
    primeiro e o resto vira texto do corpo."""
    conteudo = '---\ntitle: "x"\n---\n\n## Corpo\n'
    saida = organizer._inject_quality_frontmatter(conteudo, 1.0, [], False)
    assert saida.count("\n---\n") == 1


def test_regravar_substitui_em_vez_de_duplicar():
    """A nota passa por aqui de novo no passe de heal."""
    primeira = organizer._inject_quality_frontmatter("## Corpo\n", 0.4, ["prompt_leak"], True)
    segunda = organizer._inject_quality_frontmatter(primeira, 1.0, [], False)
    fm = _frontmatter(segunda)

    assert fm.count("quality_score:") == 1
    assert fm.count("needs_review:") == 1
    assert "quality_score: 1.0" in fm
    assert "needs_review: false" in fm
    assert "prompt_leak" not in fm


def test_os_problemas_sao_json_legivel():
    saida = organizer._inject_quality_frontmatter(
        "## Corpo\n", 0.4, ["prompt_leak", "hallucinated_name:john doe"], True)
    linha = next(l for l in _frontmatter(saida).splitlines()
                 if l.startswith("quality_issues:"))
    assert json.loads(linha.split(":", 1)[1].strip()) == [
        "prompt_leak", "hallucinated_name:john doe"]


def test_o_corpo_nao_e_alterado():
    corpo = "## Corpo\n\nCom ---\n tres tracos no meio.\n"
    saida = organizer._inject_quality_frontmatter(corpo, 1.0, [], False)
    assert saida.endswith(corpo)

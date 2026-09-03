"""`synthesis_lang` valia para a sintese e mais nada.

O ajuste existe desde a v0.2 e ate 03/09/2026 um unico modulo o lia,
`synthesizer.py`. Todo o resto montava prompt em ingles e deixava o modelo local
escolher o idioma da resposta. Visto de fora isso faz o ajuste parecer
simplesmente quebrado: com `synthesis_lang: "pt"` na config e o vault inteiro em
portugues, o resumo do `search_vault` voltava em ingles.

Vale para o resumo do `search_web`, para os titulos de secao que o `organizer`
escreve dentro da nota, e para o resumo semanal de manutencao. Cada um produz
texto que uma pessoa le, e nenhum consultava o ajuste.

E ja havia divergido dentro do proprio repositorio: o `compress` da ferramenta
MCP tinha a instrucao de idioma, montada a mao, e o `cmd_compress` do CLI, que e
a mesma operacao no terminal, nao. E esse o argumento contra repetir o
dicionario em cada ponto de chamada, e a razao de a frase morar em `config.py`.

**Os dois pontos que NAO recebem a frase sao o ponto do desenho**, e tem teste
proprio aqui para que a correcao nao se espalhe adiante por engano.
"""
from __future__ import annotations

import inspect

import pytest

from delegation_core import cli, organizer, server
from delegation_core.config import INSTRUCOES_DE_IDIOMA, lang_instruction, with_lang


class _Cfg:
    def __init__(self, lang):
        self.synthesis_lang = lang


# ── os auxiliares ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang,trecho", [("pt", "portugues"), ("en", "English")])
def test_a_frase_sai_no_idioma_configurado(lang, trecho):
    assert trecho in lang_instruction(_Cfg(lang))


@pytest.mark.parametrize("lang", ["PT", "Pt", "pT"])
def test_o_valor_e_lido_sem_ligar_para_a_caixa(lang):
    assert lang_instruction(_Cfg(lang)) == INSTRUCOES_DE_IDIOMA["pt"]


@pytest.mark.parametrize("valor", ["klingon", "", None, "pt-BR", "xx"])
def test_valor_desconhecido_nao_vira_instrucao_inventada(valor):
    """Vazio e resposta legitima: o prompt fica identico ao que era antes de
    isto existir, em vez de carregar uma frase que ninguem pediu."""
    assert lang_instruction(_Cfg(valor)) in ("", INSTRUCOES_DE_IDIOMA["en"])


def test_config_sem_o_campo_nao_explode():
    class Vazio:
        pass
    assert lang_instruction(Vazio()) == INSTRUCOES_DE_IDIOMA["en"]


def test_with_lang_anexa_a_frase():
    assert with_lang("Vault Analyst.", _Cfg("pt")) == \
        "Vault Analyst. " + INSTRUCOES_DE_IDIOMA["pt"]


def test_with_lang_nao_deixa_ponto_pendurado_quando_a_frase_e_vazia():
    """A juncao ingenua deixa um espaco no fim, e esse e o tipo de detalhe que
    so aparece na configuracao que ninguem testa."""
    assert with_lang("Vault Analyst.", _Cfg("klingon")) == "Vault Analyst."
    assert not with_lang("Vault Analyst.", _Cfg("klingon")).endswith(" ")


def test_with_lang_normaliza_espaco_do_lado_esquerdo():
    assert with_lang("Vault Analyst.  ", _Cfg("pt")) == \
        "Vault Analyst. " + INSTRUCOES_DE_IDIOMA["pt"]


# ── onde a frase entra ──────────────────────────────────────────────────────

def _fonte_da_funcao(modulo, nome_no_arquivo: str) -> str:
    """Janela em torno da ancora, e nao a partir dela.

    A primeira versao cortava a partir da ancora, e para um dos casos a ancora
    cai DENTRO da chamada `with_lang(...)`: a janela comecava depois da
    abertura e o teste acusava codigo correto. Olhar para tras tambem resolve
    o caso de o `system=` vir antes do prompt.
    """
    fonte = inspect.getsource(modulo)
    i = fonte.index(nome_no_arquivo)
    return fonte[max(0, i - 400):i + 1400]


ENTRA = [
    (server, "Summarize these vault notes for the query", "search_vault"),
    (server, "Compress these search results into key facts", "search_web"),
    (server, "Compression Engine. Be extremely concise.", "compress (MCP)"),
    (cli, "Extract only key facts, decisions, and action items", "cmd_compress (CLI)"),
    (organizer, "Write a 3-5 word title for this section", "titulo de secao"),
    (organizer, "Write a 3-sentence vault maintenance summary", "resumo semanal"),
]


@pytest.mark.parametrize("modulo,ancora,rotulo", ENTRA,
                         ids=[c[2] for c in ENTRA])
def test_todo_prompt_de_prosa_consulta_o_ajuste(modulo, ancora, rotulo):
    """Cada um destes produz texto que uma pessoa le. Nenhum consultava."""
    fonte = _fonte_da_funcao(modulo, ancora)
    assert ("with_lang(" in fonte or "_com_idioma(" in fonte
            or "_frase_de_idioma(" in fonte), (
        f"{rotulo}: monta um prompt cuja saida e prosa e nao consulta "
        "synthesis_lang. Foi assim que um vault em portugues recebeu resumo "
        "em ingles."
    )


# ── e onde ela NAO entra, que e o ponto do desenho ─────────────────────────

def test_o_classificador_NAO_recebe_instrucao_de_idioma():
    """A saida dele e nome de pasta que o chamador compara, nao prosa.

    Traduzido, `decisions` vira `decisões` e nenhuma pasta corresponde. Um
    prompt que devolve identificador nao pode receber instrucao de idioma.
    """
    from delegation_core import classifier
    fonte = inspect.getsource(classifier)
    assert "with_lang(" not in fonte and "lang_instruction(" not in fonte, (
        "o classificador ganhou instrucao de idioma; a saida dele e "
        "identificador de pasta, nao texto para ler"
    )


def test_engine_invoke_NAO_recebe_instrucao_de_idioma():
    """`engine.invoke` e transporte: serve prosa e rotulo pelo mesmo cano.

    So o chamador sabe qual dos dois esta pedindo, entao a frase entra em quem
    chama e nunca no transporte, que seria o lugar tentador.
    """
    from delegation_core import engine as engine_mod
    fonte = inspect.getsource(engine_mod)
    assert "with_lang(" not in fonte and "lang_instruction(" not in fonte, (
        "engine.invoke ganhou instrucao de idioma; ele transporta tambem os "
        "prompts cuja saida e rotulo"
    )


def test_a_frase_mora_num_lugar_so():
    """O dicionario nao pode voltar a ser copiado em cada ponto de chamada.

    Foi a copia que fez o `compress` do MCP e o do CLI divergirem dentro do
    mesmo commit.
    """
    for modulo in (server, cli, organizer):
        fonte = inspect.getsource(modulo)
        assert "Answer in English." not in fonte, (
            f"{modulo.__name__} tem a frase escrita a mao; ela mora em config.py"
        )

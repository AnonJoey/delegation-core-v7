"""Numeros que a prosa promete e o codigo desmente.

`test_version_consistency.py` ja resolveu esta familia para `__version__`: a
versao drifou tres vezes, foi re-sincronizada a mao tres vezes com um comentario
prometendo lockstep, e so parou quando um teste passou a exigir que a COPIA nao
existisse. Este arquivo faz o mesmo com as contagens.

Medido em 02/09/2026 contra a arvore de trabalho:

  HANDOFF.md dizia  "204 passing", "19 test files"   -> 1042 testes em 67 arquivos
  HANDOFF.md dizia  "31 public MCP tools"            -> 54 registros @mcp.tool()
  README.md  dizia  "579 tests"                      -> 1042
  AGENT_GUIDE dizia tool_count 45 num exemplo        -> 54

O HANDOFF abre com "Facts below were true and verified at the timestamp above",
e a data era 28/07. Cinco semanas de deriva num documento escrito para ser a
primeira coisa que o proximo agente le.

A correcao nao e sincronizar as copias, e tirar as copias volateis e guardar as
que importam. Um total de testes muda a cada teste escrito: nao pertence a
prosa. O numero de ferramentas MCP e superficie de API, muda raramente, e alguem
que le "31" e planeja em cima disso planeja errado: esse fica, e e verificado.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
HANDOFF = RAIZ / "HANDOFF.md"
README = RAIZ / "README.md"
SERVER = RAIZ / "src" / "delegation_core" / "server.py"

#: "204 passing", "579 tests", "1042 unit tests", "19 test files".
_CONTAGEM_DE_TESTES = re.compile(
    r"\b\d{2,5}\s+(?:unit\s+)?(?:tests?\b|passing\b|test\s+files\b)", re.IGNORECASE)


def _secao(texto: str, titulo: str) -> str:
    """O corpo de uma secao markdown, ate o proximo cabecalho de mesmo nivel."""
    linhas = texto.split("\n")
    try:
        inicio = next(i for i, ln in enumerate(linhas) if ln.strip() == titulo)
    except StopIteration:
        pytest.fail(f"secao {titulo!r} sumiu de {HANDOFF.name}")
    nivel = titulo.split(" ")[0]
    for j in range(inicio + 1, len(linhas)):
        if linhas[j].startswith(nivel + " "):
            return "\n".join(linhas[inicio:j])
    return "\n".join(linhas[inicio:])


def test_handoff_nao_promete_um_total_de_testes():
    """A secao que se declara verificada nao pode carregar um numero que apodrece.

    Historico datado ("Recent history (Session Summary - 2026-07-28)") fica de
    fora de proposito: ali um numero antigo e o registro do que era verdade
    naquele dia, nao uma afirmacao sobre agora.
    """
    atual = _secao(HANDOFF.read_text(encoding="utf-8"), "## Current state (all verified, not assumed)")
    # A propria explicacao cita as contagens antigas entre aspas. Ler o que esta
    # citado como se fosse afirmacao seria o teste falhando pelo motivo errado.
    sem_citacoes = re.sub(r'"[^"]*"', "", atual)

    achados = _CONTAGEM_DE_TESTES.findall(sem_citacoes)
    assert not achados, (
        f"HANDOFF.md voltou a fixar contagem de testes: {achados}. "
        "Rode a suite; nao escreva o total. Ele drifou de 204 para 1042 "
        "sem que nada falhasse."
    )


def test_readme_nao_promete_um_total_de_testes():
    texto = README.read_text(encoding="utf-8")
    sem_citacoes = re.sub(r'"[^"]*"', "", texto)
    achados = _CONTAGEM_DE_TESTES.findall(sem_citacoes)
    assert not achados, (
        f"README.md voltou a fixar contagem de testes: {achados}. "
        "Chegou a dizer 579 contra 1042 reais."
    )


def test_contagem_de_ferramentas_mcp_na_prosa_bate_com_o_servidor():
    """Superficie de API: esta pode ficar na prosa, entao tem que estar certa.

    Quem le "31 public MCP tools" e decide o que cabe no contexto do cliente
    decide com um numero 43% menor que o real.
    """
    real = len(re.findall(r"^@mcp\.tool\(\)", SERVER.read_text(encoding="utf-8"), re.MULTILINE))
    assert real > 0, "nenhum @mcp.tool() encontrado — o padrao de busca quebrou"

    for doc in (HANDOFF, README):
        texto = doc.read_text(encoding="utf-8")
        for achado in re.finditer(r"\b(\d{1,4})\s+(?:public\s+)?(?:MCP\s+tools|`?@mcp\.tool)", texto):
            assert int(achado.group(1)) == real, (
                f"{doc.name} diz {achado.group(1)} ferramentas MCP, o servidor "
                f"registra {real}. Ou corrija o numero, ou tire-o e aponte para "
                f"capabilities(), que pergunta ao servidor em vez de repetir."
            )


def test_handoff_declara_a_versao_corrente():
    """O cabecalho dizia core v0.9.0 com a arvore em v0.13.0."""
    import delegation_core

    cabecalho = HANDOFF.read_text(encoding="utf-8").split("\n## ")[0]
    versoes = re.findall(r"v(\d+\.\d+\.\d+)", cabecalho)
    assert versoes, "o cabecalho do HANDOFF nao declara mais nenhuma versao"
    assert delegation_core.__version__ in versoes, (
        f"HANDOFF.md abre declarando {versoes}, a arvore esta em "
        f"{delegation_core.__version__}. Esse cabecalho e a primeira coisa que "
        f"o proximo agente le."
    )


def test_conftest_existe_e_e_autouse():
    """A guarda de estado da suite nao pode sumir sem alguem notar.

    Ela foi escrita depois de um teste sobrescrever ~/.delegation_core/config.json
    e derrubar o daemon. Um conftest apagado devolve exatamente essa exposicao,
    em silencio.
    """
    conftest = (RAIZ / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "autouse=True" in conftest
    assert "CONFIG_FILE" in conftest

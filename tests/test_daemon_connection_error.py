"""`_is_connection_error` estourava a pilha em vez de responder a pergunta.

A funcao decide se uma excecao veio do transporte, e o `delegation-core reindex`
pela CLI depende dela para saber se o daemon caiu ou se o trabalho falhou. Ela
caminhava a cadeia `__cause__`/`__context__` com um conjunto `seen` para nao
girar em ciclo, e isso funcionava. O que nao funcionava era a recursao ao redor:
ao encontrar um `ExceptionGroup` ela chamava a si mesma para cada membro, e cada
chamada comecava um `seen` vazio.

Entao um ciclo que atravessa a fronteira do grupo nunca era visto. Grupo A com
membro B, `B.__context__` apontando de volta para A: a chamada de fora ve A,
recursa em B; a de dentro ve B, anda para A, que nao esta no `seen` dela, ve que
A e grupo, recursa em B de novo. As duas se passam o mesmo par para sempre ate
`RecursionError`.

Nao e formato inventado: os task groups do anyio embrulham excecoes nesses
grupos, e um erro de transporte relancado dentro de um task group e exatamente
como um membro passa a carregar o grupo como contexto.

A correcao troca recursao por lista de pendencias com um unico `seen`, o que
resolve tambem o caso mais silencioso do aninhamento profundo, que gastava um
quadro de pilha por nivel.
"""
from __future__ import annotations

import pytest

from delegation_core import daemon


def _ligar_contexto(exc: BaseException, contexto: BaseException) -> BaseException:
    """Encadeia sem levantar, que e como estas cadeias chegam na pratica."""
    exc.__context__ = contexto
    return exc


# ── o defeito ───────────────────────────────────────────────────────────────

def test_ciclo_atravessando_a_fronteira_do_grupo_nao_estoura():
    """O caso exato: o membro aponta de volta para o grupo que o contem.

    Antes da correcao isto nao devolvia False, morria com RecursionError.
    """
    membro = ValueError("falhou")
    grupo = ExceptionGroup("task group", [membro])
    _ligar_contexto(membro, grupo)

    assert daemon._is_connection_error(grupo) is False


def test_o_mesmo_ciclo_ainda_encontra_o_erro_de_transporte():
    """Sair do ciclo nao pode custar a resposta certa: se ha erro de conexao
    em algum lugar do grafo, ele continua sendo encontrado."""
    membro = ConnectionRefusedError("recusou")
    grupo = ExceptionGroup("task group", [membro])
    _ligar_contexto(membro, grupo)

    assert daemon._is_connection_error(grupo) is True


def test_dois_grupos_apontando_um_para_o_outro():
    a_membro = ValueError("a")
    b_membro = ValueError("b")
    grupo_a = ExceptionGroup("a", [a_membro])
    grupo_b = ExceptionGroup("b", [b_membro])
    _ligar_contexto(a_membro, grupo_b)
    _ligar_contexto(b_membro, grupo_a)

    assert daemon._is_connection_error(grupo_a) is False


def test_aninhamento_profundo_nao_gasta_pilha():
    """O caso silencioso: sem ciclo nenhum, so fundo. A versao recursiva
    gastava um quadro por nivel e morria antes de responder."""
    grupo: BaseException = ValueError("folha")
    for i in range(2000):
        grupo = ExceptionGroup(f"nivel {i}", [grupo])

    assert daemon._is_connection_error(grupo) is False


def test_aninhamento_profundo_com_o_erro_no_fundo():
    folha: BaseException = ConnectionResetError("caiu")
    for i in range(2000):
        folha = ExceptionGroup(f"nivel {i}", [folha])

    assert daemon._is_connection_error(folha) is True


# ── o que ja funcionava, e precisa continuar funcionando ────────────────────

@pytest.mark.parametrize("exc", [
    ConnectionError("perdeu"),
    ConnectionRefusedError("recusou"),
    OSError("erro de so"),
    TimeoutError("estourou"),
])
def test_erros_de_transporte_diretos(exc):
    assert daemon._is_connection_error(exc) is True


@pytest.mark.parametrize("nome", [
    "ConnectError", "ConnectTimeout", "ReadTimeout", "PoolTimeout",
    "RemoteProtocolError", "LocalProtocolError", "NetworkError",
])
def test_erros_do_httpx_reconhecidos_pelo_nome(nome):
    """httpx nao deriva de OSError diretamente, entao o nome e o que resta."""
    tipo = type(nome, (Exception,), {})
    assert daemon._is_connection_error(tipo("x")) is True


def test_erro_de_transporte_no_fundo_da_cadeia_de_causa():
    raiz = ConnectionRefusedError("recusou")
    meio = _ligar_contexto(RuntimeError("embrulho"), raiz)
    topo = _ligar_contexto(ValueError("outro embrulho"), meio)

    assert daemon._is_connection_error(topo) is True


def test_erro_de_verdade_nao_vira_erro_de_conexao():
    """O ponto da funcao: um defeito no trabalho nao pode ser lido como daemon
    caido, ou a CLI reporta a coisa errada e o usuario reinicia o servico a
    toa."""
    assert daemon._is_connection_error(ValueError("dado invalido")) is False


def test_grupo_com_varios_membros_encontra_o_unico_de_transporte():
    grupo = ExceptionGroup("misto", [
        ValueError("a"), KeyError("b"), ConnectionResetError("c"), TypeError("d"),
    ])
    assert daemon._is_connection_error(grupo) is True


def test_grupo_sem_nenhum_de_transporte():
    grupo = ExceptionGroup("misto", [ValueError("a"), KeyError("b")])
    assert daemon._is_connection_error(grupo) is False


def test_grupo_dentro_de_grupo():
    interno = ExceptionGroup("interno", [ConnectionResetError("c")])
    externo = ExceptionGroup("externo", [ValueError("a"), interno])
    assert daemon._is_connection_error(externo) is True


def test_a_funcao_nao_recursiona_mais():
    """Guarda estrutural: a recursao era a causa, e voltar a ela devolve o
    defeito inteiro sem que nenhum dos testes acima precise falhar primeiro."""
    import inspect
    fonte = inspect.getsource(daemon._is_connection_error)
    corpo = fonte.split("\n", 1)[1]
    assert "_is_connection_error(" not in corpo, (
        "a funcao voltou a chamar a si mesma; use a lista de pendencias"
    )

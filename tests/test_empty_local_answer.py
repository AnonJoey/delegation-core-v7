"""O caminho do modelo local nao pode devolver vazio em silencio.

Contexto medido em 02/09/2026: 33 revisoes de codigo foram enfileiradas para o
modelo local e as 10 primeiras voltaram com `status: "done"`, `result: null` e
`error: null`. Nada no registro dizia que tinham falhado. A causa era o modelo
ser "thinking": ele escreve em `reasoning_content` antes de escrever em
`content`, e com um arquivo de 500 linhas no prompt o orcamento de tokens
acabava dentro do raciocinio. A resposta vinha HTTP 200, com `content` vazio.

Medido no llama.cpp desta maquina, gemma-4-12B, mesmo pedido trivial:
  thinking ligado   -> 1045 tokens de completion
  thinking desligado->    4 tokens de completion

Tres defesas, uma por teste abaixo:
  1. o payload desliga o canal de raciocinio por padrao;
  2. se ainda assim vier so raciocinio, ele e devolvido em vez de "";
  3. se vier nada, e erro, e a fila registra erro em vez de sucesso.
"""
from __future__ import annotations

import json

import pytest

from delegation_core import localqueue
from delegation_core.config import Config
from delegation_core.engine import DelegationEngine


# ── 1. o payload ────────────────────────────────────────────────────────────

def _payload_sent(cfg, monkeypatch) -> dict:
    """Roda invoke ate o POST e devolve o corpo que teria sido enviado."""
    import asyncio

    engine = DelegationEngine(cfg)
    capturado: dict = {}

    class _Resposta:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}]}

    async def _post(url, json=None, **kw):          # noqa: A002
        capturado.update(json or {})
        return _Resposta()

    async def _ensure(force=False):
        return True

    monkeypatch.setattr(engine, "ensure_running", _ensure)
    monkeypatch.setattr(engine._async_client, "post", _post)
    asyncio.run(engine.invoke("qualquer coisa", force_local=True))
    return capturado


def test_thinking_desligado_por_padrao(tmp_path, monkeypatch):
    cfg = Config(vault_path=str(tmp_path))
    payload = _payload_sent(cfg, monkeypatch)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_pode_ser_religado(tmp_path, monkeypatch):
    cfg = Config(vault_path=str(tmp_path))
    cfg.llama_enable_thinking = True
    payload = _payload_sent(cfg, monkeypatch)
    assert "chat_template_kwargs" not in payload


# ── 2. a leitura da resposta ────────────────────────────────────────────────

def test_content_normal_vence():
    data = {"choices": [{"message": {"content": " a resposta ",
                                     "reasoning_content": "o raciocinio"},
                         "finish_reason": "stop"}]}
    assert DelegationEngine._answer_of(data) == "a resposta"


def test_content_vazio_cai_no_raciocinio():
    """Um pensamento truncado vale mais para quem chamou do que uma string vazia."""
    data = {"choices": [{"message": {"content": "",
                                     "reasoning_content": "estava pensando que"},
                         "finish_reason": "length"}]}
    assert DelegationEngine._answer_of(data) == "estava pensando que"


def test_content_null_nao_devolve_none():
    """`content: null` devolvia None de uma funcao anotada `-> str`."""
    data = {"choices": [{"message": {"content": None,
                                     "reasoning_content": "algo"},
                         "finish_reason": "length"}]}
    resultado = DelegationEngine._answer_of(data)
    assert isinstance(resultado, str) and resultado == "algo"


def test_os_dois_vazios_e_erro():
    data = {"choices": [{"message": {"content": "", "reasoning_content": ""},
                         "finish_reason": "length"}],
            "usage": {"completion_tokens": 4096}}
    with pytest.raises(RuntimeError) as exc:
        DelegationEngine._answer_of(data)
    # A mensagem tem que carregar a evidencia, senao o proximo a ver isso
    # precisa reproduzir do zero para saber o que aconteceu.
    assert "4096" in str(exc.value)
    assert "length" in str(exc.value)


def test_resposta_sem_choices_e_erro():
    with pytest.raises(RuntimeError):
        DelegationEngine._answer_of({"choices": []})


def _invoke_contra(resposta: dict, cfg, monkeypatch) -> str:
    """invoke() de ponta a ponta contra um corpo de resposta fabricado."""
    import asyncio

    engine = DelegationEngine(cfg)

    class _Resposta:
        status_code = 200

        @staticmethod
        def json():
            return resposta

    async def _post(url, json=None, **kw):          # noqa: A002
        return _Resposta()

    async def _ensure(force=False):
        return True

    monkeypatch.setattr(engine, "ensure_running", _ensure)
    monkeypatch.setattr(engine._async_client, "post", _post)
    return asyncio.run(engine.invoke("qualquer coisa", force_local=True))


def test_invoke_usa_a_leitura_defensiva(tmp_path, monkeypatch):
    """O caminho real tem que passar por _answer_of, nao ler content direto.

    Sem este teste, trocar `self._answer_of(r.json())` de volta por
    `r.json()["choices"][0]["message"]["content"]` passa a suite inteira: os
    testes de _answer_of continuam verdes porque chamam a funcao diretamente.
    Uma mutacao provou exatamente isso antes deste teste existir.
    """
    resposta = {"choices": [{"message": {"content": "",
                                         "reasoning_content": "o raciocinio"},
                             "finish_reason": "length"}]}
    cfg = Config(vault_path=str(tmp_path))
    assert _invoke_contra(resposta, cfg, monkeypatch) == "o raciocinio"


def test_invoke_levanta_quando_nao_veio_resposta(tmp_path, monkeypatch):
    resposta = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 4096}}
    cfg = Config(vault_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="empty message"):
        _invoke_contra(resposta, cfg, monkeypatch)


def test_so_espaco_em_branco_conta_como_vazio():
    data = {"choices": [{"message": {"content": "   \n\t  "},
                         "finish_reason": "stop"}]}
    with pytest.raises(RuntimeError):
        DelegationEngine._answer_of(data)


# ── 3. a fila ───────────────────────────────────────────────────────────────

@pytest.fixture
def fila(tmp_path, monkeypatch):
    monkeypatch.setattr(localqueue, "STORE_PATH", tmp_path / "local_tasks.json")
    return localqueue


def test_resultado_vazio_vira_erro_na_fila(fila):
    rec = fila.submit("faca algo", submitted_by="teste")
    fila.claim_next()
    terminado = fila.finish(rec["id"], result="")

    assert terminado["status"] == "error"
    assert terminado["result"] is None
    assert "empty answer" in terminado["error"]
    assert "llama_enable_thinking" in terminado["error"]


def test_resultado_so_espaco_tambem_vira_erro(fila):
    rec = fila.submit("faca algo", submitted_by="teste")
    fila.claim_next()
    assert fila.finish(rec["id"], result="  \n ")["status"] == "error"


def test_resultado_real_continua_sucesso(fila):
    rec = fila.submit("faca algo", submitted_by="teste")
    fila.claim_next()
    terminado = fila.finish(rec["id"], result="DEFEITO 1\nLINHA: 42")

    assert terminado["status"] == "done"
    assert terminado["error"] is None
    assert terminado["result"].startswith("DEFEITO 1")


def test_erro_explicito_nao_e_sobrescrito(fila):
    """Um erro real do worker tem que chegar ao submetedor como ele veio."""
    rec = fila.submit("faca algo", submitted_by="teste")
    fila.claim_next()
    terminado = fila.finish(rec["id"], error="TimeoutException: 120s")

    assert terminado["status"] == "error"
    assert terminado["error"] == "TimeoutException: 120s"


def test_o_registro_no_disco_reflete_o_erro(fila, tmp_path):
    """Quem le a fila de outro processo tem que ver o mesmo estado."""
    rec = fila.submit("faca algo", submitted_by="teste")
    fila.claim_next()
    fila.finish(rec["id"], result="")

    do_disco = json.loads((tmp_path / "local_tasks.json").read_text())
    alvo = next(t for t in do_disco if t["id"] == rec["id"])
    assert alvo["status"] == "error"


def test_resposta_vazia_nao_e_reprocessada(tmp_path, monkeypatch):
    """Resposta vazia nao e falha transitoria: nao pode cair no laco de retry.

    Antes de EmptyAnswer existir, este caminho era pego pelo `except Exception`
    generico, dormia retry_delay tres vezes (60s no padrao) e terminava com
    "Delegation failed after 3 attempts", que enterra a unica frase que diz o
    que aconteceu. Medido: os dois testes de invoke acima levavam 40s do total
    de 40,13s da suite deste arquivo.
    """
    import asyncio

    from delegation_core.engine import EmptyAnswer

    chamadas = {"n": 0, "dormidas": []}

    class _Resposta:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": ""},
                                 "finish_reason": "length"}],
                    "usage": {"completion_tokens": 4096}}

    async def _post(url, json=None, **kw):          # noqa: A002
        chamadas["n"] += 1
        return _Resposta()

    async def _ensure(force=False):
        return True

    async def _sleep(s):
        chamadas["dormidas"].append(s)

    cfg = Config(vault_path=str(tmp_path))
    engine = DelegationEngine(cfg)
    monkeypatch.setattr(engine, "ensure_running", _ensure)
    monkeypatch.setattr(engine._async_client, "post", _post)
    monkeypatch.setattr("delegation_core.engine.asyncio.sleep", _sleep)

    with pytest.raises(EmptyAnswer):
        asyncio.run(engine.invoke("qualquer coisa", force_local=True))

    assert chamadas["n"] == 1, "o POST foi repetido para uma falha nao transitoria"
    assert chamadas["dormidas"] == [], "dormiu esperando um resultado que nao muda"


def test_calibrate_mede_no_mesmo_regime_da_inferencia(tmp_path, monkeypatch):
    """A calibragem tem que rodar com thinking desligado, como a inferencia.

    _compute_budgets deriva os tetos por tarefa do tok/sec que calibrate mede.
    Se calibrate mede com o canal de raciocinio ligado e invoke roda com ele
    desligado, os tetos descrevem uma maquina que nao e a que atende: eles
    limitam pensamento, nao resposta.
    """
    import asyncio

    capturado: dict = {}

    class _Resposta:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "1\n2\n3"}}],
                    "usage": {"completion_tokens": 40}}

    async def _post(url, json=None, **kw):          # noqa: A002
        capturado.update(json or {})
        return _Resposta()

    async def _ensure(force=False):
        return True

    cfg = Config(vault_path=str(tmp_path))
    engine = DelegationEngine(cfg)
    monkeypatch.setattr(engine, "ensure_running", _ensure)
    monkeypatch.setattr(engine._async_client, "post", _post)
    asyncio.run(engine.calibrate())

    assert capturado["chat_template_kwargs"] == {"enable_thinking": False}

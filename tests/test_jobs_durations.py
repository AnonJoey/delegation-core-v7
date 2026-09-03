"""O historico de duracao dos jobs nao pode perder escrita nem sumir inteiro.

`jobs.submit` da a cada tarefa sua propria thread daemon, e `_record_duration`
fazia leitura-modificacao-escrita do arquivo sem lock nenhum: duas terminando
juntas liam o mesmo estado, cada uma anexava a sua entrada, e a segunda escrita
apagava a primeira. Perda de atualizacao classica, num arquivo cuja unica
funcao e acumular historico.

A escrita tambem nao era atomica. `write_text` trunca o arquivo real primeiro,
entao uma queda no meio deixava um JSON pela metade que `_load_durations` lia
como "nenhum historico", para TODAS as tarefas de uma vez. O sintoma visivel
disso e `task_status` parar de devolver `check_again_in_seconds`, que e
justamente o campo que o AGENT_GUIDE manda o agente usar para se ritmar.

localqueue.py guarda o mesmo tipo de coisa e ja fazia as duas metades certas.
jobs.py era o unico armazem JSON do projeto sem nenhuma das duas.
"""
from __future__ import annotations

import json
import threading

import pytest

from delegation_core import jobs


@pytest.fixture
def durations(tmp_path, monkeypatch):
    caminho = tmp_path / "job_durations.json"
    monkeypatch.setattr(jobs, "_DURATIONS_PATH", caminho)
    return caminho


def test_grava_e_le_de_volta(durations):
    jobs._record_duration("graph_build", 12.34)
    assert jobs._load_durations() == {"graph_build": [12.3]}


def test_mantem_apenas_as_ultimas(durations):
    for i in range(jobs._DURATIONS_KEEP + 5):
        jobs._record_duration("reindex", float(i))
    assert len(jobs._load_durations()["reindex"]) == jobs._DURATIONS_KEEP


def test_escritas_concorrentes_nao_se_perdem(durations):
    """O defeito, reproduzido: sem lock a ultima escrita apaga as outras."""
    quantas = 40
    barreira = threading.Barrier(quantas)

    def grava(i: float) -> None:
        barreira.wait()          # maximiza a sobreposicao
        jobs._record_duration("concorrente", i)

    fios = [threading.Thread(target=grava, args=(float(i),)) for i in range(quantas)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    historico = jobs._load_durations()["concorrente"]
    assert len(historico) == jobs._DURATIONS_KEEP, (
        f"esperado {jobs._DURATIONS_KEEP} entradas retidas, veio {len(historico)}: "
        "escritas se perderam"
    )


def test_tarefas_diferentes_nao_se_apagam(durations):
    quantas = 20
    barreira = threading.Barrier(quantas * 2)

    def grava(nome: str, i: float) -> None:
        barreira.wait()
        jobs._record_duration(nome, i)

    fios = ([threading.Thread(target=grava, args=("a", float(i))) for i in range(quantas)]
            + [threading.Thread(target=grava, args=("b", float(i))) for i in range(quantas)])
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    data = jobs._load_durations()
    assert set(data) == {"a", "b"}, "uma tarefa apagou a outra do arquivo"
    assert len(data["a"]) == jobs._DURATIONS_KEEP
    assert len(data["b"]) == jobs._DURATIONS_KEEP


def test_nao_deixa_arquivo_temporario_para_tras(durations):
    jobs._record_duration("x", 1.0)
    assert not durations.with_suffix(".json.tmp").exists()


def test_arquivo_corrompido_nao_levanta_e_avisa(durations, caplog):
    """Antes: `except Exception: return {}`, mudo. Um truncamento apagava o
    historico de todas as tarefas e nada dizia."""
    import logging

    durations.write_text('{"graph_build": [1.0', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert jobs._load_durations() == {}

    assert "unreadable" in caplog.text


def test_arquivo_ausente_e_silencioso(durations, caplog):
    """Nao existir ainda e o estado normal na primeira execucao, nao um aviso."""
    import logging

    with caplog.at_level(logging.WARNING):
        assert jobs._load_durations() == {}

    assert caplog.text == ""


def test_json_que_nao_e_dicionario_nao_contamina(durations):
    """Uma lista no lugar de um objeto quebraria .get() em typical_seconds."""
    durations.write_text("[1, 2, 3]", encoding="utf-8")
    assert jobs._load_durations() == {}
    assert jobs.typical_seconds("qualquer") is None


def test_typical_seconds_usa_a_mediana(durations):
    durations.write_text(json.dumps({"t": [10.0, 20.0, 90.0]}), encoding="utf-8")
    assert jobs.typical_seconds("t") == 20.0


def test_typical_seconds_sem_historico(durations):
    assert jobs.typical_seconds("nunca_rodou") is None


def test_entradas_nao_numericas_sao_descartadas(durations):
    durations.write_text(json.dumps({"t": ["lixo", 4.0, None, 6.0]}), encoding="utf-8")
    assert jobs.typical_seconds("t") == 5.0


def test_escrita_que_falha_no_meio_nao_destroi_o_historico(durations, monkeypatch):
    """A propriedade que a escrita atomica realmente da.

    `write_text` trunca o arquivo real ANTES de escrever. Uma falha no meio
    (disco cheio, queda) deixava um JSON pela metade, que _load_durations lia
    como "nenhum historico" para TODAS as tarefas de uma vez. Com tmp + replace,
    o arquivo bom continua intocado ate o replace, que e atomico.

    Sem este teste a mutacao que troca tmp+replace por write_text sobrevive: as
    outras onze passam identicas nos dois casos.
    """
    jobs._record_duration("antes", 5.0)
    bom = durations.read_text(encoding="utf-8")

    real = jobs.json.dump

    def explode(obj, fh, **kw):
        real(obj, fh, **kw)      # escreve de verdade, para provar que o tmp foi usado
        raise OSError("No space left on device")

    monkeypatch.setattr(jobs.json, "dump", explode)
    jobs._record_duration("depois", 9.0)      # engolido: telemetria nunca derruba job

    assert durations.read_text(encoding="utf-8") == bom, \
        "a escrita que falhou destruiu o historico que ja estava no disco"
    assert jobs._load_durations() == {"antes": [5.0]}

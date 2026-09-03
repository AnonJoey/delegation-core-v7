"""O lock do rebuild podia ser tomado por dois processos ao mesmo tempo.

`graph_hook_rebuild` roda destacado, disparado pelo hook de post-commit do git.
O lock existe para que dois commits seguidos nao ponham dois rebuilds
escrevendo no mesmo `graphs_dir/<nome>/`.

Medido em 03/09/2026: o lock nasce em DUAS syscalls, `os.open(O_CREAT|O_EXCL)`
e depois `os.write(pid)`. Entre elas o arquivo existe e esta vazio, e
`_lock_is_stale` fazia `int("")`, caia no ValueError e devolvia True. Um
segundo processo nessa janela declarava o lock obsoleto, apagava e tomava para
si, e os dois rebuilds passavam a escrever no mesmo diretorio.

`graph_hook_rebuild.py` era um dos nove modulos do nucleo sem nenhum teste.
"""
from __future__ import annotations

import os
import time

import pytest

from delegation_core import graph_hook_rebuild as ghr


@pytest.fixture
def lock(tmp_path):
    return tmp_path / ".rebuild.lock"


# ── o defeito ───────────────────────────────────────────────────────────────

def test_lock_recem_criado_e_vazio_nao_e_obsoleto(lock):
    """A janela entre O_CREAT e o write do PID."""
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)                      # existe, vazio, como no instante real

    assert ghr._lock_is_stale(lock) is False, (
        "um segundo processo tomaria o lock que outro acabou de criar"
    )


def test_lock_vazio_e_velho_continua_obsoleto(lock):
    """Vazio para sempre e abandono, e o corte de idade tem que pegar."""
    lock.write_text("", encoding="utf-8")
    antigo = time.time() - ghr._LOCK_MAX_AGE_SECONDS - 60
    os.utime(lock, (antigo, antigo))

    assert ghr._lock_is_stale(lock) is True


# ── o resto do contrato ─────────────────────────────────────────────────────

def test_lock_do_proprio_processo_esta_vivo(lock):
    lock.write_text(str(os.getpid()), encoding="utf-8")
    assert ghr._lock_is_stale(lock) is False


def test_lock_de_pid_morto_e_obsoleto(lock):
    """PID que nao existe: o dono morreu sem limpar."""
    morto = 999_999_999          # acima de qualquer pid_max realista
    lock.write_text(str(morto), encoding="utf-8")
    assert ghr._lock_is_stale(lock) is True


def test_lock_velho_e_obsoleto_mesmo_com_pid_vivo(lock):
    """O corte de idade vale mesmo quando o PID foi reaproveitado."""
    lock.write_text(str(os.getpid()), encoding="utf-8")
    antigo = time.time() - ghr._LOCK_MAX_AGE_SECONDS - 1
    os.utime(lock, (antigo, antigo))
    assert ghr._lock_is_stale(lock) is True


def test_lock_com_lixo_e_obsoleto(lock):
    """Conteudo que nao e numero nem vazio: arquivo corrompido."""
    lock.write_text("nao é um pid", encoding="utf-8")
    assert ghr._lock_is_stale(lock) is True


def test_lock_que_nao_existe_e_obsoleto(tmp_path):
    assert ghr._lock_is_stale(tmp_path / "nao_existe.lock") is True


# ── limites de recurso, best-effort ─────────────────────────────────────────

def test_limites_sem_variavel_de_ambiente_nao_levantam(monkeypatch):
    monkeypatch.delenv("DELEGATION_CORE_GRAPH_REBUILD_MEMORY_LIMIT_MB", raising=False)
    ghr._apply_resource_limits()


def test_limite_de_memoria_invalido_e_ignorado(monkeypatch):
    """Um valor errado na variavel nao pode derrubar o rebuild."""
    monkeypatch.setenv("DELEGATION_CORE_GRAPH_REBUILD_MEMORY_LIMIT_MB", "nao-e-numero")
    ghr._apply_resource_limits()


def test_sem_argumento_devolve_codigo_de_uso(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["graph_hook_rebuild"])
    assert ghr.main() == 2
    assert "usage:" in capsys.readouterr().err

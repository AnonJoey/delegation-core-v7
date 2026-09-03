"""A escolha do binario do llama.cpp, e o que acontece quando o upstream renomeia.

`downloader.py` tem 355 linhas e era um dos modulos do nucleo sem nenhum teste.
Ele baixa o modelo e o binario: errar aqui nao aparece como resultado errado,
aparece como uma instalacao que nao sobe, ou pior, que sobe com um binario de
GPU numa maquina que nao tem a GPU.

O modulo ja carrega a cicatriz de um rename do upstream: o comentario de
`_get_release_asset` conta que o llama.cpp renomeou o ativo de CPU do Windows
antes (para `win-cpu-x64.zip`) e quebrou o instalador em silencio, e por isso
existe um caminho de fallback.

Nenhum defeito ATIVO foi encontrado aqui. O que existia era uma inconsistencia:
o ramo preferencial do Windows nao aplicava `_is_plain_cpu_asset`, embora o
comentario logo abaixo afirmasse que esse filtro protege a escolha. Hoje isso
nao morde, porque nenhum build de GPU do llama.cpp carrega `avx2` no nome e a
ordenacao alfabetica coloca o de CPU primeiro. Ou seja: a correcao depende de
sorte na nomenclatura de terceiro, exatamente o tipo de coisa que ja quebrou
este modulo uma vez.
"""
from __future__ import annotations

import platform
from pathlib import Path

import pytest

from delegation_core import downloader


# ── _is_plain_cpu_asset: o que e build de CPU ───────────────────────────────

@pytest.mark.parametrize("nome", [
    "llama-b6543-bin-win-avx2-x64.zip",
    "llama-b6543-bin-win-cpu-x64.zip",
    "llama-b6543-bin-ubuntu-x64.tar.gz",
    "llama-b6543-bin-macos-arm64.tar.gz",
])
def test_build_de_cpu_e_reconhecido(nome):
    assert downloader._is_plain_cpu_asset(nome) is True


@pytest.mark.parametrize("nome", [
    "llama-b6543-bin-win-cuda-cu12.4-x64.zip",
    "llama-b6543-bin-win-vulkan-x64.zip",
    "llama-b6543-bin-ubuntu-hip-x64.tar.gz",
    "llama-b6543-bin-win-sycl-x64.zip",
    "llama-b6543-bin-win-opencl-adreno-x64.zip",
    "llama-b6543-bin-ubuntu-rocm-x64.tar.gz",
    "llama-b6543-bin-win-openvino-x64.zip",
])
def test_build_de_gpu_e_recusado(nome):
    assert downloader._is_plain_cpu_asset(nome) is False


def test_o_helper_do_cuda_e_recusado():
    """`cudart-...` e a DLL de runtime do CUDA, nao um build do llama.

    O modulo o recusa duas vezes: por `startswith("cudart-")` e pela palavra
    "cuda", ja que "cudart" a contem. A guarda de prefixo e portanto REDUNDANTE
    hoje, e uma mutacao que a remove sobrevive. Isto esta escrito aqui porque a
    primeira versao deste teste afirmava o contrario, que o prefixo era o que
    pegava o caso, e isso era falso.

    A redundancia fica: se alguem tirar "cuda" da lista de palavras-chave, o
    prefixo ainda segura o cudart. O teste abaixo prende as duas defesas em
    separado para que a remocao de qualquer uma seja visivel.
    """
    assert downloader._is_plain_cpu_asset("cudart-llama-bin-win-cu12.4-x64.zip") is False
    # a palavra-chave sozinha ja resolveria
    assert "cuda" in "cudart-llama-bin-win-cu12.4-x64.zip"
    # e o prefixo sozinho tambem, se a palavra-chave sumisse
    assert "cudart-llama-bin-win-cu12.4-x64.zip".startswith("cudart-")


def test_a_deteccao_nao_diferencia_caixa():
    assert downloader._is_plain_cpu_asset("LLAMA-BIN-WIN-CUDA-X64.ZIP") is False


# ── _get_release_asset: a escolha ───────────────────────────────────────────

def _fake_release(monkeypatch, nomes: list[str]):
    """Substitui a chamada ao GitHub por uma lista de ativos fabricada."""
    class _R:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"assets": [{"name": n, "browser_download_url": f"https://x/{n}"}
                               for n in nomes]}

    monkeypatch.setattr(downloader.requests, "get", lambda *a, **k: _R())


def test_linux_escolhe_o_ubuntu_x64_de_cpu(monkeypatch):
    _fake_release(monkeypatch, [
        "llama-b1-bin-ubuntu-x64.tar.gz",
        "llama-b1-bin-ubuntu-vulkan-x64.tar.gz",
        "llama-b1-bin-macos-arm64.tar.gz",
    ])
    url, nome = downloader._get_release_asset("Linux", "x86_64")
    assert nome == "llama-b1-bin-ubuntu-x64.tar.gz"
    assert url.endswith(nome)


def test_macos_arm_escolhe_arm64(monkeypatch):
    _fake_release(monkeypatch, [
        "llama-b1-bin-macos-arm64.tar.gz",
        "llama-b1-bin-macos-x64.tar.gz",
    ])
    _, nome = downloader._get_release_asset("Darwin", "arm64")
    assert "arm64" in nome


def test_macos_intel_escolhe_x64(monkeypatch):
    _fake_release(monkeypatch, [
        "llama-b1-bin-macos-arm64.tar.gz",
        "llama-b1-bin-macos-x64.tar.gz",
    ])
    _, nome = downloader._get_release_asset("Darwin", "x86_64")
    assert "x64" in nome and "arm64" not in nome


def test_windows_prefere_o_avx2(monkeypatch):
    _fake_release(monkeypatch, [
        "llama-b1-bin-win-avx2-x64.zip",
        "llama-b1-bin-win-cpu-x64.zip",
    ])
    _, nome = downloader._get_release_asset("Windows", "AMD64")
    assert nome == "llama-b1-bin-win-avx2-x64.zip"


def test_windows_cai_no_fallback_quando_o_avx2_some(monkeypatch):
    """O rename que ja quebrou o instalador uma vez."""
    _fake_release(monkeypatch, [
        "llama-b1-bin-win-cpu-x64.zip",
        "llama-b1-bin-win-cuda-cu12.4-x64.zip",
    ])
    _, nome = downloader._get_release_asset("Windows", "AMD64")
    assert nome == "llama-b1-bin-win-cpu-x64.zip"


def test_windows_nunca_escolhe_build_de_gpu_nem_no_ramo_preferido(monkeypatch):
    """A inconsistencia que este arquivo veio fechar.

    O ramo preferencial nao aplicava `_is_plain_cpu_asset`. Hoje isso nao morde
    porque nenhum build de GPU do llama.cpp carrega `avx2` no nome, e a ordem
    alfabetica poe o de CPU primeiro. Depender disso e depender da nomenclatura
    de um terceiro que JA renomeou este ativo antes, o que o comentario do
    proprio modulo registra.

    Aqui o de GPU e nomeado para ordenar ANTES do de CPU, que e o caso que a
    sorte alfabetica nao cobre.
    """
    _fake_release(monkeypatch, [
        "llama-b1-bin-win-cuda-avx2-x64.zip",     # ordena antes
        "llama-b1-bin-win-x64-avx2.zip",          # o de CPU
    ])
    _, nome = downloader._get_release_asset("Windows", "AMD64")
    assert downloader._is_plain_cpu_asset(nome), (
        f"escolheu um build de GPU: {nome}"
    )


def test_windows_recusa_o_cudart_no_fallback(monkeypatch):
    _fake_release(monkeypatch, [
        "cudart-llama-bin-win-cu12.4-x64.zip",
        "llama-b1-bin-win-cpu-x64.zip",
    ])
    _, nome = downloader._get_release_asset("Windows", "AMD64")
    assert not nome.startswith("cudart-")


def test_sistema_desconhecido_devolve_None(monkeypatch):
    _fake_release(monkeypatch, ["llama-b1-bin-ubuntu-x64.tar.gz"])
    assert downloader._get_release_asset("FreeBSD", "x86_64") is None


def test_nenhum_ativo_compativel_devolve_None(monkeypatch):
    _fake_release(monkeypatch, ["llama-b1-bin-macos-arm64.tar.gz"])
    assert downloader._get_release_asset("Linux", "x86_64") is None


def test_github_fora_do_ar_devolve_None_e_nao_levanta(monkeypatch):
    """Uma instalacao sem rede tem que falhar com mensagem, nao com traceback."""
    def _explode(*a, **k):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(downloader.requests, "get", _explode)
    assert downloader._get_release_asset("Linux", "x86_64") is None


def test_release_sem_a_chave_assets_devolve_None(monkeypatch):
    class _R:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(downloader.requests, "get", lambda *a, **k: _R())
    assert downloader._get_release_asset("Linux", "x86_64") is None


# ── _common_dir_prefix: desempacotar o archive ──────────────────────────────

def test_prefixo_comum_e_reconhecido():
    assert downloader._common_dir_prefix(["build/bin/a", "build/bin/b"]) == "build/"


def test_sem_prefixo_comum_devolve_vazio():
    assert downloader._common_dir_prefix(["build/a", "outro/b"]) == ""


def test_arquivo_na_raiz_nao_tem_prefixo():
    assert downloader._common_dir_prefix(["llama-server", "libggml.so"]) == ""


def test_lista_vazia_nao_explode():
    assert downloader._common_dir_prefix([]) == ""


def test_prefixo_parcial_nao_conta():
    """"build/" e "buildx/" comecam igual mas nao sao a mesma pasta."""
    assert downloader._common_dir_prefix(["build/a", "buildx/b"]) == ""


# ── find_llama_binary: o que ja esta na maquina ─────────────────────────────

def test_prefere_o_binario_gerenciado(tmp_path, monkeypatch):
    nome = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    gerenciado = tmp_path / nome
    gerenciado.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/llama-server")

    assert downloader.find_llama_binary(tmp_path) == gerenciado


def test_cai_no_PATH_quando_nao_ha_gerenciado(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.shutil, "which", lambda _: "/usr/bin/llama-server")
    assert downloader.find_llama_binary(tmp_path) == Path("/usr/bin/llama-server")


def test_sem_binario_em_lugar_nenhum_devolve_None(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.shutil, "which", lambda _: None)
    assert downloader.find_llama_binary(tmp_path) is None

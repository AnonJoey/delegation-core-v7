"""Contencao dos resolvedores de import, consolidada num helper so.

Antes existia UMA contencao neste arquivo, `_contained_in_package`, so para o
`exports` de package.json, enquanto os resolvedores de include em C, de require
em Lua e de import em JS/TS nao tinham nenhuma. Os dois primeiros foram fechados
nos commits anteriores; este traz o mecanismo comum para o terceiro.

Medido, um import relativo com travessia sai do repositorio varrido:

    import "../../fora"   ->  <acima do repo>/fora.ts    resolve e vira no
    import "../../x.txt"  ->  <acima do repo>/x.txt      idem

DIVIDA CONHECIDA, a mesma do include em C e escrita no helper: os doze
chamadores de hoje nao tem o root a mao, porque os extractors sao invocados por
um protocolo de handler de assinatura fixa. O parametro esta pronto e testado
nos tres resolvedores; falta o fio. Os testes abaixo cobrem os dois lados: com
root contem, sem root o comportamento de hoje nao muda.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core.graph.extractors.resolution import (_dentro_do_root,
                                                         _resolve_c_include_path,
                                                         _resolve_js_import_target,
                                                         _resolve_js_module_path)


@pytest.fixture
def arvore(tmp_path: Path) -> tuple[Path, Path]:
    """(<acima>, <repo>) com um alvo tentador fora do repo."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "local.ts").write_text("export const a = 1")
    (repo / "comum.ts").write_text("export const b = 2")
    (tmp_path / "fora.ts").write_text("export const segredo = 3")
    (tmp_path / "dados.txt").write_text("segredo")
    return tmp_path, repo


# ── o helper ─────────────────────────────────────────────────────────────────

def test_sem_root_o_helper_nao_opina(tmp_path: Path):
    assert _dentro_do_root(tmp_path / "qualquer", None) is True


def test_com_root_separa_dentro_de_fora(arvore):
    acima, repo = arvore
    assert _dentro_do_root(repo / "src" / "local.ts", repo) is True
    assert _dentro_do_root(acima / "fora.ts", repo) is False


def test_root_inalcancavel_nega(tmp_path: Path):
    """Guarda de contencao nega quando nao consegue decidir; nunca levanta."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    assert _dentro_do_root(a / "x", tmp_path / "raiz") is False


# ── JS/TS ────────────────────────────────────────────────────────────────────

def test_js_travessia_e_contida_com_root(arvore):
    acima, repo = arvore
    assert _resolve_js_module_path("../../fora", repo / "src", root=repo) is None


def test_js_import_legitimo_atravessa_com_root(arvore):
    acima, repo = arvore
    assert _resolve_js_module_path("./local", repo / "src", root=repo) is not None
    assert _resolve_js_module_path("../comum", repo / "src", root=repo) is not None


def test_js_sem_root_o_comportamento_de_hoje_nao_muda(arvore):
    """A correcao nao pode mexer no que os chamadores atuais recebem."""
    acima, repo = arvore
    assert _resolve_js_module_path("./local", repo / "src") is not None
    assert _resolve_js_module_path("../../fora", repo / "src") is not None


def test_js_o_alvo_completo_aceita_root(arvore):
    acima, repo = arvore
    arq = str(repo / "src" / "app.ts")
    _, caminho = _resolve_js_import_target("../../fora", arq, root=repo)
    assert caminho is None, "o alvo fora do repo nao pode virar caminho resolvido"

    _, caminho_ok = _resolve_js_import_target("./local", arq, root=repo)
    assert caminho_ok is not None


def test_js_arquivo_nao_fonte_fora_do_repo_tambem_e_contido(arvore):
    """O resolvedor aceita qualquer arquivo existente, inclusive .txt, entao a
    contencao tem que valer para eles tambem."""
    acima, repo = arvore
    assert _resolve_js_module_path("../../dados.txt", repo / "src", root=repo) is None


# ── C, que passou a usar o mesmo helper ──────────────────────────────────────

def test_c_continua_contido_pelo_helper(arvore):
    acima, repo = arvore
    (repo / "src" / "h.h").write_text("x")
    (acima / "fora.h").write_text("x")
    arq = str(repo / "src" / "main.c")
    assert _resolve_c_include_path("h.h", arq, root=repo) is not None
    assert _resolve_c_include_path("../../fora.h", arq, root=repo) is None
    assert _resolve_c_include_path("/etc/passwd", arq) is None

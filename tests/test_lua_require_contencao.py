"""Um require() de Lua nao pode resolver para fora do repositorio.

O nome do modulo vira caminho por `raw_module.replace(".", "/")`. Um ponto no
INICIO vira barra no inicio, e `probe / rel` entao DESCARTA o probe e sonda a
raiz do sistema de arquivos. Medido, com um arquivo real fora do projeto:

    require(".tmp.luatest.segredo")  ->  /tmp/luatest/segredo.lua

Mesma forma do #include absoluto do commit anterior, no mesmo arquivo:
`Path(dir) / "/x"` nao concatena, substitui.

Aqui, ao contrario do include em C, a guarda fecha o vetor INTEIRO: todo ponto
vira barra antes da checagem, entao `..` nunca sobrevive e nao ha travessia para
cima a conter. Nao precisa do root do scan, e ha teste prendendo essa premissa.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core.graph.extractors.base import _make_id
from delegation_core.graph.extractors.resolution import _resolve_lua_import_target


@pytest.fixture
def projeto(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "vizinho.lua").write_text("return 1")
    (src / "pkg" / "b.lua").write_text("return 1")
    (src / "pkg" / "init.lua").write_text("return 1")
    (tmp_path / "acima.lua").write_text("return 1")
    return src


def _req(projeto: Path, modulo: str) -> str:
    return _resolve_lua_import_target(modulo, str(projeto / "main.lua"))


def test_modulo_com_ponto_inicial_nao_sonda_a_raiz(projeto: Path, tmp_path: Path):
    """O caso concreto: um arquivo .lua de verdade fora do projeto."""
    fora = tmp_path.parent / "fora_do_projeto.lua"
    fora.write_text("return 1")
    try:
        modulo = "." + str(fora)[1:].replace("/", ".").removesuffix(".lua")
        assert _req(projeto, modulo) != _make_id(str(fora)), \
            "resolveu para um arquivo fora do repositorio varrido"
    finally:
        fora.unlink()


@pytest.mark.parametrize("modulo", [".x", "..x", "...x", ".tmp.luatest.segredo"])
def test_ponto_inicial_cai_no_fallback_documentado(projeto: Path, modulo):
    """Nome de modulo Lua e identificadores separados por ponto, entao um que
    comece com ponto ja nao e valido. A saida e a de "nada casou" (#1075), que
    preserva a aresta para a passagem de resolucao de simbolos."""
    assert _req(projeto, modulo) == _make_id(modulo)


def test_pontos_no_meio_continuam_sendo_separador(projeto: Path):
    esperado = _make_id(str(projeto / "pkg" / "b.lua"))
    assert _req(projeto, "pkg.b") == esperado


def test_arquivo_vizinho_resolve(projeto: Path):
    assert _req(projeto, "vizinho") == _make_id(str(projeto / "vizinho.lua"))


def test_init_lua_do_diretorio_resolve(projeto: Path):
    """A pasta `pkg` tem init.lua; `pkg/b.lua` ganha por vir antes na ordem, e
    um modulo que so tem init cai no segundo laco."""
    assert _req(projeto, "pkg") == _make_id(str(projeto / "pkg" / "init.lua"))


def test_sobe_niveis_para_achar_a_raiz_do_pacote(projeto: Path, tmp_path: Path):
    """A caminhada para cima, que o docstring declara, nao pode ser afetada."""
    assert _req(projeto, "acima") == _make_id(str(tmp_path / "acima.lua"))


def test_modulo_inexistente_cai_no_fallback(projeto: Path):
    assert _req(projeto, "naoexiste") == _make_id("naoexiste")


def test_modulo_vazio(projeto: Path):
    assert _req(projeto, "") == ""


def test_a_travessia_por_pontos_e_impossivel_por_construcao():
    """A premissa que dispensa o root do scan: como TODO ponto vira barra, nao
    sobra nenhum componente `..` para subir. Se alguem mudar a substituicao,
    este teste avisa que a guarda deixou de ser suficiente."""
    for modulo in ["a/../../../etc/passwd", "a..b..c", "...x", ".."]:
        rel = modulo.replace(".", "/")
        assert ".." not in rel, f"{modulo!r} deixou um '..' em {rel!r}"

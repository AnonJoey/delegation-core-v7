"""Um diretorio nao pode ser apresentado como modulo resolvido.

`_resolve_js_import_path` devolve o candidato intacto quando nada casa, que e o
comportamento fantasma usado de proposito. Para caminho inexistente isso e
honesto: ele nao e arquivo e ninguem o confunde. Para um DIRETORIO nao e, porque
o caminho EXISTE, e quem conferir com `exists()` em vez de `is_file()` aceita uma
pasta como modulo. Medido, antes da guarda os tres voltavam pela mesma porta:

    import "./comp"       -> src/comp/index.ts   ARQUIVO
    import "./vazio"      -> src/vazio           DIRETORIO
    import "./naoexiste"  -> src/naoexiste       inexistente

Import de diretorio sem arquivo de indice nao resolve nem no Node nem no tsc, e o
comentario do ramo de fallback diz que o balde "ref" e para "an external package
(or a dangling local path)" — que e o que ele e.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core.graph.extractors.resolution import (_resolve_js_import_path,
                                                         _resolve_js_import_target,
                                                         _resolve_js_module_path)


@pytest.fixture
def projeto(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "comp").mkdir(parents=True)
    (src / "vazio").mkdir()
    (src / "so_subpasta" / "dentro").mkdir(parents=True)
    (src / "comp" / "index.ts").write_text("export const a = 1")
    (src / "a.ts").write_text("export const b = 2")
    return src


def test_diretorio_sem_indice_nao_e_modulo(projeto: Path):
    assert _resolve_js_module_path("./vazio", projeto) is None
    assert _resolve_js_module_path("./so_subpasta", projeto) is None


def test_diretorio_com_indice_continua_resolvendo(projeto: Path):
    achado = _resolve_js_module_path("./comp", projeto)
    assert achado is not None and achado.name == "index.ts"


def test_arquivo_continua_resolvendo(projeto: Path):
    achado = _resolve_js_module_path("./a", projeto)
    assert achado is not None and achado.name == "a.ts"


def test_caminho_inexistente_mantem_o_id_derivado_do_caminho(projeto: Path):
    """Este caso NAO foi alterado de proposito: mudar o id de todo local
    pendurado mexeria no grafo inteiro do usuario. Preso aqui para que a
    diferenca fique visivel em vez de virar surpresa."""
    achado = _resolve_js_module_path("./naoexiste", projeto)
    assert achado is not None, "segue devolvendo o caminho fantasma"
    assert not achado.exists()


def test_o_alvo_do_import_de_diretorio_nao_carrega_caminho(projeto: Path):
    """A ponta que o resto do extractor consome."""
    alvo = _resolve_js_import_target("./vazio", str(projeto / "app.ts"))
    assert alvo is not None
    nid, caminho = alvo
    assert caminho is None, "um diretorio nao pode sair como caminho resolvido"
    assert nid.startswith("ref"), "vai para o balde de nao resolvido, como o comentario descreve"


def test_o_alvo_de_arquivo_de_verdade_carrega_o_caminho(projeto: Path):
    alvo = _resolve_js_import_target("./comp", str(projeto / "app.ts"))
    assert alvo is not None
    _, caminho = alvo
    assert caminho is not None and caminho.is_file()


def test_o_helper_de_caminho_continua_devolvendo_o_diretorio(projeto: Path):
    """A guarda foi posta na camada que promete "source file", e nao no helper
    de caminho, que outros chamadores usam com is_file() proprio. Se alguem
    mover a guarda para dentro do helper, este teste avisa."""
    assert _resolve_js_import_path(projeto / "vazio").is_dir()

"""Um #include nao pode resolver para fora do repositorio.

`_contained_in_package`, neste MESMO arquivo, existe para barrar alvo que escapa
do diretorio do pacote, e cita "../../../etc/passwd" no proprio docstring.
`_resolve_c_include_path` nao tinha guarda nenhuma. Medido:

    #include "/etc/passwd"           ->  /etc/passwd
    #include "../../../etc/passwd"   ->  /etc/passwd

O caminho absoluto e o pior dos dois e o mais facil de fechar: `Path(dir) / "/x"`
DESCARTA o `dir` inteiro, entao "resolver relativo ao arquivo que inclui", que e
o que o docstring promete, nunca chegava a acontecer.

DIVIDA CONHECIDA, com teste que a documenta: a travessia por `..` so fecha com o
root do scan, e os dois chamadores de hoje nao o tem, porque `_import_c` e
passado como `import_handler` numa LanguageConfig com assinatura fixa. O
parametro `root` ja existe e e testado aqui; falta fiar.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core.graph.extractors.resolution import _resolve_c_include_path


@pytest.fixture
def projeto(tmp_path: Path) -> Path:
    (tmp_path / "src" / "inc").mkdir(parents=True)
    (tmp_path / "src" / "local.h").write_text("x")
    (tmp_path / "src" / "inc" / "h.h").write_text("x")
    (tmp_path / "comum.h").write_text("x")
    return tmp_path


def _inclui(projeto: Path, raw: str, root: Path | None = None):
    return _resolve_c_include_path(raw, str(projeto / "src" / "main.c"), root)


def test_absoluto_e_recusado(projeto: Path):
    """Reachable: o extractor tira as aspas e entrega o caminho cru."""
    assert _inclui(projeto, "/etc/passwd") is None
    assert _inclui(projeto, "/etc/hostname") is None


def test_absoluto_que_existe_dentro_do_projeto_tambem_e_recusado(projeto: Path):
    """A regra e sobre a FORMA do include, nao sobre o destino: include entre
    aspas e relativo por definicao."""
    assert _inclui(projeto, str(projeto / "src" / "local.h")) is None


def test_relativo_normal_continua_resolvendo(projeto: Path):
    assert _inclui(projeto, "local.h") == projeto / "src" / "local.h"
    assert _inclui(projeto, "inc/h.h") == projeto / "src" / "inc" / "h.h"


def test_subir_um_nivel_dentro_do_projeto_continua_valendo(projeto: Path):
    """`..` e legitimo em C e nao pode ser cortado por contagem."""
    assert _inclui(projeto, "../comum.h") == projeto / "comum.h"


def test_inexistente_e_vazio_seguem_devolvendo_none(projeto: Path):
    assert _inclui(projeto, "naoexiste.h") is None
    assert _inclui(projeto, "") is None


# ── o parametro root, pronto e testado, ainda sem chamador ───────────────────

def test_com_root_a_travessia_por_pontos_e_fechada(projeto: Path):
    """A parte que fecha o vetor que sobra. Passa hoje so quando alguem informa
    o root; os chamadores de producao ainda nao informam."""
    assert _inclui(projeto, "../../../../../../etc/passwd", root=projeto) is None


def test_com_root_o_include_legitimo_atravessa(projeto: Path):
    assert _inclui(projeto, "../comum.h", root=projeto) == projeto / "comum.h"
    assert _inclui(projeto, "local.h", root=projeto) == projeto / "src" / "local.h"


def test_sem_root_a_travessia_segue_aberta(projeto: Path):
    """A divida, escrita como teste para que ela apareca em vez de ser esquecida.
    No dia em que alguem fiar o root ate os chamadores, este teste falha e a
    pessoa vem ler o comentario que explica o porque."""
    fora = _inclui(projeto, "../../../../../../etc/passwd")
    assert fora is None or not str(fora).startswith(str(projeto)), \
        "sem root nao ha como conter; se isto mudou, atualize a nota da divida"

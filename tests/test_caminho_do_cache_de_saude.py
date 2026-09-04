"""O cache de saude morava num caminho construido a partir de Path.home().

Sete lugares montam `~/.delegation_core/vault_health.json`. SEIS o constroem com
`Path.home() / ".delegation_core"` dentro de uma funcao, e UM
(`installer.py:979`) usa `CONFIG_DIR`. A diferenca nao e estetica: um caminho
montado em tempo de chamada a partir de Path.home() nao pode ser reapontado por
ninguem.

## COMO APARECEU, e a medicao que fechou o caso

Notei que `vault_health.json` dizia `broken_links: 2, orphans: 1` as 03:36 e que
`vault_health_detail()` dizia `0, 0` as 03:40, sobre o mesmo vault. Conferido:
NENHUMA nota do vault foi tocada entre 03:30 e 03:45, e o log do daemon nao tem
entrada as 03:36.

O que rodou as 03:36 foi a SUITE DE TESTES. Provado isolando um arquivo:

    md5 antes  : 79acc9c5c1b63bb877ad552e0e743d1e
    pytest tests/test_frontmatter_valido.py -> 6 passed
    md5 depois : b71f967007d2f31ce1c7b6d85676223c
    conteudo   : vault_path=/tmp/pytest-of-joey/.../test_o_detalhe_nomeia_o_arquiv0
                 total_notes=2

Um teste sobre um vault temporario de DUAS notas gravou o resultado dele no
estado real do usuario. E tres dos seis lugares fazem `unlink()`, entao um teste
que escreve nota APAGA o cache de saude real.

## O QUE O CONFTEST PROMETE, e por que nao alcancava

O fixture `_sem_escrita_no_estado_real` diz que "reaponta todo caminho de estado
do usuario para um diretorio temporario", e funciona para `_DURATIONS_PATH`,
`STORE_PATH`, `SESSIONS_DIR` e `REGISTRY`, que sao CONSTANTES DE MODULO: dá para
reatribuir o nome. Um caminho montado dentro da funcao nao tem nome para
reatribuir.

## O QUE JA ESTAVA CERTO

Servir numero errado nao chegava a acontecer: o cache carrega `vault_path` e
`get_health_summary` recusa um cache de outro vault. Essa guarda foi escrita
depois de "two tests over different temp vaults returned identical health", ou
seja, trataram o SINTOMA da mesma causa e deixaram o caminho fixo.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SRC = RAIZ / "src" / "delegation_core"


def _construcoes_com_home() -> list[str]:
    """Onde o caminho do cache e montado a partir de Path.home(), por AST.

    A primeira versao lia o TEXTO da linha e acusava a propria docstring que
    CITA a forma antiga como exemplo. Uma varredura que nao distingue codigo de
    prosa acusa quem esta explicando o defeito.
    """
    achados = []
    for p in sorted(SRC.rglob("*.py")):
        try:
            arvore = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, ast.BinOp) or not isinstance(no.op, ast.Div):
                continue
            texto = ast.unparse(no)
            if "vault_health.json" in texto and "Path.home()" in texto:
                achados.append(f"{p.relative_to(RAIZ)}:{no.lineno}")
    return achados


def test_ninguem_monta_o_caminho_do_cache_a_partir_de_home():
    faltando = _construcoes_com_home()
    assert not faltando, (
        "caminho de estado construido em tempo de chamada, fora do alcance de "
        "qualquer redirecionamento:\n  " + "\n  ".join(faltando)
        + "\nUse a constante de modulo derivada de CONFIG_DIR."
    )


def test_a_varredura_enxerga_a_forma_que_ela_proibe():
    """Uma varredura quebrada passaria o teste acima sem verificar nada.

    E prova tambem que ela NAO acusa prosa: a segunda arvore tem a mesma forma
    dentro de uma docstring, e nao pode contar.
    """
    codigo = ast.parse('p = Path.home() / ".delegation_core" / "vault_health.json"')
    achou = [n for n in ast.walk(codigo)
             if isinstance(n, ast.BinOp) and "vault_health.json" in ast.unparse(n)
             and "Path.home()" in ast.unparse(n)]
    assert achou, "a varredura nao enxerga a forma que ela proibe"

    prosa = ast.parse('"""exemplo: Path.home() / \'.delegation_core\' / \'vault_health.json\'"""')
    assert not [n for n in ast.walk(prosa) if isinstance(n, ast.BinOp)]


def test_o_caminho_segue_o_config_dir_em_tempo_de_chamada(tmp_path, monkeypatch):
    """Funcao e nao constante: uma constante seria copiada no import de cada
    modulo, que e a armadilha que o conftest ja documenta para CONFIG_DIR."""
    from delegation_core import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)

    assert config_mod.vault_health_cache() == tmp_path / "vault_health.json"


def test_a_saude_escreve_onde_o_conftest_aponta(tmp_path):
    """O teste que falharia hoje: prova que o cache NAO vai para a casa real."""
    from delegation_core.config import Config
    from delegation_core.vault import VaultManager

    real = pathlib.Path.home() / ".delegation_core" / "vault_health.json"
    antes = real.read_bytes() if real.exists() else None

    (tmp_path / "Sessions").mkdir()
    (tmp_path / "Sessions" / "a.md").write_text("---\ntitle: a\n---\n\nx\n", encoding="utf-8")
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Sessions"])
    v = VaultManager(cfg)
    v._ensure_ready = lambda: None
    v.collection = None

    v.get_health_summary(force=True)

    depois = real.read_bytes() if real.exists() else None
    assert depois == antes, (
        "um teste sobre um vault temporario escreveu no vault_health.json real"
    )

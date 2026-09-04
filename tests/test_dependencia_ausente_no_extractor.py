"""Arquivo que nao rendeu nada por falta de dependencia tem que ser AVISADO.

O agregador de #1745 no extract.py existe com esta justificativa escrita no
proprio comentario: sem ele "the graph builds 'successfully' while every such
file silently contributes nothing". Ele classificava a falha por TEXTO,
`"not installed" in erro`, e por isso so pegava o caso em que o pacote nao esta
instalado.

As outras duas falhas de dependencia do mesmo bloco nao contem essa frase:

  - versao incompativel do tree-sitter (TypeError), que e o caso MAIS provavel
    dos tres, porque ali o pacote ESTA instalado e so nao casa com o core;
  - pacote de gramatica sem a funcao de linguagem esperada.

Nas duas o extractor devolve zero nos e o aviso nao saia. A classificacao passa
a ser estrutural (`error_kind`), com o casamento por texto mantido como
retaguarda para os extractors por linguagem, que ainda frasejam a mensagem a mao.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from delegation_core.graph.extract import DEPENDENCIA_AUSENTE
from delegation_core.graph.extractors import engine
from delegation_core.graph.extractors.models import LanguageConfig


def _config() -> LanguageConfig:
    return LanguageConfig(ts_module="tree_sitter_ficticio", ts_language_fn="language",
                          class_types=(), function_types=(), import_types=(), call_types=())


def _com_modulo(monkeypatch, atributos: dict | None):
    """Instala (ou nao) um modulo de gramatica ficticio."""
    if atributos is None:
        monkeypatch.delitem(sys.modules, "tree_sitter_ficticio", raising=False)
        return
    mod = types.ModuleType("tree_sitter_ficticio")
    for k, v in atributos.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "tree_sitter_ficticio", mod)


def _classificado_como_dependencia(res: dict) -> bool:
    """O predicado exato do agregador de #1745, depois da correcao."""
    return (res.get("error_kind") == DEPENDENCIA_AUSENTE
            or "not installed" in (res.get("error") or ""))


def test_versao_incompativel_e_classificada(tmp_path: Path, monkeypatch):
    """O pacote esta instalado e nao casa com o core: Language() levanta
    TypeError. Zero nos, e antes disso nenhum aviso."""
    _com_modulo(monkeypatch, {"language": lambda: object()})
    arq = tmp_path / "a.fx"
    arq.write_text("qualquer coisa")

    res = engine._extract_generic(arq, _config())

    assert res["nodes"] == []
    assert "version mismatch" in res["error"], "a mensagem util para o usuario tem que continuar la"
    assert "not installed" not in res["error"], "o caso so importa porque o texto NAO casa"
    assert _classificado_como_dependencia(res)


def test_gramatica_sem_funcao_de_linguagem_e_classificada(tmp_path: Path, monkeypatch):
    _com_modulo(monkeypatch, {"outra_coisa": lambda: None})
    arq = tmp_path / "a.fx"
    arq.write_text("qualquer coisa")

    res = engine._extract_generic(arq, _config())

    assert res["nodes"] == []
    assert "not installed" not in res["error"]
    assert _classificado_como_dependencia(res)


def test_pacote_ausente_continua_classificado(tmp_path: Path, monkeypatch):
    """O unico caso que ja funcionava nao pode regredir, e o texto que o
    usuario le tambem nao."""
    _com_modulo(monkeypatch, None)
    arq = tmp_path / "a.fx"
    arq.write_text("qualquer coisa")

    res = engine._extract_generic(arq, _config())

    assert res["nodes"] == []
    assert "not installed" in res["error"]
    assert _classificado_como_dependencia(res)


def test_falha_que_nao_e_de_dependencia_nao_e_classificada_como_tal(tmp_path: Path, monkeypatch):
    """A marca nao pode virar guarda-chuva: um erro de parse nao e dependencia
    ausente, e anunciar 'instale o pacote' mandaria o usuario para o lugar
    errado."""
    res = {"nodes": [], "edges": [], "error": "unexpected token at line 3"}
    assert not _classificado_como_dependencia(res)


def test_os_extractors_por_linguagem_continuam_pela_retaguarda():
    """Os 15 extractors por linguagem ainda frasejam a mensagem a mao. Se algum
    deles deixar de dizer 'not installed' sem passar a marcar error_kind, ele
    some do aviso: este teste falha nesse dia."""
    import ast
    import pathlib
    raiz = pathlib.Path(engine.__file__).parent
    faltantes = []
    for arq in sorted(raiz.glob("*.py")):
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        for n in ast.walk(arvore):
            if not isinstance(n, ast.ExceptHandler) or not n.type:
                continue
            if "ImportError" not in ast.unparse(n.type):
                continue
            for c in ast.walk(n):
                if not isinstance(c, ast.Dict):
                    continue
                chaves = {k.value for k in c.keys if isinstance(k, ast.Constant)}
                if "error" not in chaves:
                    continue
                if "error_kind" in chaves:
                    continue
                msg = next((v.value for k, v in zip(c.keys, c.values)
                            if isinstance(k, ast.Constant) and k.value == "error"
                            and isinstance(v, ast.Constant)), None)
                if msg is None or "not installed" not in msg:
                    faltantes.append(f"{arq.name}:{n.lineno}")
    assert not faltantes, (
        "estes handlers de ImportError nao dizem 'not installed' nem marcam "
        f"error_kind, entao somem do aviso de #1745: {faltantes}")

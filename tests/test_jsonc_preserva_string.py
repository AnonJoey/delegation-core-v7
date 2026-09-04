"""_strip_jsonc promete preservar o conteudo das strings. Metade dela cumpria.

O docstring diz "Preserves string contents (including // and /* inside strings)
by skipping over quoted spans first". A passagem de comentarios faz isso com
cuidado: casa a string PRIMEIRO na alternancia e devolve o token intacto.

A remocao de virgula final, tres linhas abaixo, era um re.sub cru sobre o texto
todo, sem nenhuma nocao de string. Medido, ela reescrevia o valor:

    {"note": "lista: a, } fim"}  ->  {"note": "lista: a } fim"}

Calada: o JSON continua valido, o parse funciona, e o valor mudou.
"""
from __future__ import annotations

import json

import pytest

from delegation_core.graph.extractors.resolution import _strip_jsonc


def _ida_e_volta(texto: str) -> dict:
    return json.loads(_strip_jsonc(texto))


# ── a promessa do docstring, nos tres caracteres que importam ────────────────

@pytest.mark.parametrize("trecho", [
    "lista: a, } fim",      # virgula colada num fecho de objeto
    "array: 1, ] fim",      # virgula colada num fecho de lista
    "com espaco ,   }",     # o \\s* do padrao tambem casava aqui
    "usa // aqui",          # ja funcionava, nao pode regredir
    "usa /* aqui */",       # ja funcionava, nao pode regredir
])
def test_conteudo_de_string_atravessa_intacto(trecho):
    origem = {"note": trecho, "paths": {"@a/*": ["./s/*"]}}
    assert _ida_e_volta(json.dumps(origem)) == origem


def test_string_com_escape_nao_confunde_o_casamento():
    """A alternancia casa \\\\. antes de fechar a aspa; uma aspa escapada dentro
    do valor nao pode terminar a string cedo e expor o resto ao re.sub."""
    origem = {"note": 'ele disse "a, } b" e saiu', "x": 1}
    assert _ida_e_volta(json.dumps(origem)) == origem


# ── e a virgula final de verdade continua saindo ─────────────────────────────

def test_virgula_final_simples_sai():
    assert _ida_e_volta('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}


def test_virgula_que_so_encosta_depois_do_comentario_sai():
    """A razao de continuarem sendo DUAS passagens e nao uma: aqui a virgula so
    fica adjacente ao fecho depois que o // some. Uma unica alternancia com
    lookahead nao pegaria este caso."""
    assert _ida_e_volta('{"a": 1, // nota\n}') == {"a": 1}
    assert _ida_e_volta('{"a": 1, /* nota */ }') == {"a": 1}


def test_comentarios_continuam_saindo():
    texto = '''{
      // caminho principal
      "compilerOptions": {
        "paths": {"@app/*": ["./src/app/*"]}  /* alias */
      },
    }'''
    assert _ida_e_volta(texto) == {"compilerOptions": {"paths": {"@app/*": ["./src/app/*"]}}}


def test_tsconfig_realista_com_comentario_dentro_de_string():
    """O caso que junta tudo: um tsconfig com JSONC de verdade e um valor de
    string que contem os tres caracteres perigosos."""
    texto = '''{
      // gerado pelo SvelteKit
      "extends": "./.svelte-kit/tsconfig.json",
      "comment": "ver https://a/b, } e /* isso */ tambem",
      "compilerOptions": {
        "paths": {
          "$lib": ["./src/lib"],
          "$lib/*": ["./src/lib/*"],
        },
      },
    }'''
    resultado = _ida_e_volta(texto)
    assert resultado["comment"] == "ver https://a/b, } e /* isso */ tambem"
    assert resultado["compilerOptions"]["paths"]["$lib/*"] == ["./src/lib/*"]

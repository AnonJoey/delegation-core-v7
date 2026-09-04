"""O `packages:` do pnpm-workspace.yaml, conferido contra um parser de verdade.

A leitura era linha a linha e so entendia lista em BLOCO. Comparada com o pyyaml,
que ja e dependencia declarada do projeto e ja e importado em vault.py e
sidecar.py, ela devolvia VAZIO para a forma embutida:

    packages: ['apps/*', 'libs/*']   ->  feito a mao []   pyyaml ['apps/*', ...]

Vazio significa nenhum pacote de workspace encontrado, entao todo import entre
pacotes do monorepo deixa de resolver e vira no externo. Silencioso e total, num
arquivo que o usuario escreveu de um jeito legitimo.

A leitura linha a linha continua como retaguarda para YAML malformado, e ha teste
prendendo isso: trocar por pyyaml puro perderia a tolerancia.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from delegation_core.graph.extractors.resolution import (_globs_linha_a_linha,
                                                         _pnpm_workspace_globs)


def _escreve(tmp_path: Path, texto: str) -> Path:
    f = tmp_path / "pnpm-workspace.yaml"
    f.write_text(texto, encoding="utf-8")
    return f


def _referencia(texto: str) -> list[str]:
    """O que um parser de YAML de verdade diz."""
    dados = yaml.safe_load(texto) or {}
    pacotes = dados.get("packages") or []
    if not isinstance(pacotes, list):
        pytest.skip("o caso nao e uma lista em YAML valido")
    return [p for p in pacotes if isinstance(p, str) and p and not p.startswith("!")]


CASOS = {
    "bloco": "packages:\n  - 'apps/*'\n  - 'libs/*'\n",
    "embutida com aspas": "packages: ['apps/*', 'libs/*']\n",
    "embutida sem aspas": "packages: [apps/*, libs/*]\n",
    "embutida em varias linhas": "packages: [\n  'apps/*',\n  'libs/*',\n]\n",
    "aspas duplas": 'packages: ["apps/*"]\n',
    "com comentario no meio": "packages:\n  # os apps\n  - 'apps/*'\n  - 'libs/*'\n",
    "com outra chave depois": "packages:\n  - 'apps/*'\nshamefullyHoist: true\n",
    "com negacao": "packages:\n  - 'apps/*'\n  - '!apps/legado'\n",
    "sem aspas em bloco": "packages:\n  - apps/*\n  - libs/*\n",
    "chave ausente": "shamefullyHoist: true\n",
    "arquivo vazio": "",
}


@pytest.mark.parametrize("nome", list(CASOS))
def test_bate_com_o_parser_de_yaml(tmp_path: Path, nome):
    texto = CASOS[nome]
    assert _pnpm_workspace_globs(_escreve(tmp_path, texto)) == _referencia(texto)


def test_a_forma_embutida_nao_pode_voltar_vazia(tmp_path: Path):
    """O caso concreto, dito sem depender da referencia: vazio aqui apaga o
    monorepo inteiro do grafo."""
    achado = _pnpm_workspace_globs(_escreve(tmp_path, "packages: ['apps/*', 'libs/*']\n"))
    assert achado == ["apps/*", "libs/*"]


def test_negacao_e_descartada_nas_duas_formas(tmp_path: Path):
    for texto in ("packages:\n  - 'a/*'\n  - '!a/x'\n", "packages: ['a/*', '!a/x']\n"):
        assert _pnpm_workspace_globs(_escreve(tmp_path, texto)) == ["a/*"]


def test_yaml_malformado_cai_na_retaguarda(tmp_path: Path):
    """Um traco sem espaco nao e lista para o pyyaml, e a leitura linha a linha
    ainda extrai o glob. Se alguem trocar por pyyaml puro achando que
    simplifica, este teste avisa."""
    texto = "packages:\n  -apps/*\n"
    assert not isinstance(yaml.safe_load(texto).get("packages"), list), \
        "a premissa: para o pyyaml isto NAO e lista"
    assert _pnpm_workspace_globs(_escreve(tmp_path, texto)) == ["apps/*"]


def test_yaml_que_nem_parseia_cai_na_retaguarda(tmp_path: Path):
    texto = "packages:\n  - 'a/*'\n\t- b\n  ][\n"
    with pytest.raises(Exception):
        yaml.safe_load(texto)
    # A retaguarda e deliberadamente tolerante e colhe tambem o item da linha
    # com tabulacao, que o YAML recusaria. O que importa aqui e que ela NAO
    # devolva vazio quando o parser de verdade desiste.
    achado = _pnpm_workspace_globs(_escreve(tmp_path, texto))
    assert "a/*" in achado
    assert achado, "arquivo malformado nao pode zerar os pacotes do monorepo"


def test_a_retaguarda_recebe_texto_e_nao_caminho():
    """Assinatura da retaguarda, para nao voltar a reler o arquivo duas vezes."""
    assert _globs_linha_a_linha("packages:\n  - 'x/*'\n") == ["x/*"]

"""O caminho que APAGA arquivo nao conferia contencao no vault.

`_clear_previous_filing` remove as notas da build anterior de um grafo:

    for rel in stale:
        (cfg.vault / rel).unlink()

A unica checagem antes disso era `p.is_file()`. `resolve_in_vault` existe neste
projeto e o docstring dela diz por que:

    "Containment is checked with Path.relative_to, never a string prefix ...
     That exact bug was fixed twice in this codebase before - in relink_folder
     and in the dashboard's note route - so the check lives in one place now
     rather than being re-typed at each new call site."

Corrigido duas vezes, centralizado, e o unico caminho do projeto que APAGA
arquivo do vault nao chamava.

MEDIDO em 03/09/2026, com um `rel` de `../fora_do_vault.md`:
    (cfg.vault / rel).is_file()      -> True   <- a checagem que havia
    resolve_in_vault(vault, rel)     -> None   <- a guarda que existe

## HONESTIDADE SOBRE A SEVERIDADE: nao e explorável hoje

As duas entradas foram perseguidas ate o fim:

**`graph_name`**, que vem de argumento da ferramenta MCP `graph_build(name=...)`
e entra no caminho por `f"{folder}/{WIKI_SUBDIR}/{graph_name}"`. Testado::

    _slugify("../../Sessions")        -> "Sessions"
    _slugify("....//....//Sessions")  -> "Sessions"
    _slugify("..")                    -> "graph"

`_slugify` derruba tudo que nao seja [a-zA-Z0-9_-], entao nao ha travessia por
aqui. Sem ele, um grafo chamado `../../Sessions` faria o rglob varrer a pasta
Sessions do usuario e o unlink apagar todas as notas dela.

**`previous_paths`**, que vem do `graphs_registry.json`. Sao caminhos que o
proprio graphbridge gerou com `relative_to(cfg.vault)`, entao nao carregam `..`
em operacao normal. E um arquivo JSON comum em disco, editavel por qualquer
coisa, mas nao ha caminho de entrada que o envenene sozinho.

## ENTAO POR QUE MEXER

Porque a defesa e INCIDENTAL. `_slugify` existe para produzir um nome de
diretorio utilizavel, nao para conter travessia, e nada no codigo liga uma coisa
a outra. Um pedido plausivel -- aceitar ponto no nome, para um grafo `v1.2` --
relaxa o slug e abre o unlink, sem que ninguem perceba a ligacao.

E o mesmo criterio usado em 03/09 no `downloader`: la a escolha certa dependia
da ordem alfabetica dos nomes do upstream, e foi corrigida por depender de sorte
e nao por estar quebrada. Aqui a contencao depende de uma funcao vizinha
continuar restritiva. O teste abaixo prende as duas metades em separado.
"""
from __future__ import annotations

import pytest

from delegation_core import graphbridge
from delegation_core.config import Config


class VaultFalso:
    def __init__(self, cfg):
        self.cfg = cfg
        self.apagados: list[str] = []

    def delete_notes(self, rel_paths):
        self.apagados.extend(rel_paths)
        return len(rel_paths)


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Reference" / "graphs" / "meu-grafo").mkdir(parents=True)
    (v / "Sessions").mkdir()
    cfg = Config(vault_path=str(v), vault_folders=["Reference", "Sessions"])
    return VaultFalso(cfg)


def test_um_caminho_que_escapa_do_vault_nao_e_apagado(vault, tmp_path):
    """O caso medido: `../fora_do_vault.md` passa no is_file() e nao pode passar
    daqui."""
    fora = tmp_path / "vault" / ".." / "fora_do_vault.md"
    fora = fora.resolve()
    fora.write_text("arquivo do usuario, fora do vault\n", encoding="utf-8")

    graphbridge._clear_previous_filing(
        vault, ["../fora_do_vault.md"], "Reference/graphs/meu-grafo")

    assert fora.exists(), "apagou um arquivo fora do vault"
    assert "../fora_do_vault.md" not in vault.apagados


def test_um_link_simbolico_para_fora_tambem_nao(vault, tmp_path):
    """resolve_in_vault resolve os dois lados de proposito."""
    alvo = (tmp_path / "segredo.md")
    alvo.write_text("fora\n", encoding="utf-8")
    atalho = tmp_path / "vault" / "atalho.md"
    atalho.symlink_to(alvo)

    graphbridge._clear_previous_filing(vault, ["atalho.md"], "Reference/graphs/meu-grafo")

    assert alvo.exists()


def test_a_nota_de_verdade_do_grafo_continua_sendo_apagada(vault, tmp_path):
    """A contencao nao pode virar recusa: o proposito da funcao e limpar a build
    anterior, e sem isso cada rebuild empilha uma copia."""
    velha = tmp_path / "vault" / "Reference" / "graphs" / "meu-grafo" / "Community_0.md"
    velha.write_text("artigo da build anterior\n", encoding="utf-8")

    removidas = graphbridge._clear_previous_filing(
        vault, [], "Reference/graphs/meu-grafo")

    assert removidas == 1
    assert not velha.exists()
    assert vault.apagados == ["Reference/graphs/meu-grafo/Community_0.md"]


def test_a_pasta_do_wiki_tambem_e_contida(vault, tmp_path):
    """Se `wiki_dir_rel` escapasse, o rglob varreria fora do vault. Hoje o
    _slugify impede que chegue assim; a contencao nao pode depender disso."""
    (tmp_path / "vault" / "Sessions" / "nota-do-usuario.md").write_text(
        "nota que nao e do grafo\n", encoding="utf-8")

    graphbridge._clear_previous_filing(vault, [], "Reference/graphs/../../Sessions")

    assert (tmp_path / "vault" / "Sessions" / "nota-do-usuario.md").exists(), (
        "o rglob saiu da pasta do grafo e alcancou as notas do usuario"
    )


# ── a outra metade: o slug, prendida em separado ────────────────────────────


@pytest.mark.parametrize("nome,esperado", [
    ("../../Sessions", "Sessions"),
    ("....//....//Sessions", "Sessions"),
    ("a/../../b", "a-b"),
    ("..", "graph"),
    ("normal-repo", "normal-repo"),
])
def test_o_slug_nao_deixa_separador_nem_ponto_passar(nome, esperado):
    """A defesa de cima. Prendida sozinha para que relaxar o slug quebre ESTE
    teste e nao apenas abra o unlink em silencio."""
    assert graphbridge._slugify(nome) == esperado


def test_o_slug_nao_produz_nome_vazio():
    """Nome vazio viraria `Reference/graphs/`, e o rglob dali pega os artigos de
    TODOS os grafos."""
    for nome in ("", "   ", "---", "!!!"):
        assert graphbridge._slugify(nome) == "graph"

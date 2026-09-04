"""wikilinks() nao pode escrever link para nota que nao existe.

Achado puxando o fio do `note write`, em 03/09/2026. A sequencia foi:

1. `delegation-core note write` gravou uma nota e indexou no proprio processo.
2. A nota foi apagada do disco.
3. `indexed_notes` foi de 12.955 para 12.956 e NAO voltou: a linha sobreviveu
   ao arquivo, e `vault_health` seguiu reportando `orphans: 0`.
4. `search()` devolve `meta["path"]` sem conferir existencia, e o docstring de
   `index_note` diz que os chamadores "feed a hit's path straight back to the
   filesystem (merger, linker, the dashboard all do)".

Conferindo os quatro consumidores um por um, TRES guardam e UM nao:

    merger.try_merge         `if not existing_path.exists(): continue`
    linker.relink_folder     `if (cfg.vault / path) not in resolvable_notes`
    linker.inject_backlinks  `if not f.exists(): continue`
    linker.wikilinks         nada

E `wikilinks` nem tinha COMO conferir: a assinatura recebia so os hits e o
limiar, sem a raiz do vault. Ela e o helper compartilhado que monta o texto do
link, chamada de seis lugares, entre eles o passe de heal do organizer e o
`_inject_related_links` que o `note write` usa. O resultado e a ferramenta
fabricando link quebrado sozinha, apontando para nota que ja nao existe.

E o mesmo padrao que o proprio `merger.py` documenta tres paragrafos acima de
`try_merge`: a guarda foi escrita em `relink_folder` depois de 23 links ruins
numa passada real, e nunca desceu para o helper que os outros seis usam.
"Uma defesa que depende de cada autor lembrar dela nao e uma defesa" — por isso
a raiz do vault e parametro OBRIGATORIO aqui, e nao opcional: um chamador novo
nao pode esquecer o que nao pode omitir.

Este modulo nao tinha nenhum teste antes deste arquivo.
"""
from __future__ import annotations

import pytest

from delegation_core.linker import wikilinks


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Fixes").mkdir()
    (tmp_path / "Sessions").mkdir()
    (tmp_path / "Fixes" / "existe.md").write_text("# a\n", encoding="utf-8")
    (tmp_path / "Sessions" / "tambem-existe.md").write_text("# b\n", encoding="utf-8")
    return tmp_path


def _hit(path, sim=0.9, title=None):
    return {"path": path, "similarity": sim, "title": title}


# ── a guarda que faltava ────────────────────────────────────────────────────


def test_nota_apagada_do_disco_nao_vira_link(vault):
    """O caso exato: a linha ficou no indice, o arquivo nao existe mais."""
    saida = wikilinks([_hit("Fixes/apagada.md")], 0.5, vault)
    assert saida == ""


def test_nota_que_existe_continua_virando_link(vault):
    saida = wikilinks([_hit("Fixes/existe.md")], 0.5, vault)
    assert saida == "- [[existe]]"


def test_a_apagada_e_removida_sem_derrubar_as_outras(vault):
    """Filtrar, e nao abortar: uma linha morta no meio da lista nao pode custar
    os links bons que vieram junto."""
    hits = [_hit("Fixes/existe.md"),
            _hit("Fixes/apagada.md"),
            _hit("Sessions/tambem-existe.md")]

    saida = wikilinks(hits, 0.5, vault)

    assert saida == "- [[existe]]\n- [[tambem-existe]]"


def test_hit_sem_path_nao_vira_link_por_titulo(vault):
    """O ramo `elif h.get("title")` linkava pelo TITULO quando nao havia path.

    Uma linha sem path e exatamente a que nao da para resolver contra o disco,
    entao linkar por titulo e escrever um link que ninguem pode conferir. Vale
    aqui a mesma regra de `_unindexed_notes`: degradar para "nao da para saber",
    nunca para "esta tudo certo".
    """
    saida = wikilinks([{"title": "Alguma Nota", "similarity": 0.9}], 0.5, vault)
    assert saida == ""


def test_path_vazio_nao_vira_link_do_proprio_vault(vault):
    """`search()` devolve `path: ""` para linha sem metadado de caminho.

    HONESTIDADE SOBRE O QUE ESTE TESTE PROVA, escrita depois de uma mutacao.
    Removi a guarda `not rel` da implementacao e este teste CONTINUOU PASSANDO.
    Quem pega o caso hoje e o `is_file()`: `Path(vault) / ""` e o diretorio do
    vault, que existe mas nao e arquivo. A guarda `not rel` e redundante, e
    nenhuma entrada chega so nela.

    Ela fica assim mesmo, e o motivo esta na mutacao vizinha: trocar `is_file()`
    por `exists()` faz o diretorio do vault passar, e ai o path vazio vira
    `[[Claude Vault]]` na nota do usuario. As duas guardas cobrem o mesmo caso
    por caminhos diferentes, e a de baixo so segura se a de cima afrouxar.

    Mesma decisao, e mesmo raciocinio, do `startswith("cudart-")` no
    downloader: a palavra-chave ja pega, o prefixo fica para o dia em que
    alguem tirar a palavra-chave.
    """
    saida = wikilinks([{"path": "", "similarity": 0.9, "title": "x"}], 0.5, vault)
    assert saida == ""


def test_com_uma_checagem_de_arquivo_frouxa_o_path_vazio_ainda_e_barrado(vault, monkeypatch):
    """O teste que prende a guarda `not rel` sozinha, e nao junto da outra.

    Afrouxa a checagem de arquivo para `exists()`, que e a mutacao sob a qual o
    diretorio do vault passa, e exige que o path vazio continue barrado. Sem a
    guarda `not rel`, este teste falha e o link `[[<nome do vault>]]` aparece.
    """
    import delegation_core.linker as linker_mod

    class _SempreArquivo(type(vault)):
        def is_file(self):
            return self.exists()

    monkeypatch.setattr(linker_mod, "Path", _SempreArquivo)

    assert wikilinks([{"path": "", "similarity": 0.9, "title": "x"}], 0.5, vault) == ""


def test_diretorio_nao_conta_como_nota(vault):
    """Mesma armadilha por outra porta: `Fixes` existe, e nao e uma nota."""
    saida = wikilinks([_hit("Fixes")], 0.5, vault)
    assert saida == ""


# ── o comportamento antigo que precisa continuar valendo ────────────────────


def test_limiar_continua_cortando(vault):
    assert wikilinks([_hit("Fixes/existe.md", sim=0.2)], 0.5, vault) == ""


def test_limiar_e_inclusivo_na_borda(vault):
    assert wikilinks([_hit("Fixes/existe.md", sim=0.5)], 0.5, vault) == "- [[existe]]"


def test_hit_sem_similaridade_e_tratado_como_zero(vault):
    assert wikilinks([{"path": "Fixes/existe.md"}], 0.5, vault) == ""


def test_titulo_diferente_do_stem_vira_link_com_alias(vault):
    saida = wikilinks([_hit("Fixes/existe.md", title="Um Titulo Legivel")], 0.5, vault)
    assert saida == "- [[existe|Um Titulo Legivel]]"


def test_lista_vazia_devolve_string_vazia(vault):
    assert wikilinks([], 0.5, vault) == ""


def test_caminho_absoluto_de_arquivo_externo_nao_vira_link(vault, tmp_path):
    """Arquivos ingeridos sao indexados por caminho ABSOLUTO e nao sao notas do
    vault. `relink_folder` ja tinha essa guarda, com o registro de 23 links
    `[[SKILL]]` escritos numa passada real antes dela existir."""
    externo = tmp_path / "fora" / "doc.md"
    externo.parent.mkdir()
    externo.write_text("# externo\n", encoding="utf-8")

    saida = wikilinks([_hit(str(externo))], 0.5, vault)

    assert saida == ""

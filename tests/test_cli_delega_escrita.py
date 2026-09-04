"""Todo comando de CLI que escreve no indice tem que passar pelo daemon.

O docstring de `_delegate` afirma "Every command that writes to ChromaDB goes
through here". Em 03/09 essa frase era falsa para `note write` e `note update`,
e eu a corrigi nomeando os dois. CONTINUAVA FALSA: `relink` e `graph build`
tambem escrevem e tambem nao delegavam, e eu escrevi uma correcao que afirmava
completude sem ter contado os chamadores.

Corrigir a prosa foi o erro. Uma frase nao pode garantir uma propriedade sobre
onze funcoes; um teste pode. Este arquivo faz o que `test_docs_not_stale` faz
com as contagens e `test_version_consistency` faz com a versao: proibir a FORMA,
para o proximo comando nascer certo ou nascer quebrando um teste.

## O que foi medido, e nao suposto

`relink` chama `organizer`/`relink_folder`, que fazem `f.write_text(updated)`
seguido de `vault_manager.index_note(...)` (linker.py 229-230 e 351-352).
`graph build` file os artigos no vault e indexa cada um.

E o custo do caminho local nao e so a corrida de escrita. Medido em 04/09/2026,
com o daemon no ar e amostragem de `nvidia-smi` a cada segundo::

    delegation-core search "..."   ->  no segundo 20, um SEGUNDO processo
                                       aparece na GPU com 2314 MiB, ao lado dos
                                       2314 MiB que o daemon ja segura

Duas copias inteiras do BGE numa placa de 16 GB onde `gpu.py` existe como
arbitro de exclusao mutua entre BGE e llama.cpp. E o arbitro nao ve isso: o
`_holder` dele e um global de MODULO, que enxerga um dono dentro do processo e
nao tem como saber de um segundo processo.

## O que foi conferido e estava limpo

`note read` NAO carrega o BGE: 1,04s e nenhum processo novo na GPU, porque
`find_notes_by_stem` e rglob puro e o `VaultManager` so prepara o indice sob
demanda. A suspeita era razoavel e a medicao a derrubou.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CLI = RAIZ / "src" / "delegation_core" / "cli.py"

#: Comandos que TOCAM o indice (leem ou escrevem) atraves do VaultManager e
#: portanto podem carregar o BGE neste processo. Cada um tem que delegar ou
#: declarar por que nao.
#:
#: A isencao e por MOTIVO escrito, e nao por nome numa lista solta: quem
#: adicionar um comando novo tem que dizer aqui o que ele faz.
_ISENTOS = {
    # Nao chega a preparar o indice: find_notes_by_stem e rglob no sistema de
    # arquivos. Medido: 1,04s, zero processos novos na GPU.
    "cmd_note_read": "so sistema de arquivos, medido sem carregar BGE",
    "cmd_note_list": "so sistema de arquivos, list_notes caminha as pastas",
    # O doctor existe para consertar um indice que o daemon nao consegue abrir.
    # Delegar ao daemon o diagnostico do daemon seria circular.
    "cmd_doctor": "diagnostica o indice quando o daemon nao sobe",
}


def _comandos_que_tocam_o_indice() -> dict[str, dict]:
    fonte = CLI.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    linhas = fonte.splitlines()
    achados: dict[str, dict] = {}
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.FunctionDef) and no.name.startswith("cmd_")):
            continue
        corpo = "\n".join(linhas[no.lineno - 1: no.end_lineno])
        if "VaultManager(" not in corpo:
            continue
        achados[no.name] = {
            "delega": "_delegate(" in corpo,
            "linha": no.lineno,
        }
    return achados


def test_todo_comando_que_toca_o_indice_delega_ou_esta_isento():
    achados = _comandos_que_tocam_o_indice()
    faltando = {n: d for n, d in achados.items()
                if not d["delega"] and n not in _ISENTOS}
    assert not faltando, (
        "abrem o VaultManager neste processo sem passar pelo daemon:\n  "
        + "\n  ".join(f"cli.py:{d['linha']}  {n}" for n, d in sorted(faltando.items()))
        + "\nDelegue, ou acrescente a _ISENTOS com o motivo MEDIDO."
    )


def test_a_varredura_realmente_encontra_comandos():
    """Uma varredura quebrada passa igual a uma que nao tem o que achar."""
    achados = _comandos_que_tocam_o_indice()
    assert len(achados) >= 8, f"so achou {len(achados)} comandos; a varredura quebrou"
    assert "cmd_reindex" in achados and achados["cmd_reindex"]["delega"]


def test_a_lista_de_isentos_nao_tem_nome_morto():
    """Um isento que nao existe mais e uma permissao dada a ninguem, e esconde
    o dia em que alguem recriar a funcao com outro comportamento."""
    achados = _comandos_que_tocam_o_indice()
    fonte = CLI.read_text(encoding="utf-8")
    mortos = [n for n in _ISENTOS if f"def {n}(" not in fonte]
    assert not mortos, f"isentos que nao existem mais: {mortos}"


@pytest.mark.parametrize("comando", ["reindex", "maintain", "ingest", "relink",
                                     "search", "note write", "note update"])
def test_cada_comando_que_toca_o_indice_oferece_local(comando):
    """`--local` e a saida documentada em `_add_local_flag`: cair no processo
    local em SILENCIO seria o segundo escritor sem documentacao."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "from delegation_core.cli import main; main()",
         *comando.split(), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert "--local" in r.stdout, f"`delegation-core {comando}` nao oferece --local"


def test_o_docstring_do_delegate_nao_lista_chamadores_a_mao():
    """A frase que estava errada duas vezes.

    "Every command that writes to ChromaDB goes through here" e uma afirmacao
    sobre onze funcoes, e ficou falsa duas vezes: primeiro para note
    write/update, depois -- na correcao que EU escrevi -- para relink e graph
    build. Uma lista escrita a mao dentro de um docstring e uma copia, e copia
    deriva. O docstring passa a apontar para este teste.
    """
    fonte = CLI.read_text(encoding="utf-8")
    inicio = fonte.index("def _delegate(")
    doc = fonte[inicio: fonte.index('"""', fonte.index('"""', inicio) + 3)]
    assert "test_cli_delega_escrita" in doc, (
        "o docstring tem que apontar para o teste que garante a propriedade, "
        "em vez de reafirma-la em prosa"
    )


# ── e os testes que a mutacao me obrigou a escrever ─────────────────────────
#
# A varredura de AST acima procura o TEXTO `_delegate(` no corpo da funcao.
# Tres mutacoes SOBREVIVERAM a ela: trocar `_delegate(...)` por
# `None and _delegate(...)` desliga a delegacao e mantem o texto intacto.
#
# A varredura continua valendo como lint -- ela pega o comando NOVO que nao
# delega de jeito nenhum, que e o caso que aconteceu quatro vezes neste projeto
# -- mas ela nao pode ser a unica coisa afirmando a propriedade. Estes exercitam
# cada comando e olham qual ferramenta chegou ao daemon.


import types


@pytest.fixture
def delegacao(monkeypatch, tmp_path):
    """Captura a ferramenta pedida ao daemon, sem daemon e sem BGE."""
    import delegation_core.cli as cli
    import delegation_core.config as config_mod
    from delegation_core.config import Config

    (tmp_path / "Reference").mkdir()
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference"],
                 engine_mode="agent")
    monkeypatch.setattr(config_mod.Config, "load", staticmethod(lambda: cfg))

    chamadas = []

    def _fake(cfg_, args, tool, arguments, say):
        chamadas.append((tool, arguments))
        return {"result": {}, "sources": [], "similar": [],
                "status": "ok", "path": "Reference/x.md"}

    monkeypatch.setattr(cli, "_delegate", _fake)

    class _VaultProibido:
        def __init__(self, *a, **k):
            raise AssertionError(
                "abriu o VaultManager neste processo: e a segunda copia do BGE "
                "que a delegacao existe para evitar")

    import delegation_core.vault as vault_mod
    monkeypatch.setattr(vault_mod, "VaultManager", _VaultProibido)
    return chamadas


def _args(**kw):
    base = {"local": False, "timeout": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_relink_pede_relink_folder_bg_ao_daemon(delegacao):
    import delegation_core.cli as cli
    cli.cmd_relink(_args(folder="Reference", days=None, min_similarity=None, max_links=8))
    assert [t for t, _ in delegacao] == ["relink_folder_bg"]


def test_search_pede_search_vault_ao_daemon(delegacao):
    import delegation_core.cli as cli
    cli.cmd_search(_args(query="alguma coisa", limit=3))
    assert [t for t, _ in delegacao] == ["search_vault"]
    assert delegacao[0][1]["query"] == "alguma coisa"


def test_find_similar_pede_vault_find_similar_ao_daemon(delegacao):
    import delegation_core.cli as cli
    cli.cmd_note_find_similar(_args(name="alguma", threshold=0.8, limit=5))
    assert [t for t, _ in delegacao] == ["vault_find_similar"]


def test_graph_build_pede_graph_build_bg_ao_daemon(delegacao):
    import delegation_core.cli as cli
    cli.cmd_graph_build(_args(path="/tmp/repo", name="", force=False))
    assert [t for t, _ in delegacao] == ["graph_build_bg"]


def test_note_write_pede_write_note_ao_daemon(delegacao, tmp_path):
    """Ja tinha teste proprio; repetido aqui para o conjunto ficar completo."""
    import delegation_core.cli as cli
    entrada = tmp_path / "c.md"
    entrada.write_text("corpo", encoding="utf-8")
    cli.cmd_note_write(_args(folder="Reference", title="T", file=str(entrada)))
    assert [t for t, _ in delegacao] == ["write_note"]

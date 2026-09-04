"""O doctor afirmava "the daemon is the only writer" sem ter como saber.

Rodado contra esta instalacao em 04/09/2026, o doctor devolveu 10 ok, 0 avisos,
0 erros, e entre os ok:

    local_fallback   sentry present and local fallback off: the daemon is the
                     only writer, consistently

Na mesma noite, com lsof amostrado a cada segundo, o `delegation-core note
write` abriu o MESMO chroma.sqlite3 que o daemon segurava, e `relink`,
`graph build` e `note update` faziam o mesmo. O daemon nao era o unico escritor,
e o check dizia que sim.

O check em si esta certo e e util: `no_auto_reindex` mais
`allow_local_index_fallback: false` significam mesmo que uma CLI que NAO ALCANCA
o daemon nao vai abrir o indice sozinha. O erro esta na conclusao: essas duas
configuracoes governam o caminho de FALLBACK, e nao dizem nada sobre um comando
que nunca tenta o daemon.

E a familia inteira desta noite, na ferramenta cujo trabalho e justamente dizer
a verdade sobre a instalacao: uma verificacao que responde mais do que verificou.

A correcao tem duas metades: a mensagem passa a dizer o que foi conferido, e o
doctor passa a CONFERIR a propriedade que ele afirmava, lendo o `cli.py` da
instalacao. Contra o pacote instalado isso vale mais do que contra o repo: pega
uma instalacao velha, ou remendada a mao, em que um comando ainda escreve por
fora.
"""
from __future__ import annotations

import pytest

from delegation_core import doctor
from delegation_core.config import Config


def test_a_mensagem_de_ok_nao_afirma_escritor_unico(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "CONFIG_DIR", tmp_path)
    (tmp_path / "no_auto_reindex").write_text("", encoding="utf-8")
    cfg = Config(vault_path=str(tmp_path), allow_local_index_fallback=False)

    r = doctor.check_local_fallback(cfg)

    assert r["status"] == "ok"
    assert "only writer" not in r["detail"], (
        "as duas configuracoes governam o fallback e nao todos os caminhos de "
        "escrita; foi assim que o doctor deu verde com quatro comandos escrevendo "
        "por fora"
    )


def test_o_aviso_de_configuracao_inconsistente_continua(tmp_path, monkeypatch):
    """A metade que ja estava certa nao pode sair junto."""
    monkeypatch.setattr(doctor, "CONFIG_DIR", tmp_path)
    (tmp_path / "no_auto_reindex").write_text("", encoding="utf-8")
    cfg = Config(vault_path=str(tmp_path), allow_local_index_fallback=True)

    assert doctor.check_local_fallback(cfg)["status"] == "warn"


def test_sem_sentinela_segue_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "CONFIG_DIR", tmp_path)
    cfg = Config(vault_path=str(tmp_path))
    assert doctor.check_local_fallback(cfg)["status"] == "ok"


# ── o check novo, que confere em vez de afirmar ─────────────────────────────


def test_uma_instalacao_em_dia_passa():
    r = doctor.check_index_writers()
    assert r["status"] == "ok", r["detail"]
    assert "cmd_" not in r["detail"], "a mensagem de ok nao precisa listar funcoes"


def test_um_cli_com_comando_que_nao_delega_e_reportado(tmp_path, monkeypatch):
    """O estado real desta maquina antes das correcoes de hoje: um comando que
    constroi VaultManager e nunca chama _delegate."""
    falso = tmp_path / "cli.py"
    falso.write_text(
        "def cmd_relink(args):\n"
        "    vault = VaultManager(cfg)\n"
        "    return relink_folder(vault, args.folder)\n"
        "\n"
        "def cmd_reindex(args):\n"
        "    job = _delegate(cfg, args, 'vault_reindex_bg', {}, say)\n"
        "    vault = VaultManager(cfg)\n",
        encoding="utf-8")
    monkeypatch.setattr(doctor, "_caminho_do_cli", lambda: falso)

    r = doctor.check_index_writers()

    assert r["status"] == "warn"
    assert "cmd_relink" in r["detail"]
    assert "cmd_reindex" not in r["detail"]


def test_um_cli_ilegivel_nao_vira_um_ok(tmp_path, monkeypatch):
    """Degradar para "nao da para saber", nunca para "esta tudo certo" -- que e
    exatamente o defeito que este arquivo existe para corrigir."""
    monkeypatch.setattr(doctor, "_caminho_do_cli", lambda: tmp_path / "nao-existe.py")

    r = doctor.check_index_writers()

    assert r["status"] != "ok"
    assert "cmd_" not in r["detail"]


def test_o_check_novo_esta_na_bateria():
    """Um check que ninguem roda nao verifica nada."""
    import inspect
    fonte = inspect.getsource(doctor.run_all)
    assert "check_index_writers(" in fonte

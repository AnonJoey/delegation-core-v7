"""O uninstall removia UMA das duas registracoes de servico, e nao parava o daemon.

Medido nesta maquina, no arquivo real, antes de qualquer correcao:

    ~/.config/systemd/user/delegation-core.service        existe, enabled
      ExecStart=/home/joey/.delegation_core/venv/bin/delegation-core run
      Restart=on-failure  /  StartLimitBurst=5
    ~/.config/systemd/user/delegation-core-llama.service  existe, enabled

`uninstall.sh` desabilitava e removia a SEGUNDA. A primeira ficava, habilitada,
com o ExecStart apontando para o venv que a linha `rm -rf "$CFG_DIR/venv"` do
mesmo script acabara de apagar. O estado final de um uninstall "bem-sucedido"
era portanto uma unit que falha com 203/EXEC a cada login, tenta cinco vezes
dentro da janela de 300s e fica `failed` para sempre, de um software que o
usuario foi informado ter sido removido.

A causa nao foi distracao: os nomes das units estavam escritos a mao em cinco
arquivos (wizard.py, service.py, uninstall.sh, uninstall.bat e a mensagem de
cada um). A registracao do daemon nasceu depois dos dois uninstallers, e nada
ligava uma coisa na outra. Por isso a correcao move os nomes para service.py e
os dois lados passam a ler de la.

O segundo defeito e a ordem. Nenhum dos dois scripts parava o daemon antes de
apagar. No Linux ele seguia rodando a partir de um venv apagado, ainda segurando
o indice e a porta depois de "Uninstall complete". No Windows as remocoes nem
aconteciam, porque o processo mantinha os arquivos abertos, e cada uma delas
terminava em `>nul 2>&1` sem checar errorlevel: um uninstall pela metade
reportava sucesso.

Tudo aqui usa fakes. Nenhum teste desta suite toca gerenciador de servico real,
vault real, nem o ~/.delegation_core de verdade: o `conftest.py` reaponta
CONFIG_DIR (incluindo o do installer, que a partir daqui APAGA arquivos) para
tmp_path, e falha o teste que ainda assim remover hooks/, venv/ ou models/.
"""
from __future__ import annotations

import json

import pytest

from delegation_core import installer, service


@pytest.fixture
def cfg_dir(_sem_escrita_no_estado_real):
    """O CONFIG_DIR temporario que o conftest ja instalou."""
    return _sem_escrita_no_estado_real


@pytest.fixture
def instalacao(cfg_dir):
    """Uma instalacao completa e falsa: tudo que o uninstall deve remover, e o
    que ele nunca pode tocar."""
    (cfg_dir / "venv" / "bin").mkdir(parents=True)
    (cfg_dir / "venv" / "bin" / "delegation-core").write_text("#!/bin/sh\n")
    (cfg_dir / "models").mkdir()
    (cfg_dir / "models" / "bge-m3.gguf").write_text("pesos que sao do usuario")
    for d in installer.REMOVE_DIRS:
        (cfg_dir / d).mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hooks" / "session_export.py").write_text("# hook")
    for f in installer.REMOVE_FILES:
        (cfg_dir / f).write_text("x")
    (cfg_dir / "server.log").write_text("log")
    (cfg_dir / "backups_pre_upgrade_20260101_000000").mkdir()

    vault = cfg_dir.parent / "vault"
    vault.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({"vault_path": str(vault)}))
    return cfg_dir


@pytest.fixture
def servico(monkeypatch):
    """Substitui o gerenciador de servico e registra a ordem das chamadas."""
    # `no_ar` e a fila de respostas sobre o estado do daemon DEPOIS do stop. O
    # caminho feliz e uma unica resposta False: o daemon parou.
    #
    # O dublê passou a ser de `wait_until_down` e nao mais de `is_up`, porque a
    # pergunta que o uninstall faz e "ele DESCEU?" e nao "ele SUBIU?". Trocar a
    # funcao sem trocar isto deixaria estes tres testes verdes contra uma
    # funcao que o codigo nao chama mais. `wait_until_down` devolve True quando
    # a porta silenciou, entao a resposta e o INVERSO de "no ar".
    registro = {"ordem": [], "no_ar": [False]}

    def _stop(*a, **k):
        registro["ordem"].append("stop")
        return {"action": "stop", "status": "stopped", "detail": ""}

    def _wait_until_down(timeout_seconds=0.0, interval=0.5):
        registro["ordem"].append("wait_until_down")
        ainda_no_ar = registro["no_ar"].pop(0) if registro["no_ar"] else False
        return not ainda_no_ar

    def _uninstall():
        registro["ordem"].append("unregister:daemon")
        return {"platform": "Linux", "status": "removed"}

    def _uninstall_llama():
        registro["ordem"].append("unregister:llama")
        return {"platform": "Linux", "status": "removed"}

    monkeypatch.setattr(service, "stop", _stop)
    monkeypatch.setattr(service, "wait_until_down", _wait_until_down)
    monkeypatch.setattr(service, "uninstall", _uninstall)
    monkeypatch.setattr(service, "uninstall_llama_autostart", _uninstall_llama)
    return registro


# ── o defeito central: a registracao que ficava para tras ───────────────────

def test_remove_AS_DUAS_registracoes_de_servico(instalacao, servico):
    """O daemon e o llama. O uninstall antigo so removia o llama."""
    r = installer.uninstall()
    assert r["status"] == "ok"
    assert "unregister:daemon" in servico["ordem"]
    assert "unregister:llama" in servico["ordem"]


def test_o_plano_nomeia_as_duas_units(instalacao, servico):
    """Quem le o `--dry-run` tem que ver as duas, nao so a que existia antes."""
    plano = installer.uninstall(dry_run=True)["plan"]
    assert plano["services"] == ["delegation-core", "delegation-core-llama"]


def test_os_nomes_vem_de_service_e_nao_de_copias(instalacao, servico):
    """A causa raiz. Se alguem renomear a unit em service.py, o plano acompanha.

    Enquanto os nomes estavam escritos a mao em cada arquivo, renomear em um
    lugar deixava os outros quatro apontando para o nome velho, sem erro.
    """
    monkey_nome = "outro-nome-de-daemon"
    original = service.SERVICE_NAME
    try:
        service.SERVICE_NAME = monkey_nome
        plano = installer.uninstall(dry_run=True)["plan"]
        assert plano["services"][0] == monkey_nome
    finally:
        service.SERVICE_NAME = original


def test_o_caminho_da_unit_do_llama_deriva_do_nome():
    assert service.LLAMA_SERVICE_NAME == "delegation-core-llama"
    assert service.LLAMA_SYSTEMD_UNIT.name == "delegation-core-llama.service"
    assert service.LLAMA_LAUNCHD_PLIST.name == "com.delegation-core.llama.plist"


def test_o_wizard_registra_a_unit_que_o_uninstall_remove():
    """As duas metades tem que concordar, e agora concordam por construcao.

    Este teste falharia se alguem voltasse a escrever o nome a mao no wizard.
    """
    import inspect

    from delegation_core import wizard
    fonte = inspect.getsource(wizard)
    assert '"delegation-core-llama"' not in fonte, (
        "o wizard voltou a escrever o nome da unit a mao em vez de ler de service.py"
    )
    assert "LLAMA_SERVICE_NAME" in fonte


# ── a ordem: parar antes de apagar ───────────────────────────────────────────

def test_para_o_daemon_antes_de_remover_qualquer_coisa(instalacao, servico):
    r = installer.uninstall()
    assert servico["ordem"][0] == "stop"
    assert [p["step"] for p in r["steps"]][0] == "stop_daemon"


def test_recusa_se_o_daemon_continua_no_ar(instalacao, servico):
    """Apagar sob um processo vivo deixa um daemon segurando um indice sem
    pacote atras. Recusar e a resposta certa, nao apagar mesmo assim."""
    servico["no_ar"] = [True]          # continua respondendo depois do stop
    r = installer.uninstall()

    assert r["status"] == "refused_daemon_still_up"
    assert (instalacao / "hooks").exists(), "removeu apesar da recusa"
    assert (instalacao / "config.json").exists()
    assert "unregister:daemon" not in servico["ordem"]


def test_a_recusa_explica_o_que_fazer(instalacao, servico):
    servico["no_ar"] = [True]
    assert "Stop it yourself" in installer.uninstall()["detail"]


# ── o que nunca pode ser tocado ──────────────────────────────────────────────

def test_nao_remove_os_pesos_do_modelo(instalacao, servico):
    installer.uninstall()
    assert (instalacao / "models" / "bge-m3.gguf").read_text() == "pesos que sao do usuario"


def test_nao_remove_o_venv_de_onde_esta_rodando(instalacao, servico):
    """No Windows um interpretador nao consegue apagar os proprios arquivos.
    Tentar aqui seria meio-sucesso la e honestidade em lugar nenhum."""
    r = installer.uninstall()
    assert (instalacao / "venv").exists()
    assert r["venv_pending"]["path"] == str(instalacao / "venv")


def test_venv_e_models_estao_na_lista_de_intocaveis():
    assert "models" in installer.KEEP_ALWAYS
    assert "venv" in installer.KEEP_ALWAYS
    assert "models" not in installer.REMOVE_DIRS
    assert "venv" not in installer.REMOVE_DIRS


@pytest.mark.parametrize("protegido", ["models", "venv"])
def test_KEEP_ALWAYS_protege_mesmo_com_a_lista_de_remocao_errada(
        instalacao, servico, monkeypatch, protegido):
    """A segunda linha de defesa, exercitada sozinha.

    Enquanto nenhum nome de KEEP_ALWAYS aparece em REMOVE_DIRS/FILES/GLOBS o
    guarda nunca e alcancado, entao apaga-lo nao muda resultado nenhum e
    nenhum teste percebe. Ele existe justamente para o dia em que alguem
    acrescentar `models` a uma dessas listas: este teste encena esse dia.
    """
    monkeypatch.setattr(installer, "REMOVE_DIRS", (protegido, *installer.REMOVE_DIRS))
    installer.uninstall()
    assert (instalacao / protegido).exists(), (
        f"{protegido} foi removido: o guarda KEEP_ALWAYS nao segurou"
    )


def test_nao_toca_o_vault(instalacao, servico):
    vault = instalacao.parent / "vault"
    (vault / "uma-nota.md").write_text("conteudo do usuario")
    installer.uninstall()
    assert (vault / "uma-nota.md").read_text() == "conteudo do usuario"


def test_relata_onde_o_vault_ficou(instalacao, servico):
    r = installer.uninstall()
    assert r["kept"]["vault"] == str(instalacao.parent / "vault")


def test_sem_config_legivel_diz_que_nao_sabe_onde_o_vault_esta(instalacao, servico):
    (instalacao / "config.json").write_text("{isto nao e json")
    r = installer.uninstall()
    assert "unknown" in r["kept"]["vault"]


# ── o vault dentro do CONFIG_DIR ─────────────────────────────────────────────

def test_recusa_vault_dentro_do_config_dir(instalacao, servico):
    """Toda remocao e por nome exato, e um vault de verdade pode ter arquivos
    com esses mesmos nomes."""
    (instalacao / "config.json").write_text(
        json.dumps({"vault_path": str(instalacao / "meu-vault")}))
    r = installer.uninstall()

    assert r["status"] == "refused_vault_inside_config_dir"
    assert (instalacao / "hooks").exists()
    assert servico["ordem"] == [], "parou o daemon antes de recusar"


def test_recusa_vault_igual_ao_config_dir(instalacao, servico):
    (instalacao / "config.json").write_text(json.dumps({"vault_path": str(instalacao)}))
    assert installer.uninstall()["status"] == "refused_vault_inside_config_dir"


def test_um_vault_normal_nao_e_recusado(instalacao, servico):
    assert installer.uninstall()["status"] == "ok"


def test_vault_por_symlink_para_dentro_tambem_e_recusado(instalacao, servico, tmp_path):
    """Comparar as strings deixaria passar. A checagem resolve os dois lados."""
    alvo = instalacao / "vault-real"
    alvo.mkdir()
    link = tmp_path / "atalho-do-vault"
    link.symlink_to(alvo, target_is_directory=True)
    (instalacao / "config.json").write_text(json.dumps({"vault_path": str(link)}))

    assert installer.uninstall()["status"] == "refused_vault_inside_config_dir"


def test_vault_vazio_nao_dispara_a_recusa():
    assert installer.vault_is_inside_config_dir("") is False


# ── as remocoes ──────────────────────────────────────────────────────────────

def test_remove_o_que_prometeu(instalacao, servico):
    installer.uninstall()
    for d in installer.REMOVE_DIRS:
        assert not (instalacao / d).exists(), d
    for f in installer.REMOVE_FILES:
        assert not (instalacao / f).exists(), f
    assert not (instalacao / "server.log").exists()
    assert not (instalacao / "backups_pre_upgrade_20260101_000000").exists()


def test_uma_falha_de_remocao_e_reportada_e_nao_engolida(instalacao, servico, monkeypatch):
    """O `>nul 2>&1` do .bat e exatamente isto ao contrario: cada remocao la
    descartava o erro e o script seguia imprimindo OK."""
    import shutil as _shutil

    real_rmtree = _shutil.rmtree

    def _rmtree_que_falha(caminho, *a, **k):
        if str(caminho).endswith("graphs"):
            raise OSError(16, "Device or resource busy")
        return real_rmtree(caminho, *a, **k)

    monkeypatch.setattr(installer.shutil, "rmtree", _rmtree_que_falha)
    r = installer.uninstall()

    assert r["status"] == "partial"
    assert any("graphs" in f["path"] for f in r["failures"])
    assert not (instalacao / "sessions").exists(), "uma falha abortou o resto"


def test_config_dir_ausente_nao_e_erro(cfg_dir, servico):
    import shutil as _shutil
    _shutil.rmtree(cfg_dir)
    r = installer.uninstall()
    assert r["status"] == "nothing_to_uninstall"
    assert servico["ordem"] == []


def test_dry_run_nao_remove_nada(instalacao, servico):
    r = installer.uninstall(dry_run=True)
    assert r["status"] == "dry_run"
    assert (instalacao / "hooks").exists()
    assert (instalacao / "config.json").exists()
    assert servico["ordem"] == [], "o dry-run parou o daemon"


def test_o_plano_do_dry_run_lista_so_o_que_existe(instalacao, servico):
    (instalacao / "processes.json").unlink()
    plano = installer.uninstall(dry_run=True)["plan"]
    assert not any(p.endswith("processes.json") for p in plano["files"])
    assert any(p.endswith("config.json") for p in plano["files"])


# ── o sentinel, que e o contrato com o stub de shell ────────────────────────

def test_escreve_o_sentinel_quando_deixa_o_venv_de_pe(instalacao, servico):
    installer.uninstall()
    sentinel = installer.venv_pending_path()
    assert sentinel.exists()
    assert sentinel.read_text().strip() == str(instalacao / "venv")


def test_o_dry_run_NAO_escreve_o_sentinel(instalacao, servico):
    """O stub apaga o venv quando ve o sentinel. Um dry-run que o escrevesse
    faria o `--dry-run` apagar o venv, que e o oposto do que ele promete."""
    installer.uninstall(dry_run=True)
    assert not installer.venv_pending_path().exists()


def test_uma_recusa_NAO_escreve_o_sentinel(instalacao, servico):
    servico["no_ar"] = [True]
    installer.uninstall()
    assert not installer.venv_pending_path().exists()


def test_o_sentinel_nao_aparece_se_nao_ha_venv(instalacao, servico):
    import shutil as _shutil
    _shutil.rmtree(instalacao / "venv")
    installer.uninstall()
    assert not installer.venv_pending_path().exists()


def test_o_caminho_do_sentinel_segue_o_CONFIG_DIR_do_momento(instalacao):
    """Se fosse `CONFIG_DIR / nome` no topo do modulo, congelaria no import e um
    teste escreveria dentro da instalacao de verdade. Ja aconteceu com
    `config.CONFIG_FILE`."""
    assert installer.venv_pending_path().parent == instalacao


def test_o_sentinel_nao_e_removido_pelo_proprio_uninstall(instalacao, servico):
    """Ele e escrito no fim, e o stub e quem o apaga. Se entrasse nas listas de
    remocao, o proprio uninstall o apagaria antes de o stub olhar."""
    assert installer.VENV_PENDING_NAME not in installer.REMOVE_FILES
    installer.uninstall()
    assert installer.venv_pending_path().exists()


# ── a guarda do vault respondia "pode remover" quando nao conseguia conferir ──
#
# Achado em 03-04/09 por varredura sistematica: um script listou todo `except`
# do src/ cujo corpo e so um `return` de valor de aparencia segura, 78 no total,
# e cruzou cada um com o docstring da funcao que o contem. Este saltou:
#
#   installer.py:433  vault_is_inside_config_dir -> False
#                     "Would removing CONFIG_DIR's contents also remove vault content?"
#
# E a MESMA guarda que `wizard._conflicts_with_config_dir` faz na entrada, e o
# mesmo defeito nas duas: responder "sem conflito" quando a resposta real e "nao
# consegui conferir". A do wizard so impede uma ESCOLHA ruim; esta e a que
# decide se o desinstalador puxa o gatilho.
#
# O relatorio do uninstall ja escreve a incerteza em voz alta:
#   "vault": vault or "unknown (config.json missing or unreadable)"
# e seguia removendo assim mesmo. Nomear a duvida na saida e agir como se
# houvesse certeza e a familia inteira que este job passou a noite corrigindo.
#
# O custo do erro nao e teorico: REMOVE_DIRS comeca em "sessions", e as pastas
# que o proprio wizard sugere sao ["decisions", "research", "tools", "fixes",
# "reference", "sessions"], em minusculas. O casamento e exato, em qualquer
# sistema de arquivos.


def test_um_vault_dentro_do_config_dir_recusa_o_uninstall(cfg_dir, monkeypatch):
    """O caso que ja funcionava: pinado para nao regredir junto com a correcao."""
    (cfg_dir / "config.json").write_text(
        json.dumps({"vault_path": str(cfg_dir / "meu-vault")}), encoding="utf-8")

    r = installer.uninstall(dry_run=True)

    assert r["status"] == "refused_vault_inside_config_dir"


def test_config_ilegivel_recusa_em_vez_de_assumir_que_nao_ha_vault(cfg_dir, monkeypatch):
    """config.json existe e nao abre: o vault pode estar em qualquer lugar.

    `configured_vault_path()` devolve "" nesse caso, e `vault_is_inside_config_dir("")`
    devolve False, entao a remocao por nome exato seguia adiante sem que ninguem
    soubesse onde o vault esta.
    """
    (cfg_dir / "config.json").write_text("{ isto nao e json", encoding="utf-8")
    (cfg_dir / "sessions").mkdir(exist_ok=True)

    r = installer.uninstall(dry_run=True)

    assert r["status"] == "refused_config_unreadable", (
        "um uninstall que nao sabe onde o vault esta nao pode remover por nome"
    )
    assert "plan" not in r, "nem o plano de remocao deve ser montado"


def test_config_ausente_nao_recusa(cfg_dir, monkeypatch):
    """Sem config.json nunca houve vault configurado, e o que esta em CONFIG_DIR
    e nosso. Recusar aqui deixaria o software impossivel de remover."""
    r = installer.uninstall(dry_run=True)
    assert r["status"] == "dry_run"


def test_a_guarda_recusa_quando_o_caminho_nao_pode_ser_resolvido(monkeypatch):
    """`except OSError: return False` dizia "pode remover" para um caminho que
    nao pode nem ser resolvido."""
    def _explode(self, strict=False):
        raise OSError("loop de link simbolico")

    monkeypatch.setattr(installer.Path, "resolve", _explode)

    assert installer.vault_is_inside_config_dir("/algum/vault") is True


def test_um_vault_de_verdade_fora_do_config_dir_continua_removivel(cfg_dir, tmp_path):
    """A correcao nao pode transformar todo uninstall numa recusa."""
    vault = tmp_path / "vault-de-verdade"
    vault.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({"vault_path": str(vault)}), encoding="utf-8")

    r = installer.uninstall(dry_run=True)

    assert r["status"] == "dry_run"
    assert r["vault_path"] == str(vault)


def test_o_detalhe_do_venv_nao_contradiz_o_booleano_ao_lado(cfg_dir, tmp_path, monkeypatch):
    """`detail` afirmava "this process is running from inside it" mesmo quando
    `running_from_it` dizia False, no mesmo dicionario, uma chave acima."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({"vault_path": str(vault)}), encoding="utf-8")
    (cfg_dir / "venv").mkdir(exist_ok=True)
    monkeypatch.setattr(installer, "_running_from", lambda d: False)

    r = installer.uninstall(dry_run=False)

    assert "venv_pending" in r, f"uninstall parou antes do venv: {r.get('status')} / {r.get('detail')}"
    pend = r["venv_pending"]
    assert pend["running_from_it"] is False
    assert "running from inside it" not in pend["detail"], (
        f"o texto afirma o contrario do campo ao lado: {pend}"
    )

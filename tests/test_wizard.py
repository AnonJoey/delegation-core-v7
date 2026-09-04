"""wizard.py — o ultimo modulo do nucleo sem nenhum teste.

708 linhas, 8% de cobertura: 393 dos 433 comandos nunca executados pela suite.
Ficou de fora do trabalho de 02-03/09 com o argumento de que "roda uma vez por
maquina e o retorno nao paga o esforco". Rodar uma vez por maquina e o que torna
o defeito caro: ele acontece na primeira impressao, com o usuario novo, e
ninguem esta olhando.

Os dois defeitos abaixo estao na MESMA funcao cujo comentario ja registra que
esses caminhos sao hostis:

    # ExecStart= uses systemd's own shell-like word splitting on
    # whitespace, paths must be quoted or a space anywhere in the home
    # directory, models dir, or binary path (all user-controlled) splits
    # into the wrong number of arguments.

Quem escreveu isso sabia que binario, modelo e log vem do usuario. Tratou UM
metacaractere, o espaco, e deixou os outros dois: o `%` do systemd e o `&` do
XML do launchd.

MEDIDO NESTA MAQUINA em 03/09/2026, com systemd-analyze e com o unit carregado
de verdade::

    ExecStart="/home/joey/100%hits/llama-server" --model "/data/%name%.gguf"
      -> path=/home/joey/100/home/joeyits/llama-server
      -> argv[]=... --model /data/zz-teste-especificador.serviceame%.gguf

`%h` virou o diretorio home e `%n` virou o nome da unit. O servico aponta para
um caminho que nao existe, o motor nunca sobe no login, e o wizard imprimiu
"AI engine will start automatically at login".
"""
from __future__ import annotations

import platform
import xml.dom.minidom

import pytest

from delegation_core import wizard
from delegation_core.config import Config


class _SubprocessMudo:
    """Substitui o NOME `subprocess` no namespace do wizard.

    O wizard chama systemctl/launchctl de verdade nestas funcoes; um teste que
    os deixasse rodar mexeria nos servicos da maquina.
    """

    def run(self, *a, **k):
        return None


@pytest.fixture
def cfg_hostil(tmp_path, monkeypatch):
    """Config cujos caminhos carregam os metacaracteres que quebram cada formato."""
    cfg = Config(vault_path=str(tmp_path / "vault"), engine_mode="local")
    cfg.llama_binary = "/home/joey/100%hits/AI & ML/llama-server"
    cfg.llama_model = "/data/%name%/modelo <v2>.gguf"
    return cfg


def _unit_escrito(monkeypatch, tmp_path, cfg) -> str:
    from delegation_core import service as svc
    destino = tmp_path / "unit.service"
    monkeypatch.setattr(svc, "LLAMA_SYSTEMD_UNIT", destino)
    # setattr(wizard.subprocess, "run", ...) remendaria o modulo subprocess do
    # PROCESSO INTEIRO, e nao a visao do wizard: `wizard.subprocess` E o modulo.
    # Isso quebrou este proprio arquivo, onde um teste chama systemd-analyze
    # depois e recebia None. Trocar o NOME dentro do namespace do wizard e o que
    # isola de verdade.
    monkeypatch.setattr(wizard, "subprocess", _SubprocessMudo())
    wizard._startup_systemd(cfg)
    return destino.read_text(encoding="utf-8")


def _plist_escrito(monkeypatch, tmp_path, cfg) -> str:
    from delegation_core import service as svc
    destino = tmp_path / "agente.plist"
    monkeypatch.setattr(svc, "LLAMA_LAUNCHD_PLIST", destino)
    monkeypatch.setattr(wizard, "subprocess", _SubprocessMudo())
    monkeypatch.setattr(wizard.Path, "home", staticmethod(lambda: tmp_path))
    wizard._startup_launchd(cfg)
    return destino.read_text(encoding="utf-8")


# ── systemd: o especificador `%` ────────────────────────────────────────────


def test_o_unit_escapa_o_porcento_dos_caminhos_do_usuario(cfg_hostil, tmp_path, monkeypatch):
    """Sem isto, `%h` no caminho vira o diretorio home dentro do ExecStart."""
    unit = _unit_escrito(monkeypatch, tmp_path, cfg_hostil)

    linha = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert "100%%hits" in linha, (
        f"o % do caminho chegou cru no unit e o systemd vai expandi-lo: {linha}"
    )
    assert "%name%" not in linha or "%%name%%" in linha


@pytest.mark.skipif(platform.system() != "Linux", reason="precisa do systemd-analyze")
def test_o_systemd_de_verdade_le_o_caminho_que_o_usuario_tem(cfg_hostil, tmp_path, monkeypatch):
    """Nao acredita no escape: pergunta ao proprio systemd o que ele entendeu.

    Este e o teste que encontrou o defeito, com o unit carregado de verdade e
    `systemctl show -p ExecStart` devolvendo o caminho ja expandido.
    """
    import shutil
    import subprocess as sp
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze ausente")

    unit = _unit_escrito(monkeypatch, tmp_path, cfg_hostil)
    destino = tmp_path / "zz-teste-wizard.service"
    destino.write_text(unit, encoding="utf-8")

    saida = sp.run(["systemd-analyze", "verify", str(destino)],
                   capture_output=True, text=True).stderr

    # systemd-analyze reclama que o comando nao existe, e a reclamacao carrega o
    # caminho JA EXPANDIDO: e ali que a substituicao aparece.
    assert "/home/joey/100/home/joey" not in saida, (
        f"o systemd expandiu %h dentro do caminho do usuario: {saida.strip()}"
    )


def test_o_unit_continua_citando_os_caminhos_com_espaco(cfg_hostil, tmp_path, monkeypatch):
    """A defesa que ja existia nao pode sair junto com a correcao da nova."""
    unit = _unit_escrito(monkeypatch, tmp_path, cfg_hostil)
    linha = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert linha.count('"') >= 4, f"binario e modelo tem que sair citados: {linha}"


def test_um_caminho_sem_metacaractere_nao_e_alterado(tmp_path, monkeypatch):
    """Escapar nao pode deformar o caminho comum, que e o caso de todo mundo."""
    cfg = Config(vault_path=str(tmp_path), engine_mode="local")
    cfg.llama_binary = "/home/joey/.delegation_core/llama/llama-server"
    cfg.llama_model = "/home/joey/.delegation_core/models/gemma-12b.gguf"

    unit = _unit_escrito(monkeypatch, tmp_path, cfg)

    assert "/home/joey/.delegation_core/llama/llama-server" in unit
    assert "/home/joey/.delegation_core/models/gemma-12b.gguf" in unit
    assert "%%" not in unit


# ── launchd: o XML ──────────────────────────────────────────────────────────


def test_o_plist_e_xml_bem_formado_com_e_comercial_no_caminho(cfg_hostil, tmp_path, monkeypatch):
    """`&` e `<` sao legais num caminho do macOS e ilegais crus em XML.

    Sem escape o arquivo sai malformado e `launchctl load` recusa, entao o motor
    nunca sobe. Medido: "not well-formed (invalid token)".
    """
    plist = _plist_escrito(monkeypatch, tmp_path, cfg_hostil)

    xml.dom.minidom.parseString(plist)   # levanta se estiver malformado


def test_o_plist_preserva_o_caminho_real_e_nao_so_escapa(cfg_hostil, tmp_path, monkeypatch):
    """Escapar o XML nao pode mudar o caminho que o launchd vai executar."""
    plist = _plist_escrito(monkeypatch, tmp_path, cfg_hostil)

    doc = xml.dom.minidom.parseString(plist)
    argumentos = [n.firstChild.data if n.firstChild else ""
                  for n in doc.getElementsByTagName("array")[0].getElementsByTagName("string")]

    assert argumentos[0] == cfg_hostil.llama_binary
    assert cfg_hostil.llama_model in argumentos


def test_o_plist_de_um_caminho_comum_continua_valido(tmp_path, monkeypatch):
    cfg = Config(vault_path=str(tmp_path), engine_mode="local")
    cfg.llama_binary = "/Users/joey/.delegation_core/llama/llama-server"
    cfg.llama_model = "/Users/joey/.delegation_core/models/gemma.gguf"

    plist = _plist_escrito(monkeypatch, tmp_path, cfg)

    xml.dom.minidom.parseString(plist)
    assert "/Users/joey/.delegation_core/llama/llama-server" in plist


# ── a guarda que protege o vault do desinstalador ───────────────────────────


def test_o_proprio_config_dir_e_recusado_como_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path / ".delegation_core")
    (tmp_path / ".delegation_core").mkdir()
    assert wizard._conflicts_with_config_dir(tmp_path / ".delegation_core") is True


def test_uma_pasta_dentro_do_config_dir_e_recusada(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path / ".delegation_core")
    (tmp_path / ".delegation_core" / "vault").mkdir(parents=True)
    assert wizard._conflicts_with_config_dir(tmp_path / ".delegation_core" / "vault") is True


def test_uma_pasta_irma_e_aceita(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path / ".delegation_core")
    (tmp_path / ".delegation_core").mkdir()
    (tmp_path / "vault").mkdir()
    assert wizard._conflicts_with_config_dir(tmp_path / "vault") is False


def test_um_link_simbolico_para_dentro_do_config_dir_e_recusado(monkeypatch, tmp_path):
    """resolve() segue o link, que e o motivo de ele estar ali."""
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path / ".delegation_core")
    (tmp_path / ".delegation_core" / "vault").mkdir(parents=True)
    atalho = tmp_path / "atalho"
    atalho.symlink_to(tmp_path / ".delegation_core" / "vault")

    assert wizard._conflicts_with_config_dir(atalho) is True


def test_quando_nao_da_para_resolver_o_caminho_a_guarda_nao_diz_que_esta_tudo_bem(
        monkeypatch, tmp_path):
    """`except OSError: return False` respondia "sem conflito" para um caminho
    que nao pode nem ser resolvido.

    E a mesma familia que este projeto vem corrigindo a noite inteira: uma
    verificacao que PASSA quando nao consegue verificar. `_unindexed_notes` ja
    tem a regra escrita no proprio docstring: degradar para "nao da para saber",
    nunca para "esta tudo certo". Aqui o custo de errar e o desinstalador apagar
    conteudo de vault.
    """
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path / ".delegation_core")
    (tmp_path / ".delegation_core").mkdir()

    class _CaminhoQueNaoResolve(type(tmp_path)):
        def resolve(self, strict=False):
            raise OSError("loop de link simbolico")

    assert wizard._conflicts_with_config_dir(_CaminhoQueNaoResolve(tmp_path / "x")) is True


# ── os menus, que sao a porta de entrada do usuario novo ────────────────────


def _responde(monkeypatch, *respostas):
    fila = list(respostas)
    monkeypatch.setattr(wizard.console, "input", lambda *a, **k: fila.pop(0))


def test_menu_index_devolve_indice_base_zero(monkeypatch):
    _responde(monkeypatch, "2")
    assert wizard._menu_index("Escolha", 3) == 1


def test_menu_index_recusa_numero_fora_da_faixa_e_pergunta_de_novo(monkeypatch):
    _responde(monkeypatch, "9", "0", "1")
    assert wizard._menu_index("Escolha", 3) == 0


def test_menu_index_recusa_texto(monkeypatch):
    _responde(monkeypatch, "tres", "3")
    assert wizard._menu_index("Escolha", 3) == 2


def test_menu_index_aceita_espaco_em_volta(monkeypatch):
    _responde(monkeypatch, "  2  ")
    assert wizard._menu_index("Escolha", 3) == 1


def test_menu_devolve_indice_base_zero(monkeypatch):
    _responde(monkeypatch, "3")
    assert wizard._menu("Titulo", ["a", "b", "c"]) == 2


def test_menu_recusa_fora_da_faixa(monkeypatch):
    _responde(monkeypatch, "4", "1")
    assert wizard._menu("Titulo", ["a", "b", "c"]) == 0

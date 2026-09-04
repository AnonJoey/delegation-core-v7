"""A saude nao distinguia "vault vazio" de "vault sumiu".

Achado por injecao de falha, que e a trilha que faltava: em vez de ler o codigo
procurando defeito, tirar o chao dele e ver o que ele responde.

MEDIDO em 04/09/2026, com `vault_path` apontando para um diretorio inexistente:

    total_notes            0
    needs_repair           0
    truncated              0
    orphans                0
    broken_links           0
    unindexed              0
    malformed_frontmatter  0

Nenhum erro, nenhuma ressalva. Quem le `heartbeat()` conclui que o vault esta
saudavel e vazio. E o mesmo desfecho de um vault de 8.593 notas que acabou de
ser desmontado, renomeado, ou que mora num drive que nao subiu.

Nesta maquina isso nao e hipotetico: `proc_8c7edd` e um processo aberto sobre
mover pastas entre tres origens, e registrou 143 caminhos mortos encontrados
numa passada.

E a mesma regra que `_unindexed_notes` ja carrega no proprio docstring: "Health
must degrade to 'cannot tell' rather than to 'all fine'". Ela valia para o
indice ilegivel e nao valia para o vault ausente.

## O QUE NAO MUDA

Uma PASTA configurada que nao existe continua sendo normal e silenciosa: um
vault que nao usa `Tools/` nao tem `Tools/`, e o scan ja pula. O alarme e so
para a RAIZ.
"""
from __future__ import annotations

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


def _vault(caminho, folders=("Sessions", "Reference")):
    cfg = Config(vault_path=str(caminho), vault_folders=list(folders))
    v = VaultManager(cfg)
    v._ensure_ready = lambda: None
    v.collection = None
    return v


def test_vault_inexistente_nao_e_reportado_como_saudavel(tmp_path):
    v = _vault(tmp_path / "nao-existe")

    saude = v.get_health_summary(force=True)

    assert saude.get("vault_unreadable"), (
        "todas as metricas em zero e nenhuma ressalva: quem le conclui que o "
        "vault esta saudavel e vazio"
    )


def test_a_ressalva_diz_qual_caminho_falhou(tmp_path):
    alvo = tmp_path / "vault-que-sumiu"
    v = _vault(alvo)

    aviso = v.get_health_summary(force=True)["vault_unreadable"]

    assert str(alvo) in aviso


def test_um_vault_vazio_de_verdade_nao_levanta_a_ressalva(tmp_path):
    """A distincao inteira: existe e nao tem nota e um estado legitimo."""
    (tmp_path / "Sessions").mkdir()
    (tmp_path / "Reference").mkdir()

    saude = _vault(tmp_path).get_health_summary(force=True)

    assert not saude.get("vault_unreadable")
    assert saude["total_notes"] == 0


def test_uma_pasta_configurada_ausente_continua_normal(tmp_path):
    """Um vault que nao usa Tools/ nao tem Tools/. So a RAIZ e alarme."""
    (tmp_path / "Sessions").mkdir()
    (tmp_path / "Sessions" / "a.md").write_text("---\ntitle: a\n---\n\nx\n", encoding="utf-8")

    saude = _vault(tmp_path, folders=("Sessions", "Tools", "Reference")).get_health_summary(force=True)

    assert not saude.get("vault_unreadable")
    assert saude["total_notes"] == 1


def test_vault_que_e_um_arquivo_e_nao_uma_pasta_tambem_avisa(tmp_path):
    alvo = tmp_path / "isto-e-um-arquivo"
    alvo.write_text("nao sou um vault", encoding="utf-8")

    assert _vault(alvo).get_health_summary(force=True).get("vault_unreadable")


def test_um_vault_com_notas_continua_reportando_normalmente(tmp_path):
    """A ressalva nao pode aparecer no caminho feliz."""
    (tmp_path / "Sessions").mkdir()
    (tmp_path / "Sessions" / "a.md").write_text("---\ntitle: a\n---\n\nx\n", encoding="utf-8")
    (tmp_path / "Reference").mkdir()

    saude = _vault(tmp_path).get_health_summary(force=True)

    assert "vault_unreadable" not in saude
    assert saude["total_notes"] == 1

"""A guarda da suite tem que funcionar, e tem que ser provada funcionando.

Um fixture autouse que nao faz nada e indistinguivel de um que faz, ate o dia
em que alguem precisa dele. Estes testes chamam os mesmos caminhos que
quebraram a maquina em 02/09/2026 e verificam que agora eles caem no tmp_path.
"""
from __future__ import annotations

from pathlib import Path

from delegation_core import config as config_mod
from delegation_core.config import Config

REAL = Path.home() / ".delegation_core" / "config.json"


def test_config_file_aponta_para_fora_da_casa_do_usuario():
    assert config_mod.CONFIG_FILE != REAL
    assert Path.home() not in config_mod.CONFIG_FILE.parents or \
        ".delegation_core" not in str(config_mod.CONFIG_FILE.parent.name) or \
        str(config_mod.CONFIG_FILE).startswith("/tmp")


def test_save_escreve_no_temporario_e_nao_na_config_real(tmp_path):
    """O caminho exato que apagou a config do usuario."""
    antes = REAL.read_bytes() if REAL.exists() else None

    cfg = Config(vault_path=str(tmp_path / "um-vault-qualquer"))
    cfg.tok_sec = 123.45
    cfg.save()

    assert config_mod.CONFIG_FILE.exists(), "save() nao escreveu onde deveria"
    assert "um-vault-qualquer" in config_mod.CONFIG_FILE.read_text()

    depois = REAL.read_bytes() if REAL.exists() else None
    assert antes == depois, "save() alcancou a config real da maquina"


def test_calibrate_nao_alcanca_a_config_real(tmp_path, monkeypatch):
    """calibrate() termina em cfg.save(). Foi por ali que o estrago passou."""
    import asyncio

    from delegation_core.engine import DelegationEngine

    antes = REAL.read_bytes() if REAL.exists() else None

    class _Resposta:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "1\n2\n3"}}],
                    "usage": {"completion_tokens": 40}}

    async def _post(url, json=None, **kw):          # noqa: A002
        return _Resposta()

    async def _ensure(force=False):
        return True

    engine = DelegationEngine(Config(vault_path=str(tmp_path)))
    monkeypatch.setattr(engine, "ensure_running", _ensure)
    monkeypatch.setattr(engine._async_client, "post", _post)
    asyncio.run(engine.calibrate())

    depois = REAL.read_bytes() if REAL.exists() else None
    assert antes == depois, "calibrate() gravou por cima da config real"


def test_cada_teste_recebe_um_diretorio_proprio(tmp_path):
    """Sem isolamento por teste, um teste enxerga o estado que o anterior deixou."""
    assert str(tmp_path) in str(config_mod.CONFIG_DIR)

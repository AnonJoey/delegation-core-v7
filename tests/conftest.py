"""Guarda de suite: nenhum teste escreve em ~/.delegation_core.

Isto existe por causa de um estrago real, nao de uma preocupacao teorica.

Em 02/09/2026 um teste novo chamou `DelegationEngine.calibrate()`. O ultimo
que calibrate faz e `self.cfg.save()`, e `Config.save()` escreve em
`config.CONFIG_FILE`, que e uma constante de modulo apontando para
`~/.delegation_core/config.json`. O objeto Config nao sabe de onde veio: um
Config construido pelo teste com `vault_path=tmp_path` grava por cima da
config real da maquina exatamente como o Config carregado dela gravaria.

O resultado: o `config.json` do usuario passou a apontar para
`/tmp/pytest-of-joey/pytest-76/...`, o daemon subiu, nao encontrou vault,
imprimiu "delegation-core is not configured", e o systemd desistiu depois de
cinco tentativas. A maquina ficou sem servico ate a config ser reescrita a mao.

Ja tinha acontecido antes. O HANDOFF registra a correcao de 28/07: o fixture de
`test_dashboard_api_routes.py` passou a apontar CONFIG_DIR/CONFIG_FILE para
tmp_path. A correcao foi feita em UM arquivo, e a suite tem 65. Uma defesa que
depende de cada autor de teste lembrar dela nao e uma defesa.

Por isso o redirecionamento aqui e `autouse=True`: vale para todo teste da
suite, escrito antes ou depois, sem que ninguem precise pedir. Um teste que
QUEIRA exercitar o caminho de escrita continua podendo, e escreve no tmp_path
que este fixture instalou.
"""
from __future__ import annotations

from pathlib import Path

import pytest


#: Modulos que carregam CONFIG_DIR/CONFIG_FILE de `config` por `from ... import`
#: e portanto guardam a propria referencia, que reapontar `config.CONFIG_DIR`
#: sozinho nao alcanca.
_MODULOS_COM_COPIA = (
    "delegation_core.cli",
    "delegation_core.dashboard_api",
    "delegation_core.wizard",
    "delegation_core.doctor",
    "delegation_core.service",
    "delegation_core.clients",
    "delegation_core.windows",
    "delegation_core.localqueue",
    "delegation_core.tracker",
    "delegation_core.downloader",
)


@pytest.fixture(autouse=True)
def _sem_escrita_no_estado_real(tmp_path, monkeypatch):
    """Reaponta todo caminho de estado do usuario para um diretorio temporario."""
    import importlib

    from delegation_core import config as config_mod

    raiz = tmp_path / "estado_delegation_core"
    raiz.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config_mod, "CONFIG_DIR", raiz, raising=False)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", raiz / "config.json", raising=False)

    # `from .config import CONFIG_DIR` copia o valor no import. Reapontar so o
    # modulo de origem deixa essas copias apontando para a casa do usuario.
    for nome in _MODULOS_COM_COPIA:
        try:
            mod = importlib.import_module(nome)
        except Exception:
            continue          # extra opcional ausente nao pode quebrar a suite
        for atributo, valor in (("CONFIG_DIR", raiz),
                                ("CONFIG_FILE", raiz / "config.json"),
                                ("STORE_PATH", raiz / "local_tasks.json")):
            if hasattr(mod, atributo):
                monkeypatch.setattr(mod, atributo, valor, raising=False)

    yield raiz


@pytest.fixture(autouse=True)
def _config_real_intacta():
    """Rede de seguranca: falha o teste que ainda assim tocar a config real.

    O fixture acima cobre os caminhos conhecidos. Este cobre os que ninguem
    mapeou ainda, e falha o teste culpado em vez de deixar a maquina quebrada
    para quem for rodar a suite depois.
    """
    real = Path.home() / ".delegation_core" / "config.json"
    antes = real.read_bytes() if real.exists() else None

    yield

    depois = real.read_bytes() if real.exists() else None
    if antes != depois:
        # Devolve o arquivo antes de falhar: o teste ja errou, a maquina do
        # usuario nao precisa pagar por isso.
        if antes is None:
            real.unlink(missing_ok=True)
        else:
            real.write_bytes(antes)
        pytest.fail(
            f"o teste escreveu em {real}. Nenhum teste pode tocar o estado real "
            "da maquina: use o tmp_path que conftest._sem_escrita_no_estado_real "
            "ja instalou. O arquivo foi restaurado."
        )

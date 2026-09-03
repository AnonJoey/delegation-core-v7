"""Os scripts de shell que o usuario executa a mao, e o que nao pode estar neles.

Dois defeitos ja catalogados neste projeto:

1. Um travessao (ou qualquer byte fora de ASCII) dentro de um comentario `::`
   de um `.bat` quebra o parser do `cmd.exe` para o arquivo INTEIRO, nao so para
   a linha. `uninstall.bat` tinha quatro travessoes e 239 caracteres de
   box-drawing em linhas `::`, sem `chcp 65001`.

2. Cada tarefa escrita duas vezes, uma em bash e uma em batch, deriva. As duas
   metades do uninstall ja tinham divergido: a de bash conferia o resultado das
   remocoes, a de batch descartava todo erro com `>nul 2>&1`.

O defeito 2 se resolve encolhendo os scripts ate sobrar so o que Python nao faz
por si: achar o interpretador, e apagar o venv depois que ele sai. O tamanho e
portanto o teste: um script que volta a crescer voltou a duplicar logica.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

BATCH = ("install.bat", "uninstall.bat")
SHELL = ("install.sh", "uninstall.sh")


def _ler(nome: str) -> str:
    caminho = RAIZ / nome
    if not caminho.exists():
        pytest.skip(f"{nome} nao esta neste checkout")
    return caminho.read_text(encoding="utf-8")


# ── o parser do cmd.exe ──────────────────────────────────────────────────────

@pytest.mark.parametrize("nome", BATCH)
def test_batch_e_ascii_puro(nome):
    """Nao so nos comentarios: um `.bat` sem `chcp 65001` le o arquivo na code
    page do sistema, e um byte alto em qualquer lugar vira outra coisa."""
    texto = _ler(nome)
    fora = sorted({c for c in texto if ord(c) > 127})
    assert not fora, (
        f"{nome} tem caracteres fora de ASCII: {fora!r}. "
        "Um deles num comentario `::` quebra o cmd.exe para o arquivo inteiro."
    )


@pytest.mark.parametrize("nome", BATCH)
def test_batch_comenta_com_rem_e_nao_com_dois_pontos(nome):
    """`::` e um rotulo invalido que o cmd tolera, e para de tolerar dentro de
    blocos entre parenteses e diante de bytes altos. `rem` e um comando."""
    linhas = [ln for ln in _ler(nome).splitlines() if ln.strip().startswith("::")]
    assert not linhas, f"{nome} usa `::` em {len(linhas)} linha(s); use `rem`"


# ── o travessao, que e regra do projeto em qualquer arquivo ──────────────────

@pytest.mark.parametrize("nome", BATCH + SHELL)
def test_sem_travessao(nome):
    texto = _ler(nome)
    for traco, rotulo in (("—", "em dash"), ("–", "en dash")):
        assert traco not in texto, f"{nome} tem um {rotulo}"


# ── o tamanho, que e a medida da duplicacao ──────────────────────────────────

@pytest.mark.parametrize("nome,teto", [("uninstall.sh", 100), ("uninstall.bat", 110)])
def test_o_stub_nao_voltou_a_crescer(nome, teto):
    """Medido: uninstall.sh saiu de 193 linhas e uninstall.bat de 158, com a
    logica de ambos agora em installer.uninstall(). Se um deles passar destes
    tetos, alguem reescreveu em shell algo que ja existe em Python."""
    linhas = len(_ler(nome).splitlines())
    assert linhas <= teto, (
        f"{nome} tem {linhas} linhas (teto {teto}). Logica de uninstall pertence "
        "a installer.py, onde as tres plataformas a compartilham."
    )


# ── o contrato entre o stub e o Python ───────────────────────────────────────

def _linhas_executadas(texto: str) -> list[str]:
    """As linhas que rodam, sem comentarios nem as que so imprimem texto.

    Os dois stubs IMPRIMEM os comandos de remocao manual quando o interpretador
    esta faltando, que e ajuda legitima e nao logica duplicada. Distinguir uma
    coisa da outra e o trabalho desta funcao.
    """
    linhas = []
    for bruta in texto.splitlines():
        ln = bruta.strip()
        if not ln or ln.startswith(("#", "rem ", "REM ", "::")):
            continue
        if ln.startswith("echo") or ln.startswith("@echo"):
            continue
        linhas.append(ln)
    return linhas


@pytest.mark.parametrize("nome", ("uninstall.sh", "uninstall.bat"))
def test_o_stub_chama_o_subcomando_e_nao_reimplementa(nome):
    texto = _ler(nome)
    assert "uninstall" in texto and ("delegation-core" in texto)

    executadas = "\n".join(_linhas_executadas(texto))
    for proibido in ("systemctl", "launchctl", "schtasks"):
        assert proibido not in executadas, (
            f"{nome} voltou a mexer em servico por conta propria ({proibido}); "
            "isso e trabalho de service.py, que conhece as DUAS registracoes. "
            "Foi exatamente assim que a unit do daemon ficou para tras."
        )


def test_a_ajuda_manual_do_stub_menciona_as_duas_units():
    """Quando o venv ja esta quebrado o stub nao tem como chamar o Python, e o
    unico caminho e o usuario remover a mao. A instrucao impressa ali precisa
    citar as duas units, ou reproduz o defeito original em forma de texto."""
    for nome in ("uninstall.sh", "uninstall.bat"):
        texto = _ler(nome)
        assert "delegation-core-llama" in texto, nome
        impressas = [ln for ln in texto.splitlines() if ln.strip().startswith("echo")]
        juntas = "\n".join(impressas)
        assert "delegation-core-llama" in juntas and "delegation-core" in juntas, nome


@pytest.mark.parametrize("nome", ("uninstall.sh", "uninstall.bat"))
def test_o_stub_decide_pelo_sentinel_e_nao_pelo_codigo_de_saida(nome):
    """Saida 0 tambem quer dizer "o usuario nao digitou yes" e "--dry-run".
    Um stub que apagasse o venv por codigo de saida destruiria a instalacao de
    quem acabou de recusar o uninstall."""
    from delegation_core import installer

    texto = _ler(nome)
    assert installer.VENV_PENDING_NAME in texto, (
        f"{nome} nao consulta o sentinel {installer.VENV_PENDING_NAME}"
    )


def test_o_nome_do_sentinel_e_o_mesmo_dos_dois_lados():
    """O unico acoplamento que sobrou entre shell e Python. Se ele quebrar, o
    venv simplesmente nunca e removido, sem erro nenhum: exatamente o tipo de
    falha silenciosa que este arquivo existe para pegar."""
    from delegation_core import installer

    for nome in ("uninstall.sh", "uninstall.bat"):
        assert installer.VENV_PENDING_NAME in _ler(nome), nome

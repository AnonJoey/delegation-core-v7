"""O roteamento explicito do sidecar era ignorado em silencio nesta maquina.

Um sidecar e um `<stem>.meta.yaml` largado ao lado do arquivo no inbox. O
`folder_hint` dele existe para **contornar o classificador do modelo**: quem
escreve o sidecar ja sabe onde a nota deve ir.

Medido em 03/09/2026, vault com as pastas `Sessions`, `Decisions`, `Reference`:
os hints `sessions`, `decisions`, `sessions/2026` e `reference/x` eram TODOS
rejeitados, porque a validacao era `head in vault_folders`, sensivel a caixa.
O arquivo caia no classificador do modelo em vez de ir para onde o sidecar
mandou, sem nada em log dizendo que o hint foi descartado.

A docstring de `config.resolve_folder` descreve exatamente esta armadilha e diz
que os defaults de fabrica sao minusculos enquanto vaults configurados pelo
wizard usam nomes capitalizados. O helper foi escrito para isso e este modulo
nunca o adotou.

`sidecar.py` era um dos nove modulos do nucleo sem nenhum teste.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core import sidecar

PASTAS = ["Projects", "Decisions", "Fixes", "Sessions", "Reference"]


# ── o defeito ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hint,esperado", [
    ("sessions",   "Sessions"),
    ("Sessions",   "Sessions"),
    ("SESSIONS",   "Sessions"),
    ("decisions",  "Decisions"),
    ("reference",  "Reference"),
])
def test_hint_e_aceito_em_qualquer_caixa(hint, esperado):
    assert sidecar.resolve_folder_hint(hint, PASTAS) == esperado


def test_a_pasta_devolvida_tem_a_caixa_do_vault():
    """Nao basta aceitar: o chamador usa o retorno como caminho.

    Se `sessions` fosse aceito e usado cru, o organizer criaria uma pasta
    `sessions/` ao lado da `Sessions/` que o vault ja tem. Era por isso que
    tornar so a validacao insensivel seria uma correcao pela metade.
    """
    assert sidecar.resolve_folder_hint("sessions", PASTAS) == "Sessions"
    assert sidecar.resolve_folder_hint("sessions", PASTAS) != "sessions"


def test_subcaminho_e_preservado_com_a_raiz_canonizada():
    assert sidecar.resolve_folder_hint("reference/Acme/2026", PASTAS) == "Reference/Acme/2026"
    assert sidecar.resolve_folder_hint("sessions/2026", PASTAS) == "Sessions/2026"


def test_pasta_inexistente_e_recusada():
    assert sidecar.resolve_folder_hint("naoexiste", PASTAS) is None
    assert sidecar.resolve_folder_hint("naoexiste/sub", PASTAS) is None


@pytest.mark.parametrize("hint", [None, "", "   ", 42, [], {"a": 1}, True])
def test_hint_que_nao_e_texto_util_e_recusado(hint):
    assert sidecar.resolve_folder_hint(hint, PASTAS) is None


def test_barras_nas_pontas_nao_atrapalham():
    assert sidecar.resolve_folder_hint("/Decisions/", PASTAS) == "Decisions"


def test_barra_sozinha_nao_vira_pasta():
    assert sidecar.resolve_folder_hint("/", PASTAS) is None


# ── deteccao e leitura ──────────────────────────────────────────────────────

@pytest.mark.parametrize("nome,eh", [
    ("doc.meta.yaml", True),
    ("doc.meta.yml", True),
    ("doc.yaml", False),
    ("doc.md", False),
    # "meta.yaml" sozinho NAO e sidecar: o formato e <stem>.meta.yaml, entao
    # um arquivo com esse nome exato nao acompanha nada.
    ("meta.yaml", False),
])
def test_reconhece_o_arquivo_de_sidecar(nome, eh):
    assert sidecar.is_sidecar(Path(nome)) is eh


def test_acha_o_sidecar_do_arquivo_principal(tmp_path):
    principal = tmp_path / "relatorio.pdf"
    principal.write_bytes(b"x")
    (tmp_path / "relatorio.meta.yaml").write_text("folder_hint: Decisions\n", encoding="utf-8")

    assert sidecar.sidecar_for(principal).name == "relatorio.meta.yaml"


def test_aceita_a_variante_yml(tmp_path):
    principal = tmp_path / "a.md"
    principal.write_text("x", encoding="utf-8")
    (tmp_path / "a.meta.yml").write_text("type: meeting\n", encoding="utf-8")

    assert sidecar.sidecar_for(principal).name == "a.meta.yml"


def test_sem_sidecar_devolve_None(tmp_path):
    principal = tmp_path / "a.md"
    principal.write_text("x", encoding="utf-8")
    assert sidecar.sidecar_for(principal) is None
    assert sidecar.load(principal) == {}


def test_yaml_quebrado_nao_derruba_o_pipeline(tmp_path, caplog):
    """Um sidecar mal escrito nao pode impedir o arquivo de ser processado."""
    import logging

    principal = tmp_path / "a.md"
    principal.write_text("x", encoding="utf-8")
    (tmp_path / "a.meta.yaml").write_text("isto: [nao: fecha\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert sidecar.load(principal) == {}
    assert "a.meta.yaml" in caplog.text, "falhou sem dizer qual arquivo"


def test_yaml_que_nao_e_dicionario_vira_dicionario_vazio(tmp_path):
    """Uma lista no topo quebraria todo `sc.get(...)` do organizer."""
    principal = tmp_path / "a.md"
    principal.write_text("x", encoding="utf-8")
    (tmp_path / "a.meta.yaml").write_text("- um\n- dois\n", encoding="utf-8")

    assert sidecar.load(principal) == {}


def test_le_as_chaves_declaradas(tmp_path):
    principal = tmp_path / "a.md"
    principal.write_text("x", encoding="utf-8")
    (tmp_path / "a.meta.yaml").write_text(
        "folder_hint: sessions\nno_merge: true\ntype: meeting\n"
        "client: Acme\ntopics:\n  - um\n  - dois\n", encoding="utf-8")

    sc = sidecar.load(principal)
    assert sc["folder_hint"] == "sessions"
    assert sc["no_merge"] is True
    assert sc["topics"] == ["um", "dois"]


# ── o bloco que vai para o prompt de sintese ────────────────────────────────

def test_bloco_vazio_e_explicito():
    assert sidecar.format_block({}) == "(none)"
    assert sidecar.format_block({"folder_hint": "Decisions"}) == "(none)"


def test_bloco_omite_a_chave_de_roteamento():
    bloco = sidecar.format_block({"folder_hint": "Decisions", "type": "meeting"})
    assert "folder_hint" not in bloco
    assert "- type: meeting" in bloco


def test_bloco_achata_lista():
    assert "- topics: um, dois" in sidecar.format_block({"topics": ["um", "dois"]})


def test_bloco_pula_valor_vazio():
    bloco = sidecar.format_block({"type": "meeting", "client": "", "topics": None})
    assert "client" not in bloco and "topics" not in bloco

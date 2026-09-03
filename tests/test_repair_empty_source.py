"""A limpeza do que o piso nao desfaz.

O piso de substancia impede que uma nota seja sintetizada a partir de um arquivo
sem texto. Ele nao desfaz as 22 que ja existem, e uma nota assim nao carrega
marca nenhuma: foi escrita pelo mesmo caminho de qualquer outra e ate o
`quality_score` saiu 1.0.

A evidencia esta na origem, que o pipeline preserva em `_processed/`. Reextrair
a origem com o piso de hoje responde a pergunta que ninguem podia fazer na
epoca: havia texto suficiente ali para justificar esta nota? Por isso a busca
parte das origens e nao das notas.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core import repair
from delegation_core.config import Config
from delegation_core.extractor import no_text_stub


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Um vault com uma origem sem texto, a nota inventada que saiu dela, e uma
    nota legitima ao lado que nao pode ser tocada."""
    v = tmp_path / "vault"
    (v / "_processed").mkdir(parents=True)
    (v / "Reference").mkdir(parents=True)

    deck = v / "_processed" / "GFK 30 Dias.pptx"
    deck.write_bytes(b"conteudo nao importa: extract esta trocado")

    inventada = v / "Reference" / "2026-04-25-GFK-30-Dias.md"
    inventada.write_text(
        "---\ntitle: GFK 30 Dias\nquality_score: 1.0\n---\n\n"
        "## Summary\nO conselho aprovou o plano de 30 dias.\n\n"
        "## Pessoas\n- Sarah Chen\n\n## Decisoes\n- Seguir com a fase 2\n",
        encoding="utf-8")

    legitima = v / "Reference" / "2026-04-25-Ata-do-conselho.md"
    legitima.write_text("---\ntitle: Ata\n---\n\n## Resumo\nReuniao de verdade.\n",
                        encoding="utf-8")

    toco = no_text_stub(deck, formato="PowerPoint (slides are images)",
                        unidade="Slides", total=30, extraido=0)
    monkeypatch.setattr(repair, "extract",
                        lambda f: toco if f.name.endswith(".pptx") else "texto real" * 90)

    cfg = Config(vault_path=str(v), server_token="t", server_port=8787)
    return cfg, v, inventada, legitima, toco


# ── encontrar: nao muda nada ────────────────────────────────────────────────

def test_encontra_a_nota_pela_origem(vault):
    cfg, _v, inventada, _leg, _toco = vault
    r = repair.encontrar(cfg)

    assert r["notes_total"] == 1
    assert r["found"][0]["notes"] == ["Reference/2026-04-25-GFK-30-Dias.md"]
    assert "GFK 30 Dias.pptx" in r["found"][0]["source"]


def test_encontrar_nao_toca_em_nada(vault):
    cfg, _v, inventada, _leg, _toco = vault
    antes = inventada.read_text(encoding="utf-8")
    repair.encontrar(cfg)
    assert inventada.read_text(encoding="utf-8") == antes


def test_nao_confunde_com_a_nota_legitima_ao_lado(vault):
    cfg, _v, _inv, legitima, _toco = vault
    r = repair.encontrar(cfg)
    achadas = [n for a in r["found"] for n in a["notes"]]
    assert legitima.name not in " ".join(achadas)


def test_origem_com_texto_nao_entra(vault, monkeypatch):
    cfg, v, _inv, _leg, _toco = vault
    monkeypatch.setattr(repair, "extract", lambda f: "um documento de verdade " * 40)
    assert repair.encontrar(cfg)["found"] == []


def test_a_busca_ignora_as_pastas_internas(vault):
    """`_processed`, `_inbox` e `_dump` nao sao lugar de nota do usuario, e a
    propria origem mora numa delas."""
    cfg, v, _inv, _leg, _toco = vault
    (v / "_dump").mkdir(exist_ok=True)
    (v / "_dump" / "2026-04-25-GFK-30-Dias.md").write_text("copia velha", encoding="utf-8")

    achadas = [n for a in repair.encontrar(cfg)["found"] for n in a["notes"]]
    assert not any(n.startswith("_") for n in achadas)


# ── aplicar: substituir pelo toco ───────────────────────────────────────────

def test_substitui_o_corpo_e_apaga_a_invencao(vault):
    cfg, _v, inventada, _leg, _toco = vault
    repair.aplicar(cfg)

    depois = inventada.read_text(encoding="utf-8")
    assert "Sarah Chen" not in depois
    assert "Seguir com a fase 2" not in depois
    assert "[no extractable text]" in depois


def test_a_nota_continua_existindo_e_com_o_mesmo_nome(vault):
    """O ponto de substituir em vez de apagar: o material continua achavel."""
    cfg, _v, inventada, _leg, _toco = vault
    repair.aplicar(cfg)
    assert inventada.exists()


def test_o_frontmatter_original_e_preservado(vault):
    cfg, _v, inventada, _leg, _toco = vault
    repair.aplicar(cfg)
    depois = inventada.read_text(encoding="utf-8")
    assert "title: GFK 30 Dias" in depois


def test_a_nota_consertada_diz_que_foi_consertada(vault):
    cfg, _v, inventada, _leg, _toco = vault
    repair.aplicar(cfg)
    assert repair.MARCA in inventada.read_text(encoding="utf-8")


def test_a_nota_legitima_nao_e_tocada(vault):
    cfg, _v, _inv, legitima, _toco = vault
    antes = legitima.read_text(encoding="utf-8")
    repair.aplicar(cfg)
    assert legitima.read_text(encoding="utf-8") == antes


def test_rodar_duas_vezes_nao_reprocessa(vault):
    """A marca existe para isto: a segunda passada nao pode empilhar avisos
    nem tratar como invencao um toco que ela mesma escreveu."""
    cfg, _v, inventada, _leg, _toco = vault
    repair.aplicar(cfg)
    primeira = inventada.read_text(encoding="utf-8")

    r2 = repair.aplicar(cfg)
    assert r2["notes_total"] == 0
    assert inventada.read_text(encoding="utf-8") == primeira


# ── aplicar: arquivar ───────────────────────────────────────────────────────

def test_arquivar_tira_da_busca_sem_destruir(vault):
    cfg, v, inventada, _leg, _toco = vault
    r = repair.aplicar(cfg, arquivar=True)

    assert not inventada.exists()
    arquivada = v / repair.PASTA_DE_ARQUIVO / inventada.name
    assert arquivada.exists()
    assert "Sarah Chen" in arquivada.read_text(encoding="utf-8"), (
        "arquivar preserva o original; quem quiser apagar decide depois"
    )
    assert r["applied"][0]["action"] == "archived"


def test_arquivar_nao_sobrescreve_homonimo(vault):
    cfg, v, _inv, _leg, _toco = vault
    destino = v / repair.PASTA_DE_ARQUIVO
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "2026-04-25-GFK-30-Dias.md").write_text("outra coisa", encoding="utf-8")

    repair.aplicar(cfg, arquivar=True)
    assert (destino / "2026-04-25-GFK-30-Dias.md").read_text(encoding="utf-8") == "outra coisa"
    assert (destino / "2026-04-25-GFK-30-Dias-1.md").exists()


# ── o que ele admite nao alcancar ───────────────────────────────────────────

def test_origem_sem_nota_e_relatada_em_separado(vault, monkeypatch):
    """Uma origem sem texto que nao gerou nota nenhuma nao e problema, mas
    quem le o relatorio precisa saber que ela existe."""
    cfg, v, inventada, _leg, _toco = vault
    inventada.unlink()
    r = repair.encontrar(cfg)

    assert r["found"] == []
    assert "GFK 30 Dias.pptx" in r["sources_without_note"]


def test_origem_ilegivel_nao_derruba_a_passagem(vault, monkeypatch):
    cfg, _v, _inv, _leg, _toco = vault

    def _explode(f):
        raise RuntimeError("arquivo corrompido")
    monkeypatch.setattr(repair, "extract", _explode)

    r = repair.encontrar(cfg)
    assert r["unreadable"]
    assert r["found"] == []


def test_nada_e_apagado_em_nenhum_caminho():
    """Guarda estrutural: este modulo conserta e move, nunca destroi."""
    import inspect
    fonte = inspect.getsource(repair)
    assert ".unlink(" not in fonte
    assert "rmtree" not in fonte

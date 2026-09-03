"""Extracao vazia virava nota sintetizada, com pessoas e decisoes inventadas.

Relato de campo de 03/09/2026. Os decks de um cliente sao graficos renderizados
como imagem: nao existe uma linha de texto dentro do arquivo. O extrator
devolveu vazio e o pipeline sintetizou a nota assim mesmo, com resumo, topicos,
pessoas e decisoes. O modelo nao errou ao resumir; recebeu nada e preencheu o
formulario. Vinte e duas notas, todas invencao integral, e com
`quality_score: 1.0`.

Dois defeitos, e o segundo e o que faz o primeiro doer:

**Um.** So o PDF escaneado tinha piso. Um deck cujos slides sao imagens, um
`.docx` sem paragrafo e uma planilha sem celula preenchida voltavam string
vazia, e o que sobrava era um erro seco em vez de um registro de que o arquivo
existe.

**Dois, o grave.** O toco do PDF e texto VALIDO. Nao-vazio, com mais de 30
caracteres, ele passa pela guarda `if not raw_text.strip()`, por `classify()` e
por `synthesize()` sem que nada o barre. O avaliador de sintese so reprova saida
curta demais e nomes alucinados conhecidos. Entao dar um toco ao `.pptx` sem
mexer no consumidor teria REINTRODUZIDO a nota inventada por outra porta, agora
para todo formato.

Por isso o piso e o guarda entram no mesmo commit, e ambos tem teste.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from delegation_core import extractor
from delegation_core.extractor import (
    NO_TEXT_MARKER, is_no_text_stub, no_text_stub,
)


# ── o toco ──────────────────────────────────────────────────────────────────

def test_o_toco_comeca_pela_marca():
    """A checagem le a primeira linha, entao a marca tem que estar la.

    Ficar no inicio tambem evita achar a frase por acidente no meio de um
    documento que por acaso fale sobre extracao de texto.
    """
    t = no_text_stub(Path("deck.pptx"), formato="PowerPoint", unidade="Slides", total=30)
    assert t.lstrip().startswith(NO_TEXT_MARKER)


def test_o_toco_diz_qual_arquivo_e_quanto_tinha():
    t = no_text_stub(Path("GFK 30 Dias.pptx"), formato="PowerPoint (slides are images)",
                     unidade="Slides", total=30)
    assert "GFK 30 Dias.pptx" in t
    assert "Slides: 30" in t
    assert "PowerPoint" in t


def test_o_toco_diz_que_nao_vai_virar_nota_sintetizada():
    """Quem abrir a nota precisa entender por que ela nao tem resumo."""
    t = no_text_stub(Path("x.pptx"), formato="PowerPoint", unidade="Slides", total=3)
    assert "no note will be synthesised" in t


@pytest.mark.parametrize("texto,esperado", [
    (no_text_stub(Path("a.pptx"), formato="PowerPoint", unidade="Slides", total=1), True),
    ("  " + NO_TEXT_MARKER + "\nFile: b.pdf", True),
    ("", False),
    (None, False),
    ("Uma nota normal sobre o projeto.", False),
])
def test_reconhece_o_toco(texto, esperado):
    assert is_no_text_stub(texto) is esperado


def test_documento_que_FALA_de_extracao_vazia_nao_e_toco():
    """A marca no meio do texto nao pode transformar uma nota real em toco.

    Esta nota, por exemplo, cita a marca ao explicar o defeito.
    """
    doc = (
        "## Analise\n\nO extrator devolveu o marcador "
        f"{NO_TEXT_MARKER} e o pipeline sintetizou assim mesmo.\n"
    )
    assert is_no_text_stub(doc) is False


# ── o piso, por formato ─────────────────────────────────────────────────────

class _SlideFalso:
    def __init__(self, textos):
        self.shapes = [type("F", (), {"text": t})() for t in textos]


class _PrsFalsa:
    def __init__(self, slides):
        self.slides = [_SlideFalso(s) for s in slides]


def test_deck_de_imagens_vira_toco(monkeypatch):
    """O caso do relato: 30 slides, nenhuma linha de texto."""
    import sys, types
    modulo = types.ModuleType("pptx")
    modulo.Presentation = lambda _p: _PrsFalsa([[] for _ in range(30)])
    monkeypatch.setitem(sys.modules, "pptx", modulo)

    r = extractor._pptx(Path("GFK 30 Dias.pptx"))
    assert is_no_text_stub(r)
    assert "Slides: 30" in r


def test_deck_com_texto_de_verdade_nao_vira_toco(monkeypatch):
    import sys, types
    modulo = types.ModuleType("pptx")
    modulo.Presentation = lambda _p: _PrsFalsa([["Resultados do Q3"], ["Alta de 12%"]])
    monkeypatch.setitem(sys.modules, "pptx", modulo)

    r = extractor._pptx(Path("q3.pptx"))
    assert not is_no_text_stub(r)
    assert "Resultados do Q3" in r


def test_pdf_escaneado_continua_com_piso(monkeypatch):
    """O unico formato que ja tinha piso nao pode te-lo perdido na unificacao."""
    import sys, types
    class _Pagina:
        def extract_text(self): return ""
    class _Reader:
        def __init__(self, _p): self.pages = [_Pagina() for _ in range(12)]
    modulo = types.ModuleType("pypdf")
    modulo.PdfReader = _Reader
    monkeypatch.setitem(sys.modules, "pypdf", modulo)

    r = extractor._pdf(Path("digitalizado.pdf"))
    assert is_no_text_stub(r)
    assert "Pages: 12" in r


def test_todo_formato_que_pode_voltar_vazio_tem_piso():
    """A guarda estrutural. Um formato novo que esqueca o piso devolve string
    vazia, e string vazia foi o comeco desta historia."""
    import inspect
    for nome in ("_pdf", "_pptx", "_docx", "_xlsx"):
        fonte = inspect.getsource(getattr(extractor, nome))
        assert "no_text_stub(" in fonte, (
            f"{nome} pode devolver vazio sem produzir toco"
        )


# ── o guarda, que e a metade que importa ────────────────────────────────────

class _VaultFalso:
    """O minimo que `organizer.run` usa, e um registro do que foi escrito."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.escritas = []

    def write_note(self, folder, title, content, **kw):
        caminho = self.cfg.vault / folder / f"{title}.md"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(content, encoding="utf-8")
        self.escritas.append({"folder": folder, "title": title, "content": content})
        return caminho


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Um inbox com um arquivo cuja extracao devolve toco, e nada real por perto."""
    from delegation_core import organizer
    from delegation_core.config import Config

    vault = tmp_path / "vault"
    (vault / "_inbox").mkdir(parents=True)
    deck = vault / "_inbox" / "GFK 30 Dias.pptx"
    deck.write_bytes(b"nao importa: extract esta trocado")

    cfg = Config(vault_path=str(vault), server_token="t", server_port=8787)
    vm = _VaultFalso(cfg)

    toco = no_text_stub(deck, formato="PowerPoint (slides are images)",
                        unidade="Slides", total=30)
    monkeypatch.setattr(organizer, "extract", lambda _f: toco)

    chamou = {"classify": 0, "synthesize": 0}

    async def _classify(*a, **k):
        chamou["classify"] += 1
        return "Decisions"

    async def _synthesize(*a, **k):
        chamou["synthesize"] += 1
        return ("## Summary\nO conselho aprovou o plano de 30 dias.\n"
                "## Pessoas\n- Sarah Chen\n## Decisoes\n- Seguir com a fase 2")

    monkeypatch.setattr(organizer, "classify", _classify)
    monkeypatch.setattr(organizer, "synthesize", _synthesize)
    monkeypatch.setattr(organizer, "is_junk", lambda *a, **k: None)
    return organizer, vm, chamou, toco, vault


def _rodar(organizer, vm):
    import asyncio
    return asyncio.run(organizer.run(engine=object(), vault_manager=vm))


def test_o_toco_NAO_passa_pelo_modelo(cenario):
    """O centro do defeito. Uma nota inteira com pessoas e decisoes saia daqui."""
    organizer, vm, chamou, _toco, _vault = cenario
    _rodar(organizer, vm)

    assert chamou["synthesize"] == 0, "sintetizou a partir de um toco"
    assert chamou["classify"] == 0, "classificou um toco, escolhendo pasta a esmo"


def test_a_nota_escrita_e_o_toco_literal(cenario):
    organizer, vm, _chamou, toco, _vault = cenario
    _rodar(organizer, vm)

    assert len(vm.escritas) == 1
    assert vm.escritas[0]["content"] == toco
    assert "Sarah Chen" not in vm.escritas[0]["content"]


def test_o_arquivo_continua_achavel_pelo_nome(cenario):
    """O toco existe justamente para o arquivo nao sumir. Se fosse so um erro,
    ninguem descobriria que o deck passou por aqui."""
    organizer, vm, _chamou, _toco, _vault = cenario
    _rodar(organizer, vm)
    assert "GFK 30 Dias" in vm.escritas[0]["title"]


def test_o_resultado_relata_o_toco_em_separado(cenario):
    """Nem erro nem classificacao normal: quem le o resultado precisa saber que
    o arquivo entrou sem texto."""
    organizer, vm, _chamou, _toco, _vault = cenario
    r = _rodar(organizer, vm)

    assert r["stubs"], "o toco nao foi relatado"
    assert "no text layer" in r["stubs"][0]
    assert not r["errors"]


def test_arquivo_com_texto_de_verdade_continua_sintetizando(cenario, monkeypatch):
    """O contraponto: o guarda nao pode ter desligado o caminho normal."""
    organizer, vm, chamou, _toco, _vault = cenario
    monkeypatch.setattr(organizer, "extract",
                        lambda _f: "## Ata\n\nO conselho aprovou o plano." * 20)
    _rodar(organizer, vm)

    assert chamou["synthesize"] == 1
    assert chamou["classify"] == 1

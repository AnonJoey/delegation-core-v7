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
    """Densidade de deck real: os 7 decks legitimos medidos em 03/09 tem entre
    406 e 755 caracteres por slide. Este fixture usa 420, perto da borda de
    baixo, para provar que o piso nao morde o documento mais magro que existe
    de verdade."""
    import sys, types
    corpo = "Resultados do Q3. " * 23          # ~414 caracteres por slide
    modulo = types.ModuleType("pptx")
    modulo.Presentation = lambda _p: _PrsFalsa([[corpo], [corpo]])
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
    """Dublê do `VaultManager`, limitado a metodos que a classe real TEM.

    A primeira versao deste dublê expunha `write_note`, um metodo que eu supus
    e que nao existe. O organizer o chamava, todos os testes passavam, e o
    defeito so apareceu quando um deck de verdade atravessou o daemon no ar:
    `'VaultManager' object has no attribute 'write_note'`.

    Por isso o dublê agora e conferido contra a superficie real no
    `__init_subclass__` abaixo, e a nota e lida do disco em vez de um registro
    que o proprio dublê mantem: o teste passa a olhar o efeito, nao a intencao.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.indexadas = []

    def index_note(self, content, meta, **kw):
        self.indexadas.append({"content": content, "meta": meta})

    def search(self, *a, **k):
        return []


def test_o_duble_so_expoe_metodos_que_o_VaultManager_real_tem():
    """A guarda que teria evitado tudo isto.

    Um dublê livre para inventar metodo transforma o teste num espelho da
    suposicao de quem o escreveu.
    """
    from delegation_core.vault import VaultManager
    for nome in dir(_VaultFalso):
        if nome.startswith("_"):
            continue
        assert hasattr(VaultManager, nome), (
            f"o duble expoe `{nome}`, que o VaultManager real nao tem"
        )


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


def _notas_escritas(vault):
    """As notas do usuario, sem as pastas internas e sem o resumo semanal.

    `run()` sempre escreve `YYYY-Wnn-maintenance.md` no fim; contá-lo aqui faria
    o teste falhar por um motivo que nao tem nada a ver com o toco.
    """
    return [p for p in vault.rglob("*.md")
            if not p.relative_to(vault).parts[0].startswith("_")
            and "-maintenance" not in p.stem]


def test_a_nota_escrita_e_o_toco_literal(cenario):
    organizer, vm, _chamou, toco, vault = cenario
    _rodar(organizer, vm)

    notas = _notas_escritas(vault)
    assert len(notas) == 1
    corpo = notas[0].read_text(encoding="utf-8")
    assert toco in corpo
    assert "Sarah Chen" not in corpo


def test_o_arquivo_continua_achavel_pelo_nome(cenario):
    """O toco existe justamente para o arquivo nao sumir. Se fosse so um erro,
    ninguem descobriria que o deck passou por aqui."""
    organizer, vm, _chamou, _toco, vault = cenario
    _rodar(organizer, vm)
    assert "GFK 30 Dias" in _notas_escritas(vault)[0].stem


def test_o_toco_e_indexado_como_qualquer_nota(cenario):
    """Sem indexar, a nota existe no disco e some da busca, que e metade do
    motivo de ela existir."""
    organizer, vm, _chamou, _toco, _vault = cenario
    _rodar(organizer, vm)
    assert len(vm.indexadas) == 1
    assert vm.indexadas[0]["meta"]["title"] == "GFK 30 Dias"


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


# ── o piso de substancia, medido em documento real ──────────────────────────

from delegation_core.extractor import (  # noqa: E402
    MIN_CHARS_POR_PAGINA, MIN_CHARS_TOTAL, sem_substancia,
)


def test_os_dois_pisos_ficam_no_vao_medido():
    """Medido em 03/09 sobre 59 documentos reais: os degenerados param em 56
    caracteres no total e 14,0 por pagina, e o menor documento de verdade tem
    4.296 no total e 195,1 por pagina. Um piso fora desse vao esta ajustado a
    uma das bordas."""
    assert 56 < MIN_CHARS_TOTAL < 4296
    assert 14.0 < MIN_CHARS_POR_PAGINA < 195.1


@pytest.mark.parametrize("texto,paginas,esperado", [
    ("", None, True),
    ("x" * 56, 4, True),                       # G.I.A.pptx, medido
    ("x" * 199, None, True),
    ("x" * 200, None, False),
    ("x" * 4296, 62, False),                   # o menor real medido
])
def test_o_piso_total(texto, paginas, esperado):
    assert sem_substancia(texto, paginas=paginas) is esperado


def test_o_piso_por_pagina_pega_o_que_o_total_deixa_passar():
    """30 slides com numero de slide: 240 caracteres cruzam o piso total e nao
    sao documento nenhum."""
    assert sem_substancia("x" * 240, paginas=30) is True
    assert sem_substancia("x" * 240, paginas=None) is False


def test_documento_curto_e_denso_passa():
    """Uma pagina com 300 caracteres e um memorando, nao residuo."""
    assert sem_substancia("x" * 300, paginas=1) is False


def test_paragrafo_nao_serve_de_unidade():
    """O menor .docx real medido tem 39,2 caracteres por paragrafo, abaixo do
    proprio piso. Passar paragrafo como pagina reprovaria documento legitimo."""
    from delegation_core import extractor
    import inspect
    fonte = inspect.getsource(extractor._docx)
    assert "paginas=" not in fonte, "_docx passou paragrafo como unidade de pagina"

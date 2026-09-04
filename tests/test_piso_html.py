"""O piso de substancia cobria quatro formatos e o HTML nao era um deles.

`sem_substancia` e `no_text_stub` nasceram em 03/09/2026 para o caso das 22
notas de invencao integral: arquivos que nao estavam vazios o bastante para a
guarda de vazio pegar, e vazios demais para qualquer sintese ser verdadeira.

Conferido por AST quem aplica o piso:

    _pdf  _docx  _xlsx  _pptx   aplicam
    _html _json  _csv   _text   nao aplicam

E o pipeline nao fecha o buraco: `organizer.py:335` chama `is_no_text_stub`, que
so reconhece o TOCO que os quatro binarios produzem. Quem nao produz toco passa
como documento.

MEDIDO em 04/09/2026 com dois HTML reais de um caso comum:

    casca JS       ('<div id="root">' + noscript)   ->  17 chars extraidos
    export so de imagens (<img> x3)                 ->   0 chars extraidos

Nos DOIS, `sem_substancia` diz True e `is_no_text_stub` diz False, ou seja o
pipeline os trata como documento e manda sintetizar. Um arquivo do qual se
extraiu ZERO caracteres virando nota e a forma mais pura do defeito que o piso
existe para impedir.

E `.html` nao e um formato hipotetico aqui: os dois artefatos que entraram no
inbox desta maquina em 03/09 eram `.html`.

## POR QUE SO O HTML, e nao os outros tres

Nao e o mesmo caso. Em `.txt`, `.json` e `.csv` o arquivo E o texto: um curto e
curto de proposito, e transforma-lo em toco destruiria a intencao de quem o
escreveu. No HTML a extracao DESCARTA a maior parte do arquivo -- as tags -- e
por isso ela pode esvaziar um arquivo grande, que e exatamente a propriedade
que os quatro binarios tem.

HIPOTESE QUE EU TESTEI E QUE ESTAVA ERRADA, registrada para nao ser refeita: eu
suspeitei que as duas notas com prompt vazado no vault (que vieram de `.html`)
tivessem sido causadas por extracao fina. Nao foram. Medido: 17.515 e 8.094
caracteres de conteudo real, muito acima do piso. Aquele vazamento foi inteiro
do fallback de modo agente, e nada tem a ver com este achado.
"""
from __future__ import annotations

import pytest

from delegation_core import extractor


def _html(tmp_path, corpo: str):
    f = tmp_path / "pagina.html"
    f.write_text(corpo, encoding="utf-8")
    return f


CASCA_JS = ('<!doctype html><html><head><title>Relatorio</title>'
            '<script src="/app.js"></script></head>'
            '<body><div id="root"></div><noscript>Enable JavaScript</noscript>'
            '<script>window.__DATA__={"a":1};</script></body></html>')

SO_IMAGENS = ('<!doctype html><html><head><title>Deck</title></head>'
              '<body><img src="s1.png"><img src="s2.png"></body></html>')


def test_uma_casca_de_javascript_vira_toco_e_nao_documento(tmp_path):
    texto = extractor.extract(_html(tmp_path, CASCA_JS))
    assert extractor.is_no_text_stub(texto), (
        "17 caracteres de 'Enable JavaScript' viravam uma nota sintetizada"
    )


def test_um_html_so_de_imagens_vira_toco(tmp_path):
    texto = extractor.extract(_html(tmp_path, SO_IMAGENS))
    assert extractor.is_no_text_stub(texto)


def test_o_toco_diz_o_formato_e_quanto_saiu(tmp_path):
    texto = extractor.extract(_html(tmp_path, SO_IMAGENS))
    assert "HTML" in texto
    assert "Characters extracted: 0" in texto


def test_uma_pagina_de_verdade_continua_passando(tmp_path):
    corpo = ("<html><body><h1>Relatorio trimestral</h1>"
             + "<p>" + ("Texto de verdade sobre o trimestre. " * 20) + "</p>"
             "</body></html>")
    texto = extractor.extract(_html(tmp_path, corpo))
    assert not extractor.is_no_text_stub(texto)
    assert "Relatorio trimestral" in texto


def test_o_piso_do_html_e_o_mesmo_dos_outros(tmp_path):
    """199 caracteres nao passam, 201 passam: o corte e o MIN_CHARS_TOTAL medido,
    e nao um numero novo inventado so para o HTML."""
    quase = "<html><body><p>" + ("a" * (extractor.MIN_CHARS_TOTAL - 1)) + "</p></body></html>"
    passa = "<html><body><p>" + ("a" * (extractor.MIN_CHARS_TOTAL + 1)) + "</p></body></html>"

    assert extractor.is_no_text_stub(extractor.extract(_html(tmp_path, quase)))
    assert not extractor.is_no_text_stub(extractor.extract(_html(tmp_path, passa)))


# ── a linha entre quem tem piso e quem nao tem, prendida ────────────────────


@pytest.mark.parametrize("sufixo,corpo", [
    (".txt", "uma nota curta que a pessoa escreveu de proposito"),
    (".json", '{"a": 1}'),
    (".csv", "col\nvalor"),
])
def test_formatos_de_texto_puro_nao_ganham_piso(tmp_path, sufixo, corpo):
    """Num `.txt`, `.json` ou `.csv` o arquivo E o texto: um curto e curto de
    proposito, e vira-lo toco destruiria a intencao de quem o escreveu. A
    extracao de HTML e diferente porque DESCARTA a maior parte do arquivo."""
    f = tmp_path / f"arquivo{sufixo}"
    f.write_text(corpo, encoding="utf-8")

    texto = extractor.extract(f)

    assert not extractor.is_no_text_stub(texto), (
        f"{sufixo} nao pode virar toco: o conteudo e o proprio arquivo"
    )

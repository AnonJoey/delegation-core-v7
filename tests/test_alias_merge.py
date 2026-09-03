"""O alias que salva o titulo truncado nao pode ser descartado pelo autor.

`safe_filename` corta o nome do arquivo em 50 caracteres, entao uma nota
titulada "Descompassos entre o card dos Agentes PMO e o que foi decidido"
aterrissa no disco como "2026-09-02-Descompassos entre o card dos Agentes PMO e
o que.md". `compose_note` ja gerava um `aliases:` com o titulo inteiro para que
um link escrito com o titulo real resolvesse.

So que a fusao de frontmatter adiciona chave gerada apenas onde o autor NAO
forneceu a sua (`if k not in have`). Para `title:` isso e correto e documentado:
e assim que uma nota carrega um nome curto de exibicao independente do arquivo.
Para `aliases:` estava errado, porque o alias gerado nao e uma opiniao sobre
nomes, e a ponte de volta para um titulo que o nome do arquivo nao coube.

Medido neste vault em 02/09/2026. Duas notas forneceram dois aliases
descritivos proprios, nao receberam o alias de truncamento, e sao linkadas pelo
titulo completo a partir de outras notas. Esses links resolvem para nada, e
aparecem nos 25 `broken_links` do vault_health.
"""
from __future__ import annotations

from delegation_core.vault import compose_note, safe_filename

TITULO_LONGO = "Descompassos entre o card dos Agentes PMO e o que foi decidido"


def _frontmatter(texto: str) -> str:
    assert texto.startswith("---\n")
    return texto[4:texto.index("\n---\n", 4)]


def _e_alias(fm: str, valor: str) -> bool:
    """`valor` aparece como ITEM de aliases, nao em qualquer linha.

    Sem esta distincao os testes passam pelo motivo errado: `title: "<titulo>"`
    tambem esta no frontmatter, entao um `titulo in fm` continua verdadeiro
    mesmo quando o alias foi descartado. Uma mutacao provou exatamente isso.
    """
    from delegation_core.linker import frontmatter_aliases

    nota = f"---\n{fm}\n---\n\ncorpo\n"
    return valor.lower() in {a.lower() for a in frontmatter_aliases(nota)}


def test_titulo_longo_e_mesmo_truncado_pelo_nome_do_arquivo():
    """A premissa de tudo abaixo, medida e nao suposta."""
    assert len(safe_filename(TITULO_LONGO)) < len(TITULO_LONGO)


def test_sem_aliases_do_autor_o_gerado_entra():
    """Comportamento que ja existia, preso aqui para nao regredir."""
    fm = _frontmatter(compose_note(TITULO_LONGO, "## Corpo\n", "2026-09-02"))
    assert "aliases:" in fm
    assert _e_alias(fm, TITULO_LONGO)


def test_titulo_curto_nao_ganha_alias_redundante():
    fm = _frontmatter(compose_note("Curto", "## Corpo\n", "2026-09-02"))
    assert "aliases:" not in fm


def test_alias_do_autor_em_bloco_nao_engole_o_gerado():
    """O defeito. O autor forneceu aliases e o alias de truncamento sumiu."""
    conteudo = (
        "---\n"
        "aliases:\n"
        '  - "Card dos Agentes PMO em 02/09"\n'
        '  - "Sete descompassos do card"\n'
        "---\n\n"
        "## Corpo\n"
    )
    fm = _frontmatter(compose_note(TITULO_LONGO, conteudo, "2026-09-02"))

    assert _e_alias(fm, "Card dos Agentes PMO em 02/09"), "apagou o alias do autor"
    assert _e_alias(fm, "Sete descompassos do card"), "apagou o alias do autor"
    assert _e_alias(fm, TITULO_LONGO), "o alias de truncamento foi descartado"


def test_alias_do_autor_inline_nao_engole_o_gerado():
    conteudo = '---\naliases: ["Um", "Dois"]\n---\n\n## Corpo\n'
    fm = _frontmatter(compose_note(TITULO_LONGO, conteudo, "2026-09-02"))

    assert _e_alias(fm, "Um") and _e_alias(fm, "Dois")
    assert _e_alias(fm, TITULO_LONGO)


def test_lista_inline_vazia():
    conteudo = "---\naliases: []\n---\n\n## Corpo\n"
    fm = _frontmatter(compose_note(TITULO_LONGO, conteudo, "2026-09-02"))
    assert _e_alias(fm, TITULO_LONGO)


def test_nao_duplica_alias_que_o_autor_ja_escreveu():
    """Contado sobre o CONJUNTO de aliases, nao por substring.

    Contar ocorrencias do titulo no texto e fragil: `title:` carrega uma, e o
    alias com prefixo de data carrega o titulo dentro dele. A propriedade que
    importa e que o titulo nu apareca uma unica vez como alias.
    """
    from delegation_core.linker import frontmatter_aliases

    conteudo = f'---\naliases:\n  - "{TITULO_LONGO}"\n---\n\n## Corpo\n'
    nota = compose_note(TITULO_LONGO, conteudo, "2026-09-02")

    todos = [a.lower() for a in frontmatter_aliases(nota)]
    assert todos.count(TITULO_LONGO.lower()) == 1, f"alias duplicado: {todos}"
    assert f"2026-09-02-{TITULO_LONGO}".lower() in todos


def test_titulo_do_autor_continua_vencendo():
    """A regra que NAO muda: title do autor vence o gerado."""
    conteudo = '---\ntitle: "Nome curto"\naliases:\n  - "Outro"\n---\n\n## Corpo\n'
    fm = _frontmatter(compose_note(TITULO_LONGO, conteudo, "2026-09-02"))
    assert 'title: "Nome curto"' in fm
    assert f'title: "{TITULO_LONGO}"' not in fm


def test_a_fusao_nao_quebra_o_bloco_do_autor():
    """Chaves que este modulo nao modela tem que sobreviver intactas."""
    conteudo = (
        "---\n"
        'subtitle: "a versao longa, para humanos"\n'
        "tags: [soteria, pmo]\n"
        "aliases:\n"
        '  - "Um"\n'
        "custom_key:\n"
        "  nested: valor\n"
        "---\n\n"
        "## Corpo\n"
    )
    fm = _frontmatter(compose_note(TITULO_LONGO, conteudo, "2026-09-02"))

    assert 'subtitle: "a versao longa, para humanos"' in fm
    assert "tags: [soteria, pmo]" in fm
    assert "custom_key:" in fm
    assert "  nested: valor" in fm
    assert _e_alias(fm, TITULO_LONGO)
    # O alias entra no bloco de aliases, nao dentro de custom_key.
    linhas = fm.split("\n")
    i_alias = next(i for i, l in enumerate(linhas) if l.startswith("aliases:"))
    i_custom = next(i for i, l in enumerate(linhas) if l.startswith("custom_key:"))
    i_novo = next(i for i, l in enumerate(linhas) if TITULO_LONGO in l and l.strip().startswith("- "))
    assert i_alias < i_novo < i_custom


def test_o_alias_gerado_torna_a_nota_resolvivel_pelo_titulo():
    """A propriedade que importa: a resolucao de link tem que encontrar.

    Sem isto os testes acima provam so que um texto foi inserido num arquivo, e
    nao que o link escrito com o titulo completo passa a resolver.
    """
    from delegation_core.linker import frontmatter_aliases

    conteudo = '---\naliases:\n  - "Card dos Agentes PMO em 02/09"\n---\n\n## Corpo\n'
    nota = compose_note(TITULO_LONGO, conteudo, "2026-09-02")

    resolviveis = {a.lower() for a in frontmatter_aliases(nota)}
    assert TITULO_LONGO.lower() in resolviveis, (
        "o link escrito com o titulo completo continua sem resolver"
    )
    assert "card dos agentes pmo em 02/09" in resolviveis


DATA = "2026-09-02"


def _aliases(nota: str) -> set[str]:
    from delegation_core.linker import frontmatter_aliases
    return {a.lower() for a in frontmatter_aliases(nota)}


def test_gera_a_forma_com_data_que_os_links_reais_usam():
    """A comparacao de link e literal e NAO tira a data do alvo.

    get_health_summary faz `key = link.lower()` e procura em `resolvable`.
    link_names_for_stem tira a data do STEM, nunca do alvo. Entao um alias so
    com o titulo nu nao resolve `[[2026-09-02-Titulo completo]]`.

    Medido neste vault: dos seis alvos quebrados que apontam para notas reais,
    os SEIS usam a forma com data. A primeira versao desta correcao gerava so o
    titulo nu e nao teria consertado nenhum deles.
    """
    nota = compose_note(TITULO_LONGO, "## Corpo\n", DATA)
    assert f"{DATA}-{TITULO_LONGO}".lower() in _aliases(nota)
    assert TITULO_LONGO.lower() in _aliases(nota)


def test_as_duas_formas_sobrevivem_a_aliases_do_autor():
    conteudo = '---\naliases:\n  - "Card dos Agentes PMO em 02/09"\n---\n\n## Corpo\n'
    nota = compose_note(TITULO_LONGO, conteudo, DATA)
    als = _aliases(nota)

    assert f"{DATA}-{TITULO_LONGO}".lower() in als
    assert TITULO_LONGO.lower() in als
    assert "card dos agentes pmo em 02/09" in als


def test_um_link_com_data_resolve_de_ponta_a_ponta(tmp_path):
    """O caminho real: escreve a nota, e o alvo que estava quebrado resolve.

    Usa a mesma funcao de resolucao que get_health_summary usa, em vez de
    reimplementar a regra aqui.
    """
    from delegation_core.linker import frontmatter_aliases
    from delegation_core.vault import link_names_for_stem, safe_filename

    nota = compose_note(TITULO_LONGO, "## Corpo\n", DATA)
    stem = f"{DATA}-{safe_filename(TITULO_LONGO)}"

    resolvable = link_names_for_stem(stem) | {a.lower() for a in frontmatter_aliases(nota)}

    alvo_real = f"{DATA}-{TITULO_LONGO}"
    assert alvo_real.lower() in resolvable, (
        f"o link [[{alvo_real}]] continua quebrado; o arquivo e {stem}.md"
    )

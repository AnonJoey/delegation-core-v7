# Mapa do delegation-core

_Medido em 2026-09-03 contra a árvore de trabalho da branch
`otimizacao/overnight-0903`. Todo número aqui foi contado por script na hora,
nenhum foi copiado de documento anterior. Se você está lendo isto muito depois,
rode os comandos da última seção em vez de acreditar nos números._

## O que é

Um servidor MCP local sobre um vault de markdown, com índice vetorial
(ChromaDB + BGE-m3), um modelo local opcional (llama.cpp), um pipeline de grafo
de código vendorizado do Graphify, um CLI completo, e um dashboard Tauri.

Serve **54 ferramentas MCP**. Não confie nesse número: chame `capabilities()`,
que pergunta ao servidor em execução. `tests/test_docs_not_stale.py` falha se
esta linha divergir de `server.py`.

## Tamanho, por área

| Área | Arquivos | Linhas | De quem é |
|---|---:|---:|---|
| `src/delegation_core/` sem `graph/` | 54 | 18.981 | **nosso** |
| `src/delegation_core/graph/` | 56 | 32.965 | vendorizado do Graphify |
| `tests/` | 75 | 13.710 | **nosso** |
| `hooks/` | 2 | 579 | **nosso** |
| `skills/` | 81 | 18.156 | vendorizado da Anthropic |
| `docs/` | 5 | 670 | ferramentas de bancada |

O código que é de fato deste projeto são **~33.000 linhas** entre núcleo, testes
e hooks. As outras ~51.000 são vendorizadas e mudá-las custa o re-vendor, o que
é uma decisão registrada no `HANDOFF.md`, não uma omissão.

## O núcleo, por responsabilidade

**O índice e o vault**
- `vault.py` (1.664) — ciclo de vida do ChromaDB, busca, indexação, reindex,
  saúde do vault. É o módulo mais denso e o segundo mais importado.
- `notes.py` (408) — nomes de arquivo, caminhos, frontmatter. Funções puras,
  sem ChromaDB. Separado de `vault.py` em 03/09 porque nove módulos importavam
  essas regras e arrastavam junto o módulo que abre o índice.
- `embeddings.py` (484) — BGE, perfis de modelo, chunking, fallback para CPU.
- `linker.py` (331) — wikilinks, aliases, backlinks.
- `ingest.py` (395) — indexar pasta externa sem mover arquivo.
- `extractor.py` (292) — PDF, docx, xlsx, pptx, html, csv para texto.

**O modelo local**
- `engine.py` (~540) — o único dono da conexão com o llama.cpp. Fila,
  orçamento por tarefa, arbitragem de GPU, retry. Desde 03/09 há teste que
  falha se qualquer módulo fora daqui abrir `/v1/chat/completions`.
- `gpu.py` (~210) — árbitro de exclusão mútua entre BGE e llama. Nesta máquina
  os dois não cabem nos 16 GB da placa.
- `localqueue.py` (~250) / `localworker.py` (163) — fila em disco de tarefas
  para o modelo local, drenada por um worker só.

**O servidor e as superfícies**
- `server.py` (1.497) — as 54 ferramentas MCP. Depende de 10 outros módulos.
- `daemon.py` (296) — o cliente HTTP que o CLI usa para falar com o daemon.
- `dashboard_api.py` (1.025) — API do dashboard Tauri. Depende de 9 módulos.
- `cli.py` (1.437) — o CLI.

**A manutenção do vault**
- `organizer.py` (553) — classifica o inbox, funde duplicatas, religa.
  Depende de 9 módulos, todos da própria manutenção.
- `classifier.py`, `merger.py`, `splitter.py`, `junk.py`, `synthesizer.py`.

**Estado que atravessa sessões**
- `tracker.py` (177) — processos em `processes.json`.
- `jobs.py` (~140) — jobs de background em memória, com histórico de duração
  em disco.
- `config.py` (~460) — o `config.json`. É o módulo mais importado do projeto.

**Grafo de código**
- `graphbridge.py` (704) — a ponte entre o pipeline vendorizado e o vault.
- `graph/` — vendorizado. `extract.py` sozinho tem 5.372 linhas.

## Acoplamento medido

**Mais importados** (mexer aqui mexe em tudo):
`config` (12 importadores), `vault` (11), `linker` (7), `periodo` (7),
`embeddings` (6), `extractor` (4).

**Que mais sabem** (candidatos a quebra futura):
`server` (10 dependências), `dashboard_api` (9), `organizer` (9).

`server` e `dashboard_api` dependem quase do mesmo conjunto, o que é esperado:
são duas superfícies sobre o mesmo núcleo. `organizer` depende de nove módulos
que são todos da própria manutenção do vault, o que é coesão e não dispersão.

## Invariantes que o código defende com teste

- Nenhum teste escreve em `~/.delegation_core` (`tests/conftest.py`, autouse).
  Existe porque um teste sobrescreveu o `config.json` real e derrubou o daemon.
- `__version__` e `pyproject.toml` concordam, e não há terceira cópia.
- A prosa não carrega contagem de testes; a contagem de ferramentas MCP é
  verificada; as assinaturas que o `AGENT_GUIDE` declara batem com `server.py`.
- `notes.py` não pode voltar a importar `chromadb`, `embeddings`, `gpu` nem
  `vault`.
- Nenhum módulo fora do `engine` abre `/v1/chat/completions`.
- Toda nota arquivada por `graph_build` é carimbada em `.chroma_index.json`.

## Como reproduzir estes números

```bash
cd /home/joey/Projects/delegation-core

# Testes
~/.delegation_core/venv/bin/python -m pytest -q

# Ferramentas MCP
grep -c '@mcp.tool()' src/delegation_core/server.py

# Tamanho por área
python3 ~/.delegation_core/overnight-0903/mapa.py . --only src --json /tmp/m.json

# Acoplamento e código morto
python3 ~/.delegation_core/overnight-0903/morto.py

# Saúde do vault (não escreva script para isso)
# heartbeat(force=true) e vault_health_detail() pelo MCP
```

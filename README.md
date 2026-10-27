# Stolas — Book Translation Editorial

Skill e ferramentas Python para tradução editorial contextual de PDFs, com preservação de elementos gráficos e auditoria visual.

## Estado

**Em desenvolvimento — Sprint 1 iniciada.** O repositório contém especificação editorial, extração estruturada inicial, pré-auditoria visual e teste sintético. **Não** realiza tradução e reconstrução automática de livros completos.

## Instalação

```bash
python -m pip install -r requirements.txt
```

Para usar como skill no Codex, coloque o repositório em `~/.codex/skills/book-translation-editorial/` ou configure seu ambiente de skills conforme a versão do Codex.

## Extração estruturada

Execute na raiz do repositório:

```bash
python -m scripts.extract_pdf caminho/arquivo.pdf --out workspace/manifest.json
```

Gera JSON com SHA-256, páginas, blocos de texto, spans, caixas, fontes e limites de desenhos vetoriais. A ordem de leitura é heurística e não há OCR nem reconhecimento semântico de sigilos.

## Pré-auditoria visual

```bash
python scripts/pdf_audit.py inventory original.pdf --out workspace/inventory.json
python scripts/pdf_audit.py audit original.pdf traduzido.pdf --out qa/
```

A comparação de pixels não substitui revisão editorial ou visual humana.

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes foram adicionados, mas ainda precisam ser executados e validados em ambiente de CI.

## Planejamento

- [Roadmap](docs/ROADMAP.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Testes e homologação](docs/TESTING.md)

Não publique livros protegidos por direitos autorais nem remova DRM.

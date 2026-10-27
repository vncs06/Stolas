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

Gera JSON schema `0.2.0` com SHA-256 do PDF, versão do motor, páginas, blocos de texto, spans, caixas, fontes, ocorrências de imagens e propriedades recuperáveis de desenhos vetoriais. O PyMuPDF é fixado na versão homologada `2.0`, pois versões diferentes podem alterar segmentação, geometria e descoberta de imagens.

Imagens são descobertas pela combinação do conteúdo exibido e recursos da página; o dicionário textual com bytes é carregado somente como fallback para imagens inline ou associações ambíguas. Cada ocorrência registra página, caixa, dimensões, formato, hash e `xref` quando disponível; os bytes não são incorporados ao manifesto. Hashes são calculados uma vez por XRef, pixmaps temporários são liberados imediatamente e o cache mantém apenas metadados. Páginas são classificadas como `empty`, `text`, `image`, `mixed` ou `vector`. Qualquer página com imagem é marcada para revisão por possível texto rasterizado, mas nenhum OCR é executado.

IDs de texto, imagem e vetor derivam do documento, página, tipo, geometria e conteúdo ou propriedades estruturais. Isso reduz dependência da ordem interna do motor, sem garantir IDs idênticos se uma versão futura reagrupar blocos ou alterar coordenadas.

A ordem de leitura permanece heurística. O registro de imagens e comandos vetoriais permite recuperação e análise posterior, mas não garante reconstrução visual perfeita nem reconhecimento semântico de diagramas, sigilos ou hebraico rasterizado.

## Pré-auditoria visual

```bash
python scripts/pdf_audit.py inventory original.pdf --out workspace/inventory.json
python scripts/pdf_audit.py audit original.pdf traduzido.pdf --out qa/
```

A comparação de pixels não substitui revisão editorial ou visual humana.

## Testes automatizados

```bash
python -m unittest discover -s tests -v
```

Os testes geram PDFs sintéticos durante a execução e cobrem texto, hebraico nativo, imagens internas e parcialmente externas, reutilização de imagens, páginas mistas e vazias, propriedades vetoriais, estabilidade de IDs, serialização e ausência das rotas de associação gráfica que decodificam todas as imagens. Eles não constituem homologação completa de livros reais.

## Documentação

- [Roadmap](docs/ROADMAP.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Instruções permanentes para agentes](AGENTS.md)
- [Protocolo editorial da skill](SKILL.md)

Instruções específicas de homologação e testes pontuais são fornecidas fora do repositório.

Não publique livros protegidos por direitos autorais nem remova DRM.

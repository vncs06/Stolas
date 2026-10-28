# Stolas — Book Translation Editorial

Skill e ferramentas Python para tradução editorial contextual de PDFs, com preservação de elementos gráficos e auditoria visual.

## Estado

**Em desenvolvimento.** A extração estrutural da Sprint 1 está consolidada e o repositório inclui proteção contextual e uma primeira camada de estrutura editorial. **Não** realiza tradução, OCR automático ou reconstrução de livros.

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

## Proteção e classificação

Classifique um manifesto de extração `0.2.0` sem modificá-lo:

```bash
python -m scripts.classify_protected workspace/manifest.json --out workspace/protected-spans.json
```

Para aplicar decisões editoriais externas:

```bash
python -m scripts.classify_protected workspace/manifest.json \
  --overrides workspace/protection-overrides.json \
  --out workspace/protected-spans.json
```

A saída usa schema e ruleset `0.2.0` e contém decisões por fragmento textual, imagem e vetor, além de regiões gráficas ou manuais. Cada decisão registra origem, página, geometria, classificação (`translatable`, `protected`, `review_required` ou `ignored`), razão, necessidade independente de revisão, origem da decisão e possibilidade de override. Decisões textuais acrescentam `linguistic_role` e `evidence`.

O idioma predominante do documento é evidência, não uma lista do que deve ser traduzido. A análise usa a ordem estrutural de blocos, linhas e spans, contexto local direcional, scripts, forma textual e cues como `say`, `repeat`, `pronounce`, `chant` e `vibrate`. Uma instrução pode permanecer traduzível enquanto a fórmula ou pronúncia no mesmo span, no span seguinte ou na linha seguinte é protegida. Evidência forte produz `protected`; evidência textual ambígua mantém `translatable` com `review_required=true`; sem evidência, o texto permanece `translatable`.

Texto editorial comum, caixa alta, nomes próprios, cidades, termos técnicos, palavras desconhecidas e proximidade de imagens não bastam para proteção. Scripts não latinos deliberados, fórmulas e representações fonéticas com evidência estrutural são preservados. Uma memória determinística propaga somente termos com semente forte e função compatível; conflitos de `linguistic_role` bloqueiam a propagação cega.

Imagens e vetores são protegidos; imagens em páginas mistas ou sem texto nativo permanecem protegidas e também pedem revisão. Caracteres privados e símbolos isolados pedem revisão conservadora. A classificação não atribui significado religioso, não reconhece sigilos e não executa OCR.

Overrides `0.1.0` ou `0.2.0` podem mirar `decision_id` ou `source_id` em `element_overrides` e acrescentar caixas em `region_overrides`. Uma decisão editorial prevalece sobre a regra automática. O artefato não contém timestamps, e seus IDs derivam do documento, página, geometria e referências de origem para manter determinismo.

## Estrutura editorial

Construa ordem de leitura, unidades editoriais e segmentos futuros a partir dos dois artefatos homologados:

```bash
python -m scripts.build_editorial_structure \
  workspace/manifest.json \
  workspace/protected-spans.json \
  --out workspace/editorial-structure.json
```

A saída usa schema e algoritmo `0.1.0`. Ela registra hashes canônicos do manifesto e da proteção, recusa schemas incompatíveis, não altera as entradas e é gravada atomicamente. O mesmo manifesto, proteção e configuração produzem JSON byte a byte idêntico.

O reading order combina geometria, sobreposição horizontal, largura, alinhamento e âncoras de largura completa. Colunas simples são lidas integralmente da esquerda para a direita; quando duas ordens permanecem plausíveis, a página recebe `review_required`, confiança, evidências e candidatos alternativos. Cabeçalhos, rodapés e números de página são classificados, não removidos.

Unidades editoriais registram `editorial_id`, página, bbox, linhas e `source_id` de origem, ordem, tipo, confiança, evidências, texto e relação com decisões protegidas. Os IDs usam conteúdo normalizado, tipo, página e vizinhança, sem depender exclusivamente de índice, bbox exata ou bloco do PyMuPDF. `reconcile_editorial_structures` compara versões e informa `same`, `moved`, `modified`, `split`, `merged`, `new` e `removed`.

Segmentos correspondem a unidades editoriais coerentes, nunca a um limite fixo de caracteres. Uma unidade mista mantém partes `translatable` e `protected` na ordem original; conteúdo integralmente protegido, cabeçalhos, rodapés e números de página não viram segmentos de tradução. O agrupamento vetorial é opcional e não destrutivo: cada `graphic_group` mantém os IDs dos desenhos individuais e rejeita cadeias extensas de regras editoriais.

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

Os testes geram PDFs e manifestos sintéticos durante a execução. Cobrem extração; proteção contextual; uma, duas e três colunas; títulos de largura completa; cabeçalhos, rodapés e paginação; tipos editoriais; fragmentação de spans e blocos; partes protegidas; reconciliação; agrupamento vetorial; CLI; determinismo; serialização e imutabilidade das entradas. As camadas continuam heurísticas mesmo após homologação em material real.

## Documentação

- [Roadmap](docs/ROADMAP.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Instruções permanentes para agentes](AGENTS.md)
- [Protocolo editorial da skill](SKILL.md)

Instruções específicas de homologação e testes pontuais são fornecidas fora do repositório.

Não publique livros protegidos por direitos autorais nem remova DRM.

# Stolas — Book Translation Editorial

Skill e ferramentas Python para tradução editorial contextual de PDFs, com preservação de elementos gráficos e auditoria visual.

## Estado

**Em desenvolvimento.** A extração estrutural da Sprint 1 está consolidada e o repositório inclui proteção contextual, estrutura editorial, memória editorial persistente, batching contextual e um motor de tradução estruturado com provider fake. **Não** inclui provider real embutido, OCR automático ou reconstrução de livros.

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

## Memória editorial persistente

Construa ou atualize a memória a partir da estrutura editorial e da proteção correspondentes:

```bash
python -m scripts.build_editorial_memory \
  workspace/editorial-structure.json \
  workspace/protected-spans.json \
  --out workspace/editorial-memory.json
```

Para uma atualização incremental, use `--existing-memory`. Decisões humanas e preferências podem ser fornecidas com `--overrides`; um guia de estilo versionado pode ser fornecido com `--style-guide`. A saída schema `0.1.0` registra hashes das entradas, regras, overrides e estilo, além de versão lógica e histórico sem timestamps.

A memória reúne glossário contextual, termos preservados, ocorrências, decisões, guia de estilo, perguntas, exceções, referências cruzadas, dependências e itens obsoletos. Cada ocorrência aponta para `editorial_id`, `segment_id` quando aplicável, página, contexto curto e papel linguístico. A chave combina termo normalizado e contexto: a mesma superfície em funções distintas não recebe uma decisão global cega.

O merge é determinístico. Decisões humanas têm autoridade máxima; conflitos automáticos são registrados e resolvidos em favor delas. Alterações de decisão marcam dependentes anteriores como obsoletos para recomputação futura. Com entradas e configuração idênticas, a memória existente é reutilizada sem criar versão ou revisão artificial. A camada apenas prepara candidatos e decisões editoriais: não chama modelo de tradução nem produz texto traduzido.

## Batching contextual

Converta segmentos editoriais em lotes determinísticos para uma futura etapa de tradução:

```bash
python -m scripts.build_translation_batches \
  workspace/editorial-structure.json \
  workspace/editorial-memory.json \
  workspace/protected-spans.json \
  --out workspace/translation-batches.json
```

O artefato schema `0.1.0` separa obrigatoriamente `payload`, `context_before` e `context_after`. Cada segmento traduzível aparece como payload exatamente uma vez e na ordem editorial; contexto pode reaparecer apenas nos campos de contexto. Partes protegidas continuam estruturadas dentro do segmento, ficam fora de `payload_text` e carregam seus IDs de decisão de preservação.

O crescimento dos lotes respeita headings, subheadings e limites de segmentos. Heading/subheading e sua primeira explicação formam um grupo semântico inseparável. O orçamento local divide a janela configurável entre payload, contexto, memória, futuras instruções de sistema e resposta. Segmentos ou grupos semânticos maiores que o orçamento não são cortados: permanecem inteiros com warning explícito.

A memória é selecionada por ocorrência, termo/variante, contexto adjacente, preservação e override humano, até o orçamento configurado. Entradas obrigatórias de preservação não são descartadas silenciosamente. Cada inclusão registra motivos e dependências. Guia de estilo, conflitos relevantes, relação entre lotes, envelope de handoff e checkpoints de retomada também são persistidos.

Use `--existing-batches` para reconciliar checkpoints. Mudança de memória relevante marca somente lotes dependentes como obsoletos quando a faixa editorial e a configuração permanecem compatíveis. Mudanças no artefato editorial ou configuração são detectadas. A camada não executa tradução nem chamadas externas.

Opções de orçamento incluem `--max-input-tokens`, `--reserved-output-tokens`, `--reserved-system-tokens`, `--context-budget`, `--memory-budget` e `--profile`.

## Motor de tradução contextual

Execute os batches por uma interface de provider estruturada:

```bash
python -m scripts.translate_batches \
  workspace/translation-batches.json \
  workspace/editorial-memory.json \
  workspace/protected-spans.json \
  --provider fake \
  --target-language pt-BR \
  --out workspace/translation-results.json
```

O provider `fake` é local, determinístico e serve apenas para validar o pipeline; ele não produz tradução editorial real. Providers externos devem implementar `TranslationProvider`, receber requests estruturados e retornar o schema de resposta esperado. O core não contém credenciais, SDK obrigatório ou lógica acoplada a fornecedor.

Cada request contém payload por segmento, contexto somente para leitura, seção, memória e glossário relevantes, estilo, decisões protegidas, handoff anterior e contrato de saída. Fórmulas, transliterações, nomes e termos marcados para preservação são substituídos por placeholders únicos derivados de hashes. A resposta deve devolver cada placeholder exatamente uma vez; o core restaura o texto original e bloqueia omissão, duplicação, placeholder desconhecido ou alteração. Placeholders nunca permanecem no resultado final.

Cada resposta deve mapear explicitamente `segment_id` para texto. Cobertura incompleta, ID extra, ID duplicado, vazamento de contexto ou falha de QA impede aprovação do batch. O QA automático verifica estrutura, proteção, números, URLs, referências de nota, truncamento evidente, duplicação do original, contexto copiado e balanceamento básico; ele não avalia qualidade literária.

Resultados usam schema `0.1.0` e persistem estados, tentativas, dependências, handoffs compactos, propostas terminológicas pendentes, issues de reconciliação, cobertura e checkpoints. `--existing-results --resume` reutiliza batches aprovados compatíveis. Mudanças no batch ou fingerprint relevante reexecutam somente dependentes; retries são limitados e distinguem erro temporário, timeout, erro permanente e resposta inválida.

Propostas do provider nunca alteram a memória mestre. A reconciliação apenas registra conflitos terminológicos, preservação inconsistente, mudança de registro e flags de continuidade, sem reescrever traduções silenciosamente. Atualmente apenas `fake` vem registrado; providers reais são integrações opcionais futuras.

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

Os testes geram PDFs e manifestos sintéticos durante a execução. Cobrem extração; proteção contextual; uma, duas e três colunas; títulos de largura completa; cabeçalhos, rodapés e paginação; tipos editoriais; fragmentação de spans e blocos; partes protegidas; reconciliação; agrupamento vetorial; glossário contextual; merge, versionamento, precedência humana, conflitos, guia de estilo, dependências e invalidação; cobertura 1:1 dos batches; divisão semântica; contexto deslizante; seleção de memória; orçamento; provider fake; placeholders; glossário obrigatório; handoffs reais; retry, resume, QA textual e reconciliação; CLI; determinismo; serialização e imutabilidade das entradas. As camadas continuam heurísticas mesmo após homologação em material real.

## Documentação

- [Roadmap](docs/ROADMAP.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Instruções permanentes para agentes](AGENTS.md)
- [Protocolo editorial da skill](SKILL.md)

Instruções específicas de homologação e testes pontuais são fornecidas fora do repositório.

Não publique livros protegidos por direitos autorais nem remova DRM.

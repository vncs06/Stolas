# Roadmap do Stolas

Status: fundação editorial consolidada até o batching contextual `0.1.0`. Extração estrutural/gráfica, proteção contextual `0.2.0`, estrutura editorial `0.1.0`, memória editorial `0.1.0` e preparação determinística de lotes foram implementadas e homologadas no livro real de 274 páginas; isso não inicia tradução, chamadas a LLM ou reconstrução.

## Oito frentes
1. **Extração inteligente:** blocos, spans, ordem de leitura, geometria, imagens, vetores, notas e colunas.
2. **Tradução contextual:** lotes adaptativos, tradução com continuidade e verificação de cobertura.
3. **Memória editorial:** glossário, guia de estilo, decisões, handoff, checkpoints e retomada.
4. **Proteção de elementos especiais:** sigilos, hebraico, diagramas, fórmulas e regiões não traduzíveis.
5. **Reconstrução editorial:** substituição de texto, fontes, encaixe, preservação de imagens/vetores e reflow controlado.
6. **QA visual avançado:** colisões, cortes, diferenças, evidências e dupla revisão.
7. **Orquestração:** CLI, estados, logs, checkpoints e recuperação.
8. **Testes e homologação:** fixtures sintéticas, testes automatizados e revisão de casos reais autorizados.

## Sprints e critérios de saída
### Sprint 1 — Fundação
- Implementar extração estruturada de texto com coordenadas, fontes e ordem básica de leitura.
- Produzir JSON por página, IDs determinísticos, SHA-256, versão do motor, referências recuperáveis de imagens e propriedades disponíveis dos vetores.
- Marcar candidatos a spans especiais sem alegar reconhecimento perfeito de sigilos.
- Testar com PDFs sintéticos: páginas vazias, texto, hebraico nativo, imagens internas/parcialmente externas/reutilizadas, páginas mistas e vetores.
**Aceite:** inventário completo, schema documentado, testes passando e limitações registradas.

Implementado: schema `0.2.0`, PyMuPDF 2.0 fixado, descoberta complementar e deduplicação de imagens, metadados recuperáveis, vetores enriquecidos, classificação de páginas, revisão conservadora de imagens e processamento incremental de hashes com cache mínimo por XRef. A homologação real preservou texto, imagens, vetores e IDs; o pico de memória deixou de acumular imagens decodificadas. O encerramento técnico da Sprint 1 não amplia o escopo para OCR, tradução ou reconstrução.

### Proteção e classificação — etapa atual
- Consumir o manifesto de extração `0.2.0` sem alterá-lo e produzir artefato versionado próprio.
- Classificar spans, imagens e vetores como traduzíveis, protegidos, pendentes de revisão ou ignorados.
- Representar regiões gráficas e caixas manuais, mantendo referências à origem.
- Aceitar overrides editoriais determinísticos com precedência sobre heurísticas.
- Sinalizar hebraico Unicode, símbolos e possíveis textos rasterizados sem executar OCR ou inferência religiosa.

Implementado e homologado: schema e ruleset de proteção `0.2.0`, contexto direcional entre spans e linhas, separação de instrução e literal, papéis linguísticos, evidências, memória condicionada por função, regiões derivadas e manuais, CLI com escrita atômica, validação de entrada, overrides e IDs determinísticos. A suíte cobre falsos positivos e conflitos de memória; a execução no PDF de 274 páginas foi determinística e confirmou fórmulas, nomes com pronúncia e transliterações extensas sem preservar automaticamente prosa, títulos ou proximidade de imagem.

Limite do aceite: a camada é um filtro heurístico. Texto rasterizado, símbolos gráficos e casos sem evidência estrutural continuam dependendo de revisão editorial ou overrides. Tradução, OCR, batching, reconstrução e glossário editorial completo permanecem fora desta etapa.

### Estrutura editorial — implementada
- Consumir manifesto e proteção `0.2.0` sem alterá-los e produzir artefato versionado `0.1.0`.
- Reconstruir uma, duas e múltiplas colunas simples com confiança, evidências e candidatos de revisão.
- Derivar tipos editoriais e segmentos coerentes com partes protegidas e traduzíveis.
- Criar IDs editoriais independentes de bbox exata e agrupamento do PyMuPDF, além de reconciliar versões.
- Agrupar vetores por proximidade, continuidade e estilo sem perder os membros individuais.

Implementado e homologado: CLI com validação de schemas e hashes, escrita atômica, ordem espacial, detecção recorrente de margens, classificação editorial auditável, IDs estáveis em pequenas mudanças geométricas e reagrupamentos equivalentes, reconciliação de split/merge, segmentos compostos e agrupamento vetorial conservador. Fixtures cobrem uma a três colunas, gráficos intercalados, estrutura, proteção, IDs e erros de entrada. Três execuções integrais no livro real produziram bytes idênticos.

Limite do aceite: reading order e tipos continuam heurísticos. Layouts assimétricos, legendas sem sinais tipográficos, notas não marcadas, texto rasterizado e semântica de desenhos podem exigir revisão. A camada não chama LLM, não traduz, não cria batching por tokens, OCR ou reconstrução.

### Memória editorial — implementada
- Consumir estrutura editorial `0.1.0` e proteção `0.2.0`, validar identidade e hashes e produzir memória versionada `0.1.0`.
- Extrair candidatos de glossário com contexto, variantes, papéis linguísticos, evidências e referências a unidades, segmentos e páginas.
- Persistir termos preservados, decisões, guia de estilo, perguntas, exceções, referências cruzadas, histórico e proveniência.
- Fazer merge determinístico com memória existente, decisões humanas, overrides e estilo; decisões humanas têm autoridade máxima.
- Registrar dependências e marcar resultados anteriores como obsoletos quando uma decisão muda.

Implementado e homologado: CLI com escrita atômica, schemas e hashes validados; candidatos contextuais; preservação proveniente da proteção; nomes, transliterações, termos técnicos e títulos revisáveis; guia de estilo configurável; merge e versionamento lógicos; conflitos explícitos; precedência humana; dependências e invalidação; serialização e IDs determinísticos. Fixtures cobrem consistência contextual, overrides, conflitos, merge, estilo, imutabilidade e determinismo. Três execuções integrais no livro real produziram bytes idênticos, e um merge sem mudanças reutilizou a mesma versão.

Limite do aceite: candidatos lexicais e nomes inferidos são heurísticos e podem conter ruído ou deixar termos raros de fora; revisão humana continua necessária. A camada não cria tradução semântica, batching, chamadas a modelo, OCR, reconstrução ou QA visual.

### Batching contextual — implementado
- Consumir estrutura `0.1.0`, memória `0.1.0` e proteção `0.2.0` sem alterar seus contratos.
- Separar payload de contexto anterior/posterior e provar cobertura 1:1 em ordem global.
- Crescer lotes por fronteiras editoriais e orçamento configurável sem cortar segmentos, protected parts ou heading/subheading e primeira explicação.
- Selecionar memória relevante com motivos auditáveis, incluindo preservações e overrides obrigatórios.
- Persistir relações entre lotes, handoff tipado, dependências e checkpoints retomáveis com invalidação seletiva.

Implementado e homologado: schema e versão `0.1.0`; estimador local determinístico; reservas de payload, contexto, memória, sistema e resposta; contexto deslizante; decisões protegidas estruturadas; memória seletiva; validação de cobertura; IDs determinísticos; escrita atômica; reconciliação de checkpoints e invalidação localizada. Fixtures cobrem capítulos, limites, listas, páginas, protected parts, contexto, memória, orçamento, segmentos superdimensionados, determinismo e retomada. A execução integral no livro de 274 páginas manteve cobertura completa e carregou uma pequena fração do glossário por lote.

Limite do aceite: headings e subheadings dependem da classificação editorial heurística; a estimativa por bytes não corresponde exatamente ao tokenizer de um modelo futuro; segmentos inseparáveis acima do orçamento são preservados com warning. A camada não traduz, chama LLM, executa OCR, reconstrói PDF ou realiza QA visual.

### Sprint 2 — Inteligência editorial
- Usar os batches homologados para execução controlada, cobertura de respostas e reconciliação textual.
- Preencher handoffs e checkpoints somente a partir de resultados reais e auditáveis.
- Integrar adaptador explícito de tradução (sem credenciais embutidas).
**Aceite:** cobertura 1:1 por ID e retomada sem duplicação; revisão humana do texto.

### Sprint 3 — Reconstrução
- Inserção segura de tradução, fontes com cobertura Unicode, ajuste de caixas e reflow controlado.
- Preservar regiões protegidas e verificar remoção de texto original.
**Aceite:** conjunto de PDFs de teste reconstruído sem perdas conhecidas.

### Sprint 4 — Qualidade
- Detectar cortes e colisões, produzir evidências por página, integrar revisão visual final independente.
**Aceite:** nenhuma página sem decisão registrada; casos bloqueados não entregues como concluídos.

### Sprint 5 — Integração e homologação
- CLI de ponta a ponta, documentação, CI, recuperação e ensaios em capítulos e livros autorizados.
**Aceite:** execução reproduzível, relatórios e resultados validados; limitações explícitas.

## Princípios
Não confundir plano com funcionalidade implementada. Não declarar QA visual automático equivalente à inspeção editorial. Não publicar obras protegidas. Evitar alterações irreversíveis no PDF de origem.

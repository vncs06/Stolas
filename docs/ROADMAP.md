# Roadmap do Stolas

Status: Sprint 1 consolidada e tecnicamente pronta para encerramento. A extração estrutural e gráfica passou por duas homologações no material real e por profiling de recursos, sem regressão funcional conhecida no escopo implementado.

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

### Sprint 2 — Inteligência editorial
- Segmentar por fronteiras semânticas e limites de tokens configuráveis.
- Criar glossário, guia de estilo, handoffs, checkpoints e reconciliação.
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

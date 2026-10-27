# Roadmap do Stolas

Status: Sprint 1 em desenvolvimento; extração estruturada inicial e testes sintéticos publicados, ainda sem homologação registrada. Manual operacional Codex em AGENTS.md, docs/WORKFLOWS.md e docs/TOOL_REGISTRY.md.

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
- Produzir JSON por página, IDs estáveis, SHA-256 e registro de desenhos/imagens.
- Marcar candidatos a spans especiais sem alegar reconhecimento perfeito de sigilos.
- Testar com PDF sintético, páginas vazias, texto e imagens.
**Aceite:** inventário completo, schema documentado, testes passando e limitações registradas.

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

---
name: book-translation-editorial
description: Tradução editorial contextual de livros em PDF para português brasileiro com preservação de elementos não traduzíveis e auditoria visual.
---

# Stolas — Book Translation Editorial

## Objetivo
Traduzir livros completos ou trechos de PDF, preferencialmente inglês → pt-BR, preservando semântica, voz autoral, terminologia, referências e projeto gráfico. Não prometer equivalência pixel a pixel nem apresentar a skill como tradutor autônomo já implementado.

## Entrada e saída
Receber PDF legítimo, idioma alvo e preferências. Entregar PDF traduzido, glossário, manifesto e relatório de QA; nunca sobrescrever original, remover DRM ou redistribuir conteúdo protegido.

## Preflight
Inventariar **100% das páginas**: dimensões, rotação, fontes, camada textual, blocos, ordem de leitura, imagens, vetores, links, notas, cabeçalhos e rodapés. Classificar PDF nativo, escaneado ou misto. Evitar OCR quando houver texto confiável. Atribuir IDs estáveis e hashes.

Na implementação atual da Sprint 1, o manifesto registra o PyMuPDF e sua versão, combina APIs independentes para descobrir imagens, mantém `xref`, geometria, dimensões, formato e hashes recuperáveis, e preserva propriedades disponíveis dos traçados vetoriais. Páginas com imagens são sinalizadas para revisão de possível texto rasterizado; OCR não é executado automaticamente.

A camada de proteção consome o manifesto `0.2.0` e produz um artefato derivado com schema e ruleset `0.2.0`, sem alterar a extração. Ela classifica fragmentos de spans nativos, imagens e vetores, cria regiões gráficas protegidas e aceita overrides editoriais determinísticos. É uma triagem conservadora: não traduz, não executa OCR, não reconhece semanticamente sigilos e não modifica conteúdo gráfico.

A camada editorial consome manifesto e proteção `0.2.0` e produz um artefato independente `0.1.0`. Ela reconstrói ordem de leitura e colunas, classifica unidades editoriais com confiança e evidências, cria IDs lógicos resistentes a pequenas mudanças de bbox e reagrupamento de spans/blocos, produz segmentos compostos e agrupa vetores relacionados sem remover os desenhos individuais. Ambiguidade de ordem deve permanecer explícita, com candidatos e `review_required`.

A memória editorial consome estrutura editorial `0.1.0` e proteção `0.2.0` e produz um artefato persistente `0.1.0`. Ela extrai candidatos contextuais, registra ocorrências e proveniência, consolida termos preservados, decisões e estilo, e mantém dependências para invalidação. O merge é determinístico e decisões humanas prevalecem sobre candidatos automáticos. Esta camada não traduz.

O batching contextual consome estrutura, memória e proteção homologadas e produz lotes `0.1.0`. Ele separa payload de contexto, prova cobertura 1:1, seleciona memória relevante e cria handoffs e checkpoints determinísticos. Partes protegidas permanecem estruturadas e nunca são incorporadas ao texto traduzível. Esta camada não chama LLM.

O motor de tradução consome batches `0.1.0`, memória `0.1.0` e proteção `0.2.0`. Ele constrói requests estruturados, executa um `TranslationProvider`, valida cobertura e invariantes, restaura placeholders, produz handoffs/checkpoints e reconcilia issues sem modificar a memória. O provider fake homologado valida o pipeline, mas não é tradução editorial real; provider real não está embutido.

## Estrutura e segmentação editorial
- Reconstruir ordem por geometria e agrupamento espacial; nunca alternar linhas de colunas quando a leitura correta percorre cada coluna inteira.
- Tratar títulos de largura completa como âncoras. Classificar cabeçalhos, rodapés e paginação recorrentes sem remover conteúdo.
- Registrar `source_id` e `editorial_id`. O segundo deriva de documento, página, conteúdo normalizado, tipo e vizinhança; bbox exata, índice ordinal ou bloco do motor não podem ser sua única identidade.
- Classificar unidades como heading, subheading, paragraph, list_item, caption, note, footnote, header, footer, page_number, quote, body ou unknown, sempre com confiança e evidências auditáveis.
- Segmentar por unidade editorial coerente. Não dividir por span, linha física ou quantidade fixa de caracteres.
- Manter instrução traduzível e literal protegido na mesma unidade quando forem semanticamente inseparáveis, representando-os como partes ordenadas. Unidades totalmente protegidas não entram em futuros lotes de tradução.
- Agrupar vetores somente com evidência de proximidade, continuidade e estilo compatível. Preservar membros individuais e rejeitar encadeamentos extensos de linhas editoriais.
- Reconciliar versões como same, moved, modified, split, merged, new ou removed antes de reutilizar checkpoints futuros.

## Orquestração adaptativa
1. Estimar carga por tokens, densidade e complexidade visual; dividir por capítulos, seções e parágrafos, recursivamente quando necessário. Sobreposição de contexto não pode duplicar texto na saída.
2. Persistir `manifest.json`, `style-guide.md`, `glossary.json`, `protected-spans.json`, `decisions.jsonl` e `progress.json`.
3. Antes de cada lote carregar guia, glossário, decisões, resumo estruturado, capítulos vizinhos e trechos limítrofes. Ao concluir salvar handoff com IDs, termos, decisões e pendências.
4. Em paralelismo, usar faixas sem sobreposição; propostas terminológicas locais devem ser reconciliadas centralmente, sem edição concorrente do glossário mestre.
5. Validar cobertura: cada segmento traduzível deve aparecer exatamente uma vez, sem omissões, truncamentos, duplicações ou placeholders perdidos.
6. Reconciliar voz, registro, anáforas, referências, índice e continuidade entre lotes antes da diagramação.
7. Criar checkpoints atômicos com hashes e versões editoriais; invalidar resultados dependentes de decisões alteradas. Não declarar conclusão parcial como integral.

Na implementação atual, o batching local usa orçamento configurável e estimativa determinística por bytes UTF-8. `payload`, `context_before` e `context_after` são campos distintos; somente payload conta para cobertura e futura emissão. Lotes crescem até a fronteira segura anterior ao limite, sem cortar segmentos nem o par heading/subheading e primeira explicação. Um segmento inseparável acima do limite é mantido inteiro e sinalizado, nunca truncado.

## Batching contextual
- Cada `segment_id` traduzível deve aparecer como payload exatamente uma vez, em ordem global. Contexto não pode ser confundido com conteúdo de saída.
- Não transformar unidades integralmente protegidas em payload. Em segmentos mistos, manter partes e decisões protegidas separadas do `payload_text`.
- Reservar orçamento independente para payload, contexto, memória, futuras instruções e resposta. Não depender de tokenizer ou API externa para produzir um artefato válido.
- Carregar apenas entradas de memória justificadas por ocorrência, variante, contexto, preservação ou decisão humana. Registrar o motivo de cada inclusão e seus IDs de dependência.
- Incluir estilo global e decisões obrigatórias de preservação mesmo quando candidatos opcionais forem excluídos pelo orçamento; sinalizar excesso obrigatório.
- Persistir vínculos anterior/próximo, seção, faixa global, envelope de handoff e checkpoint. Checkpoints concluídos devem sobreviver a reconstruções idênticas.
- Invalidar somente lotes cuja memória relevante, proteção, estrutura ou configuração mudou quando a correspondência por faixa de payload permitir. Não reutilizar checkpoint incompatível.
- Derivar IDs de documento, artefato editorial, faixa de segmentos, configuração e dependências relevantes, sem timestamps.

## Memória editorial
- Identificar cada entrada por termo normalizado e contexto funcional; nunca impor uma tradução ou preservação global apenas pela superfície textual.
- Registrar ocorrências com `editorial_id`, `segment_id` quando houver, página, contexto curto e papel linguístico, mantendo evidência rastreável até os artefatos de origem.
- Tratar extração lexical e nomes inferidos como candidatos revisáveis. Termos protegidos com evidência mantêm a decisão correspondente, sem transformar prosa comum em conteúdo intocável.
- Aplicar decisões e preferências humanas com autoridade máxima. Registrar conflitos e preservar a decisão humana quando uma regra automática posterior discordar.
- Fazer merge por identidade lógica, com ordenação canônica, histórico versionado e hashes de entrada, regras, overrides e estilo. Entradas idênticas não criam nova revisão.
- Registrar dependências entre termos e unidades/segmentos; uma alteração de decisão deve marcar resultados dependentes anteriores como obsoletos, sem apagá-los silenciosamente.
- Manter perguntas, exceções e decisões futuras explícitas. Não preencher traduções candidatas por inferência quando nenhuma tradução humana ou etapa de tradução as forneceu.

## Proteção e tradução
- Tratar a classificação automática como elegibilidade preliminar, nunca como decisão semântica definitiva. Decisões editoriais manuais prevalecem sobre regras e heurísticas.
- Traduzir a linguagem editorial da obra e preservar o que o original mantém deliberadamente como outro idioma, transliteração, fórmula, nome especial ou representação fonética. O idioma-alvo e o idioma local detectado não decidem isso sozinhos.
- Usar contexto direcional entre spans e linhas. Separar a instrução traduzível do literal preservado mesmo quando aparecem no mesmo span; não estender cues a toda a página.
- Registrar `linguistic_role` e `evidence` em decisões heurísticas. Evidência forte pode produzir `protected`; ambiguidade textual deve continuar `translatable` com `review_required=true`; ausência de evidência permanece `translatable`.
- Propagar termos pela memória somente quando a semente, a forma e a função forem compatíveis. Conflitos de papel linguístico impedem propagação automática.
- Manter imagens e vetores protegidos por padrão. Uma imagem pode continuar protegida e simultaneamente exigir revisão por possível texto rasterizado.
- Preservar scripts não latinos deliberados sem inferir significado religioso. Encaminhar caracteres privados e símbolos isolados para revisão.
- Proteger spans não traduzíveis com placeholders reversíveis: sigilos, símbolos, fórmulas, diagramas, grafias ritualísticas, hebraico, latim litúrgico e referências especiais quando necessário.
- Não tratar indiscriminadamente toda expressão estrangeira como intocável.
- Não usar caixa alta, nome próprio, cidade, termo técnico, palavra desconhecida ou proximidade gráfica como evidência suficiente de proteção.
- Traduzir por parágrafos e seções com contexto, nunca por linhas isoladas; preservar proposições, ambiguidades, voz, notas, números e citações.
- Manter glossário de termos técnicos e nomes; não inventar equivalentes consagrados, etimologias ou fontes.
- Revisar terminologia e conteúdo em segunda passagem e registrar dúvidas.

## Execução de tradução estruturada
- Enviar ao provider um objeto estruturado por batch, nunca texto solto: payload, contexto, seção, memória, glossário, estilo, decisões protegidas, handoff e contrato de saída.
- Substituir cada parte protegida e cada termo com preservação aprovada por placeholder determinístico, único e reversível. Validar ocorrência exatamente única antes de restaurar bytes originais.
- Exigir uma resposta por `segment_id`. Omissão, duplicação, ID desconhecido, placeholder ausente/extra ou contexto emitido bloqueiam o batch.
- Aplicar como obrigação somente traduções aprovadas e overrides humanos. Preservações aprovadas são invariantes; candidatos continuam sugestões e não podem virar regra automática.
- Manter propostas do modelo em fila `pending_review`; nunca editar o glossário mestre diretamente a partir da resposta.
- Produzir handoff compacto com resumo, termos, propostas, referentes, ambiguidades, pendências e continuidade. Não copiar capítulos ou traduções integrais no handoff.
- Retomar batches aprovados somente quando ID e fingerprint de dependência coincidirem. Reexecutar batches stale; limitar retries para timeout e falha temporária; não repetir indefinidamente erro permanente ou resposta inválida.
- Executar QA estrutural/textual antes da aprovação: cobertura, proteção, placeholders, números, URLs, notas, truncamento, duplicação e vazamento de contexto. Não confundir essas verificações com revisão literária.
- Reconciliar terminologia, nomes preservados, registro e continuidade como issues e recomendações. Não reescrever silenciosamente.
- Providers reais devem implementar a interface genérica e receber credenciais externamente. Nunca persistir chaves ou tornar SDK de fornecedor requisito do core.

## Reconstrução editorial
Preservar geometria, imagens, vetores, sigilos, ornamentos e tipografia quando possível. Remover corretamente texto original antes de inserir tradução, sem texto original visível ou extraível sob a nova camada. Usar fontes licenciadas com suporte ao idioma. Não cortar texto nem reduzir fontes até ficarem ilegíveis. Se a tradução não couber, registrar divergência e pedir decisão sobre reflow/expansão; nunca omitir conteúdo.

## Auditoria obrigatória
**Primeira passagem, página a página:** renderizar original e traduzido, comparar lado a lado e com diff quando útil. Conferir conteúdo, margens, alinhamentos, tipografia, figuras, sigilos, tabelas, cabeçalhos, rodapés, notas e colisões. Corrigir e renderizar novamente. Registrar `pendente`, `em revisão`, `aprovada` ou `bloqueada`.

**Segunda passagem, independente, após montar o PDF inteiro:** inspecionar visualmente **100% das páginas finais**, inclusive páginas aparentemente vazias. Verificar sobreposições, figuras/vetores alterados, recortes, transparências, deslocamentos, fontes, áreas protegidas e continuidade. Registrar `aprovada_final`, `corrigir` ou `bloqueada` por página com evidências. Correções reabrem a revisão, inclusive de páginas vizinhas afetadas.

Separar QA estrutural, semântico e gráfico. Auditoria automática é apenas pré-verificação, não certificação. Sigilos podem ser vetores e contagens de desenhos não provam integridade. Não afirmar que houve inspeção visual sem realizá-la.

## Portões de qualidade
`preflight → segmentado → traduzido → QA textual → reconciliado → diagramado → QA visual por página → QA visual final → entregue`

Nenhuma etapa deve ser aprovada sem evidências; se faltarem recursos, salvar estado retomável e relatar pendências. Revisar índice, remissões, glossário e fronteiras de lotes após integração.

## Homologação
Testar página textual, página mista e página com sigilos/hebraico, depois capítulo, só então livro completo. Registrar hash de origem e destino, versão de glossário, renderizações e decisão do revisor.

## Limites atuais
Esta skill é um protocolo de trabalho e contém um motor estrutural com provider fake, não um tradutor editorial autônomo nem provider real embutido. Não contém composição de PDF ou revisão visual independente. A proteção, a estrutura, a extração de candidatos da memória editorial e a detecção de fronteiras estruturais continuam heurísticas e requerem revisão para casos ambíguos, scripts rasterizados, layouts complexos, legendas sem sinais tipográficos e gráficos semanticamente incertos. QA textual automático não prova fidelidade, naturalidade ou qualidade literária. A estimativa local de tokens não substitui o tokenizer de um modelo futuro. A extração não reconhece sigilos ou diagramas nem lê hebraico rasterizado. Imagens e vetores são registrados para recuperação futura, sem promessa de reconstrução visual perfeita.

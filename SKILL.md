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

## Orquestração adaptativa
1. Estimar carga por tokens, densidade e complexidade visual; dividir por capítulos, seções e parágrafos, recursivamente quando necessário. Sobreposição de contexto não pode duplicar texto na saída.
2. Persistir `manifest.json`, `style-guide.md`, `glossary.json`, `protected-spans.json`, `decisions.jsonl` e `progress.json`.
3. Antes de cada lote carregar guia, glossário, decisões, resumo estruturado, capítulos vizinhos e trechos limítrofes. Ao concluir salvar handoff com IDs, termos, decisões e pendências.
4. Em paralelismo, usar faixas sem sobreposição; propostas terminológicas locais devem ser reconciliadas centralmente, sem edição concorrente do glossário mestre.
5. Validar cobertura: cada segmento traduzível deve aparecer exatamente uma vez, sem omissões, truncamentos, duplicações ou placeholders perdidos.
6. Reconciliar voz, registro, anáforas, referências, índice e continuidade entre lotes antes da diagramação.
7. Criar checkpoints atômicos com hashes e versões editoriais; invalidar resultados dependentes de decisões alteradas. Não declarar conclusão parcial como integral.

## Proteção e tradução
- Proteger spans não traduzíveis com placeholders reversíveis: sigilos, símbolos, fórmulas, diagramas, grafias ritualísticas, hebraico, latim litúrgico e referências especiais quando necessário.
- Não tratar indiscriminadamente toda expressão estrangeira como intocável.
- Traduzir por parágrafos e seções com contexto, nunca por linhas isoladas; preservar proposições, ambiguidades, voz, notas, números e citações.
- Manter glossário de termos técnicos e nomes; não inventar equivalentes consagrados, etimologias ou fontes.
- Revisar terminologia e conteúdo em segunda passagem e registrar dúvidas.

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
Esta skill é um protocolo de trabalho; não contém, por si só, motor completo de tradução, segmentação, composição de PDF ou revisão visual independente. A extração não reconhece semanticamente sigilos ou diagramas, não lê hebraico rasterizado e não garante ordem em layouts complexos. Imagens e vetores são registrados para recuperação futura, sem promessa de reconstrução visual perfeita.

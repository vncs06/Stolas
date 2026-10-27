# Estratégia de testes

## Camadas
1. **Unitários:** IDs estáveis, bboxes, texto vazio, rotação, caracteres especiais e serialização JSON.
2. **Integração:** PDF sintético com parágrafos, figuras, desenhos, hebraico, notas e múltiplas páginas.
3. **Regressão:** comparação de manifests, cobertura de segmentos e relatórios estruturais.
4. **Visual:** renderização lado a lado, inspeção de 100% das páginas finais e decisões documentadas.
5. **Editorial:** amostra revisada por pessoa fluente, continuidade entre lotes e glossário consistente.

## Critérios
- Nenhuma página omitida.
- Cada segmento traduzível tem exatamente um resultado.
- Nenhuma região protegida alterada sem autorização explícita.
- Sem truncamento, sobreposição ou texto original oculto sob tradução.
- Erros e incertezas reportados; páginas bloqueadas não marcadas como aprovadas.

## Execução inicial
`python -m unittest discover -s tests -v`

O projeto ainda não dispõe de suíte de homologação com PDFs complexos; adicionar fixtures sintéticas antes de declarar qualquer sprint concluída.

## Matriz obrigatória — homologação da Sprint 1
| Caso | Verificação | Resultado esperado |
|---|---|---|
| Teste sintético | unittest | Sem falhas, ou traceback documentado |
| PDF textual | texto, spans, fontes, bboxes | Conteúdo presente e geometria válida |
| PDF multipágina | contagem e IDs | 100% das páginas e IDs únicos |
| Página vazia | registro de página | Página presente, flag heurística de revisão OCR |
| Duas colunas | ordem de leitura | Inspecionar; registrar divergência |
| Imagens | blocos de imagem | Presença/caixa quando reconhecida pelo PyMuPDF |
| Desenhos/sigilos | limites vetoriais | Presença de desenhos, sem reconhecimento semântico garantido |
| Hebraico | spans Unicode | Inspeção dos caracteres e direção |
| PDF escaneado | ausência de camada textual | Sinalização para revisão OCR; OCR não implementado |
| PDF protegido/inválido | falha controlada | Erro documentado; sem tentativa de burlar proteção |

O agente deve registrar `PASSOU`, `FALHOU`, `LIMITAÇÃO CONHECIDA` ou `NÃO TESTADO` para cada caso, além de ambiente, SHA do commit, comandos, páginas e evidências. Relatório local: `reports/HOMOLOGATION.md`.

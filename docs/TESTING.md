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

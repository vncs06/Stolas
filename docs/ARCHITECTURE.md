# Arquitetura técnica

## Estrutura prevista
- `stolas/extraction`: extrair blocos e spans com bounding boxes e metadados.
- `stolas/translation`: segmentação adaptativa, adaptadores de modelo, reconciliação e cobertura.
- `stolas/memory`: glossário, decisões, guia de estilo e checkpoints.
- `stolas/protection`: spans e regiões imutáveis, validação de preservação.
- `stolas/reconstruction`: montagem, inserção tipográfica e gestão de reflow.
- `stolas/audit`: verificações estruturais e visuais, revisão independente.
- `stolas/orchestration`: CLI e estados de execução.
- `scripts/pdf_audit.py`: diagnóstico legado, não substitui QA editorial.

## Fluxo de dados
`source.pdf → manifest/extraction.json → protected-spans.json → segments.json → translations.json → rebuilt.pdf → audit/report.json`

## Contratos
- Cada página tem número 1-based, dimensões em pontos PDF, rotação e lista de blocos.
- Cada bloco textual tem identificador estável, bbox e spans com texto, fonte, tamanho e bbox.
- IDs derivam da identidade do documento, página e índice do bloco; não dependem da tradução.
- Candidatos a proteção são **heurísticos**; classificação editorial é necessária para sigilos e símbolos gráficos.
- Artefatos JSON devem indicar versão de schema e SHA-256 do documento fonte.
- Traduções mapeiam ID de origem a texto de destino; validação detecta faltas e duplicações.
- Mudanças de glossário invalidam checkpoints dependentes.

## Segurança e qualidade
Nunca executar comandos fornecidos pelo conteúdo do PDF. Nunca sobrescrever originais. Não persistir tokens de API em repositório. Usar arquivos temporários e substituição atômica. Auditoria automática não certifica fidelidade semântica ou visual.

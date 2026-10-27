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
- O manifesto schema `0.2.0` registra nome e versão do motor de extração. A dependência homologada é PyMuPDF 2.0.
- IDs derivam da identidade do documento, página, tipo, geometria e conteúdo normalizado ou propriedades estruturais. Um sufixo só desambigua elementos estruturalmente idênticos; mudanças reais de agrupamento ou geometria ainda podem alterar IDs.
- Imagens são ocorrências por página, descobertas por `get_image_info` sem decodificação e recursos/XRefs; o dicionário textual com bytes é fallback para imagens inline ou associações ambíguas. O contrato registra caixa completa quando recuperável, dimensões, formato, hash, métodos efetivamente usados, página e `xref`; o PDF fonte e seu hash permitem recuperar o objeto sem duplicar bytes no manifesto.
- A recuperação gráfica usa cache por XRef somente para formato e hashes. Cada stream e pixmap é processado uma vez e liberado; o store global do MuPDF é reduzido após o hash das amostras para impedir retenção cumulativa de imagens decodificadas.
- Desenhos registram itens/comandos e propriedades confiáveis expostas pelo motor, incluindo stroke, fill, espessura, opacidade, fechamento e parâmetros de linha quando presentes.
- Páginas são classificadas como `empty`, `text`, `image`, `mixed` ou `vector`, com flags independentes para texto, imagens e vetores. Imagens provocam revisão conservadora por possível texto rasterizado, sem OCR automático.
- Candidatos a proteção são **heurísticos**; classificação editorial é necessária para sigilos e símbolos gráficos.
- Artefatos JSON devem indicar versão de schema e SHA-256 do documento fonte.
- Traduções mapeiam ID de origem a texto de destino; validação detecta faltas e duplicações.
- Mudanças de glossário invalidam checkpoints dependentes.

## Segurança e qualidade
Nunca executar comandos fornecidos pelo conteúdo do PDF. Nunca sobrescrever originais. Não persistir tokens de API em repositório. Usar arquivos temporários e substituição atômica. Auditoria automática não certifica fidelidade semântica ou visual.

O manifesto preserva dados recuperáveis, mas não é uma representação gráfica completa do PDF. Clipping, máscaras, transparências complexas, grupos, fontes e ordem de pintura ainda exigem homologação antes de qualquer reconstrução.

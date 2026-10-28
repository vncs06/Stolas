# Arquitetura técnica

## Estrutura prevista
- `stolas/extraction`: extrair blocos e spans com bounding boxes e metadados.
- `stolas/translation`: segmentação adaptativa, adaptadores de modelo, reconciliação e cobertura.
- `stolas/memory`: glossário, decisões, guia de estilo e checkpoints.
- `stolas/protection`: classificação de elegibilidade, regiões protegidas e overrides editoriais.
- `stolas/editorial`: reading order, estrutura editorial, segmentos compostos, IDs lógicos, reconciliação e agrupamento vetorial não destrutivo.
- `stolas/reconstruction`: montagem, inserção tipográfica e gestão de reflow.
- `stolas/audit`: verificações estruturais e visuais, revisão independente.
- `stolas/orchestration`: CLI e estados de execução.
- `scripts/pdf_audit.py`: diagnóstico legado, não substitui QA editorial.

## Fluxo de dados
`source.pdf → manifest/extraction.json → protected-spans.json → editorial-structure.json → translations.json → rebuilt.pdf → audit/report.json`

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
- O artefato de proteção schema `0.2.0` referencia e inclui o hash canônico do manifesto de extração `0.2.0`; nunca altera o manifesto recebido. O `ruleset_version` também é `0.2.0`.
- Cada decisão de proteção referencia o ID de origem, página, tipo, bbox, classificação, razão, origem da decisão e se aceita override. Fragmentos textuais também carregam localizador de linha, span e caracteres, `linguistic_role` e `evidence`.
- `classification` e `review_required` são campos independentes. Texto ambíguo pode continuar `translatable` e pedir revisão; uma imagem pode ser `protected` e simultaneamente exigir revisão por possível texto rasterizado.
- A análise textual respeita a ordem de bloco, linha e span, usa somente uma janela local direcional e pode dividir um span para manter a instrução traduzível e proteger o literal. Cues em outra parte da página não são evidência suficiente.
- O idioma predominante e o idioma local são sinais auxiliares. Scripts, estrutura fonética, formatação e função contextual determinam a elegibilidade em conjunto; proximidade de imagem nunca basta.
- A memória de termos recebe somente sementes protegidas com alta confiança. A chave normaliza caixa e variantes de separadores, mas a propagação ainda exige forma ou contexto compatível e é bloqueada quando as sementes têm papéis linguísticos conflitantes.
- Imagens e vetores geram regiões derivadas. Regiões manuais aceitam bbox e referências opcionais a elementos, sem modificar ou rasterizar o conteúdo coberto.
- Overrides editoriais externos podem mirar uma decisão ou todos os spans de um elemento. A decisão manual prevalece; uma região gráfica derivada é atualizada ou removida quando o elemento deixa de ser protegido. Alvos ausentes, duplicados ou incompatíveis falham explicitamente.
- IDs de decisões e regiões derivam de SHA-256 do documento, página, tipo, geometria e referências estruturais. Sem timestamps e com ordenação canônica, mesmas entradas, regras e overrides produzem saída idêntica.
- Artefatos JSON devem indicar versão de schema e SHA-256 do documento fonte.
- Traduções mapeiam ID de origem a texto de destino; validação detecta faltas e duplicações.
- Mudanças de glossário invalidam checkpoints dependentes.

## Schema editorial `0.1.0`

O artefato editorial é derivado e separado. Seus campos de topo são `schema_version`, `algorithm_version`, `source_manifest`, `source_protection`, `source_document`, `pages`, `editorial_units`, `segments`, `graphic_groups`, `warnings` e `statistics`. Ele referencia hashes canônicos das duas entradas e nunca as modifica.

- O reading order usa caixas, alinhamento, largura e agrupamento horizontal. Âncoras largas separam zonas; colunas disjuntas são percorridas integralmente da esquerda para a direita. Geometrias sobrepostas permanecem em coluna única. Baixa confiança produz candidato alternativo e revisão explícita.
- Cada unidade contém `editorial_id`, `source_ids`, localizadores de linhas, página, bbox, ordem, tipo editorial, confiança, evidências, texto, partes e relação com proteção.
- O `editorial_id` deriva do hash do documento, página, tipo, conteúdo normalizado e vizinhança. IDs de origem continuam preservados, mas não participam da identidade primária; pequenas mudanças de bbox e reagrupamentos equivalentes de spans/blocos não alteram o ID quando o conteúdo lógico permanece igual.
- A reconciliação compara versões por ID, texto normalizado, posição e similaridade, reportando `same`, `moved`, `modified`, `split`, `merged`, `new` ou `removed`.
- Segmentos excluem cabeçalho, rodapé, número de página e unidades integralmente protegidas. Unidades mistas carregam partes ordenadas `translatable`, `protected`, `review_required` ou `ignored`, com decisões de origem.
- `graphic_groups` são componentes opcionais de vetores próximos e estilisticamente compatíveis. Os desenhos originais permanecem intactos. Limites de extensão impedem que cadeias de linhas editoriais formem um diagrama gigante.
- A serialização não contém timestamps, usa ordenação estável e substituição atômica. Mesmas entradas produzem bytes e IDs idênticos.

## Segurança e qualidade
Nunca executar comandos fornecidos pelo conteúdo do PDF. Nunca sobrescrever originais. Não persistir tokens de API em repositório. Usar arquivos temporários e substituição atômica. Auditoria automática não certifica fidelidade semântica ou visual.

O manifesto preserva dados recuperáveis, mas não é uma representação gráfica completa do PDF. Clipping, máscaras, transparências complexas, grupos, fontes e ordem de pintura ainda exigem homologação antes de qualquer reconstrução.

## Schema de proteção `0.2.0`

Campos de topo: `schema_version`, `ruleset_version`, `source_manifest`, `source_document`, `term_memory`, `classifications`, `protected_regions`, `summary` e, quando aplicável, `overrides`. Decisões usam uma das classificações `translatable`, `protected`, `review_required` ou `ignored`. `decision_source` distingue `rule`, `heuristic` e `editorial_override`; confiança só aparece quando a conclusão é heurística.

O arquivo opcional de overrides aceita `schema_version: 0.1.0` ou `0.2.0`, pode fixar `source_sha256`, contém `element_overrides` identificados por `decision_id` ou `source_id` e `region_overrides` com página e bbox. Arquivos são escritos por temporário e substituição atômica.

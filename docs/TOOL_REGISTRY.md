# Registro de ferramentas — fonte operacional

Este registro distingue ferramentas **implementadas**, **experimentais** e **planejadas**. Antes de executar, verificar se o código e a CLI existem. Atualizar a cada alteração.

## Implementada, experimental: extração estruturada
- Código: `stolas/extraction/extract.py`
- Entrada: PDF local acessível, não criptografado.
- Execução (na raiz do repositório): `python -m scripts.extract_pdf arquivo.pdf --out workspace/manifest.json`
- Saída: JSON UTF-8 com `schema_version`, SHA-256 do arquivo, contagem de páginas, páginas, blocos de texto/imagem, spans com fonte/tamanho/caixa, limites de desenhos e flag heurística `requires_ocr_review`.
- Erros esperados: arquivo ausente, PDF inválido, permissões, PDF protegido por senha, falha do PyMuPDF.
- Verificação: JSON parseável, número de páginas igual ao PDF, IDs únicos, coordenadas finitas, texto de amostra presente.
- Limites: ordem de leitura apenas heurística; não extrai imagem binária, não faz OCR, não identifica sigilos semanticamente, não preserva por si só conteúdo.
- Próximo passo: revisar amostras e produzir relatório de homologação.

## Implementada, experimental: inventário básico
- Código: `scripts/pdf_audit.py`
- Execução: `python scripts/pdf_audit.py inventory arquivo.pdf --out workspace/inventory.json`
- Saída: JSON com métricas básicas por página.
- Limites: inventário não equivale a extração editorial completa.
- Próximo passo: comparar contagem de páginas e indicadores com o manifesto estruturado.

## Implementada, experimental: pré-auditoria visual
- Código: `scripts/pdf_audit.py`
- Execução: `python scripts/pdf_audit.py audit original.pdf traduzido.pdf --out qa/`
- Entradas: dois PDFs locais.
- Saídas: PNG original/destino/diff por página, `report.json`, `report.csv`.
- Erros: arquivos inválidos, falta de permissão, memória insuficiente, renderização falha.
- Limites: diferença de pixels **não** certifica fidelidade editorial, sigilos, fontes, semântica ou diagramação. Sem PDF reconstruído, esta ferramenta NÃO é teste de tradução.
- Próximo passo: inspeção visual independente.

## Implementada, ainda sem homologação registrada: testes sintéticos
- Código: `tests/test_extraction.py`
- Execução: `python -m unittest discover -s tests -v`
- Saída: resultado unittest; registrar stdout/stderr e código de saída.
- Limites: uma suíte sintética inicial não prova robustez em livros reais.

## Planejadas — NÃO executar como se existissem
- `stolas/translation`: tradução contextual e lotes adaptativos.
- `stolas/memory`: glossário, decisões e checkpoints.
- `stolas/protection`: detecção/classificação e integridade de spans/regiões protegidos.
- `stolas/reconstruction`: substituição e diagramação.
- `stolas/audit`: auditoria aprofundada e revisão final independente.
- `stolas/orchestration`: execução ponta a ponta, estados e recuperação.

## Critério para promover uma ferramenta
Implementação existente + CLI/contrato documentado + testes executados + limitações registradas + revisão de regressão. Não promover com base apenas em planejamento ou código não executado.

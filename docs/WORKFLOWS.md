# Procedimentos operacionais do Codex — Stolas

## Seleção de fluxo
- Recebeu PDF(s) para **testar**: executar **Fluxo A**.
- Recebeu pedido de **implementar/corrigir**: executar **Fluxo B**.
- Recebeu pedido de **traduzir**: executar **Fluxo C**.
Não assumir autorização para publicar, modificar PDF original ou implementar módulos novos.

## Fluxo A — Homologação atual (Sprint 1)
**Objetivo:** avaliar extração estruturada e inventário, NÃO tradução.

### A0. Preparação
1. Ler `AGENTS.md`, `docs/TOOL_REGISTRY.md`, `docs/TESTING.md`.
2. Registrar commit SHA, sistema operacional, versão Python e PyMuPDF.
3. Preparar ambiente virtual e instalar `requirements.txt`.
4. Armazenar PDFs de teste fora do versionamento. Não imprimir conteúdo integral do livro em relatórios.

### A1. Testes automatizados
Executar:
```bash
python -m unittest discover -s tests -v
```
Se falhar: preservar traceback, identificar teste, classificar defeito; não prosseguir declarando aprovação.

### A2. Extração e inventário
Para cada PDF:
```bash
python -m scripts.extract_pdf "arquivo.pdf" --out "workspace/manifest.json"
python scripts/pdf_audit.py inventory "arquivo.pdf" --out "workspace/inventory.json"
```
Usar nomes de saída exclusivos para múltiplos PDFs. Conferir código de saída e parsear JSON. Validar SHA-256, `page_count`, índices 1-based, IDs únicos, bboxes e contagem de páginas em ambos os arquivos.

### A3. Inspeção orientada a casos
Selecionar páginas com: texto comum, página vazia, múltiplas colunas, figura, desenho vetorial, hebraico, sigilo e página escaneada (quando disponíveis).
Para cada caso:
- Confirmar presença e posição dos elementos esperados, comparando PDF original e JSON.
- Diferenciar falha do extrator de capacidade ainda não implementada.
- Para hebraico: conferir caracteres Unicode e ordem; não pressupor suporte perfeito.
- Para sigilos: conferir registro de imagem/desenho, sem alegar reconhecimento semântico.
- Para escaneados: esperar `requires_ocr_review` quando não há texto; não executar OCR automaticamente.
- Em colunas: registrar divergências de ordem de leitura como limitações/defeitos com evidências.

### A4. Relatório
Criar `reports/HOMOLOGATION.md` local, com:
- commit testado, ambiente, PDFs anonimizados, hashes e páginas;
- comandos e resultados; testes `PASSOU/FALHOU/LIMITAÇÃO CONHECIDA/NÃO TESTADO`;
- páginas amostradas, evidências, defeitos reproduzíveis, severidade e sugestões;
- resultado geral: **APROVADO PARA CONTINUAR**, **BLOQUEADO** ou **INCONCLUSIVO**;
- declaração expressa de que tradução/reconstrução/QA final não foram testados.
Não fazer commit nem push sem autorização.

## Fluxo B — Desenvolvimento
1. Ler documentação e código atual, escolher menor incremento da sprint.
2. Escrever critérios de aceite e teste de regressão antes ou junto da mudança.
3. Implementar com IDs estáveis, entradas/saídas explícitas e sem alterar originais.
4. Executar testes; registrar resultados reais e limitações.
5. Atualizar `AGENTS.md` se mudar o protocolo, e obrigatoriamente registro, workflows, roadmap, testing, README/SKILL conforme impacto.
6. Reportar arquivos alterados, comandos, evidências e pendências. Publicar apenas quando autorizado.

## Fluxo C — Tradução editorial
**Ainda não operacional de ponta a ponta.** Não inventar CLI de tradução, reconstrução, memória ou proteção. Explicar que somente extração e pré-auditoria existem; solicitar autorização para desenvolvimento ou realizar uma prova de conceito explicitamente delimitada. Não chamar prova de conceito de tradução completa homologada.

## Tratamento de falhas
- Dependência ausente: registrar, instalar apenas as dependências declaradas e repetir.
- PDF inválido/protegido: registrar bloqueio; não tentar contornar DRM.
- Falha de extração: salvar traceback e página, reduzir para caso reproduzível autorizado.
- Saída incompleta/JSON inválido: marcar FALHOU e bloquear conclusão.
- Recurso planejado: marcar NÃO IMPLEMENTADO, nunca improvisar saída fictícia.
- Erro intermitente: registrar tentativas e condições; não suprimir evidências.

## Política de atualização
Toda ferramenta nova exige entrada em `docs/TOOL_REGISTRY.md`, fluxo em `docs/WORKFLOWS.md`, testes em `docs/TESTING.md` e estado em `docs/ROADMAP.md`, no mesmo conjunto de alterações.

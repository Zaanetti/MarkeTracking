# Plano de implementacao: consultas e dashboards v1

Status: planejado, ainda nao implementado.
Data: 2026-10-11.
Base inspecionada: branch `codex/nfce-v1-local-docker`, commit `244088c`.

Este documento e o contrato de trabalho para um modelo de codigo implementar a API de consultas
e os dados necessarios para dashboards. As escolhas abaixo fecham os detalhes da primeira versao.
Implementar as etapas completas, incluindo migrations, testes, OpenAPI e validacao no Docker local.
Nao construir o frontend nesta tarefa.

## 1. Objetivo e limites

Entregar consultas de compras, itens, estabelecimentos e execucoes, mais agregacoes para:

- Cards de gasto, quantidade de compras, ticket medio, descontos e cobertura dos dados.
- Evolucao de gastos por dia, semana e mes.
- Distribuicao dos gastos e frequencia por estabelecimento.
- Ranking de itens observados, recorrencia e quantidade por unidade comercial.
- Historico de precos e maiores variacoes de um mesmo item local.
- Comparacao de periodos e acompanhamento das falhas de processamento.

| Componente do dashboard | Endpoint que fornece os dados |
|---|---|
| Indicadores e comparacao com o periodo anterior | `/dashboards/overview` |
| Grafico de gastos ao longo do tempo | `/reports/spending?group_by=day` (ou week/month) |
| Ranking e participacao dos estabelecimentos | `/reports/spending?group_by=store` |
| Itens mais recorrentes, quantidade e valor bruto | `/dashboards/items` |
| Grafico detalhado de preco de um item local | `/reports/prices` |
| Maiores aumentos e quedas de preco observados | `/dashboards/price-changes` |
| Sucesso, falhas, duracao e andamento das importacoes | `/dashboards/processings` |
| Tabela de compras recentes e navegacao para detalhe | `/purchases` |

Todos os novos endpoints sao `GET` e consultam somente o PostgreSQL. Eles nao acessam o portal,
nao revalidam cancelamentos, nao decodificam imagens e nao criam Processings. O `POST` existente
continua sendo a entrada para processamento e reconsulta do documento fiscal.

Ficam fora: catalogo canonico de produtos, matching entre lojas, categorias inferidas, conversao
entre unidades/embalagens, dados do consumidor, meios de pagamento, metas de gasto, previsoes,
autenticacao/multitenancy novos, exportacoes, jobs de atualizacao e deploy online.

Preservar as decisoes existentes:

- [Contrato NFC-e](../specs/nfce-v1.md): Purchase e Fiscal Document sao 1:1.
- [Produtos canonicos adiados](../adr/0005-defer-canonical-product-matching.md).
- [Dados do consumidor ignorados](../adr/0004-ignore-consumer-data.md).
- [Dados de pagamento adiados](../adr/0006-defer-payment-data.md).
- [Dados de sucesso normalizados](../adr/0007-persist-only-normalized-success-data.md).
- [Tratamento de cancelamentos](../adr/0009-do-not-create-purchases-from-cancelled-documents.md).

Nao alterar os leitores QR, parsers, validacoes fiscais ou retencao de HTML para implementar GETs.

## 2. Estado atual e arquivos de referencia

O projeto usa Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL e Alembic.

| Arquivo | Papel na implementacao |
|---|---|
| `src/marketracking/api/router.py` | Registro das novas rotas sob `/api`. |
| `src/marketracking/api/routes_nfce.py` | POST existente; preservar contrato e adicionar IDs persistidos. |
| `src/marketracking/api/routes_processings.py` | GET de detalhe existente; acrescentar listagem. |
| `src/marketracking/schemas/nfce.py` | Contratos internos atuais e resposta do processamento. |
| `src/marketracking/db/models.py` | Modelos e novo vinculo opcional Processing -> Purchase. |
| `src/marketracking/services/persistence_service.py` | Projecao da compra persistida e conclusao de execucoes. |
| `src/marketracking/services/processing_service.py` | Fluxo atual; manter comportamento de criacao/idempotencia. |
| `alembic/versions/` | Nova migration; nao editar a migration inicial ja aplicada. |
| `tests/unit/`, `tests/integration/` | Contratos, calculos e consultas reais em PostgreSQL. |

Ler `AGENTS.md`, verificar o Git e conferir o codigo atual antes de executar o plano. Preservar
alteracoes locais de terceiros. A lista acima descreve a base inspecionada, nao autoriza sobrescrita.

## 3. Convencoes compartilhadas

### 3.1 Rotas, filtros e respostas

Base: `/api/v1`. Nomes de campos e parametros em `snake_case`; enums em ingles quando novos.
Preservar nomes/enums ja publicados, inclusive `processamento_id` e os valores de ProcessingState.

Listagens usam `page=1`, `page_size=25`, com limite de 100. Retornar:

```json
{
  "data": [],
  "pagination": {
    "page": 1,
    "page_size": 25,
    "total": 0,
    "total_pages": 0
  }
}
```

Pagina alem da ultima retorna `data=[]` com os totais corretos. `page<1` ou `page_size` fora do
intervalo 1..100 retorna 422. Ordenacao deve ter desempate por UUID; nao depender da ordem do banco.

Filtros combinam por AND. CNPJ e chave fiscal aceitam somente 14 e 44 digitos, respectivamente.
Validar chave com a regra existente. UUID invalido, enum desconhecido, intervalo invertido e valor
negativo retornam 422. Recurso inexistente retorna 404; listagem sem resultados retorna 200 vazia.
Preservar o envelope de erro de validacao do FastAPI e o estilo atual de `HTTPException`.
Indisponibilidade do banco retorna 503 com mensagem generica; nao expor SQL ou credenciais.

`q` busca trecho literal da descricao/nome, sem distinguir maiusculas, com tamanho 1..120 depois de
remover espacos nas extremidades. Escapar `%` e `_` para nao virarem curingas. Usar parametros SQL,
nao concatenar SQL recebido do cliente. A busca nao faz matching de produtos nem promete ignorar
acentos. Codigo local e descricao de identidade usam igualdade exata dos valores persistidos.

Moeda: `BRL`. Valores monetarios sao strings Decimal com duas casas; precos unitarios e quantidades
com seis; percentuais com duas, em escala 0..100 para participacoes/taxas. Variacoes podem ser
negativas ou maiores que 100. Usar Decimal, nunca float. Arredondar com `ROUND_HALF_UP` somente no
resultado final de uma agregacao. UUIDs e timestamps seguem os tipos Pydantic atuais.

### 3.2 Datas e periodos

`date_from` e `date_to` sao datas ISO `YYYY-MM-DD`, inclusivas, no fuso `America/Sao_Paulo`.
Compras/itens/relatorios financeiros filtram `FiscalDocument.issued_at`, nao a importacao.
Execucoes filtram `Processing.created_at`.

Converter os limites locais para um intervalo de timestamps `[inicio, dia_apos_fim)` e filtrar a
coluna sem aplicar cast de data no WHERE. Isto inclui todo o ultimo dia e ajuda o uso de indices.

- Listagens: datas opcionais; sem datas, todo o historico. Um limite isolado e permitido.
- Relatorios e dashboards: sem datas, os ultimos 30 dias locais incluindo hoje.
- Apenas `date_to`: `date_from=date_to-29 dias`.
- Apenas `date_from`: `date_to=hoje`; validar que o intervalo e possivel.
- Relatorios e dashboards aceitam no maximo 366 dias. Limite maior retorna 422.
- Retornar sempre os limites efetivos e `timezone` nos metadados de agregacoes.

Agrupamentos temporais: `day`, `week`, `month`. Semana ISO comeca na segunda-feira. Aplicar o fuso
local antes do agrupamento. Primeiro/ultimo bucket podem ser parciais; seus limites retornados
devem ser recortados ao intervalo solicitado. Buckets financeiros sem compras retornam zeros.
Buckets de preco sem observacao retornam preco `null`, quantidade de observacoes zero; nao usar
preco zero nem carregar o ultimo preco automaticamente.

### 3.3 Situacao fiscal e qualidade dos dados

Listagens de compras/itens aceitam `status=authorized|cancelled|all`, default `all`, para manter a
visibilidade historica. Detalhes sempre podem exibir canceladas. Relatorios, rankings, resumos de
lojas e dashboards financeiros incluem somente compras autorizadas, sem opcao de incluir
canceladas nesta versao. Comparacoes de precos tambem excluem canceladas. Considerar autorizada
somente a compra cujo status e o do FiscalDocument sejam ambos `authorized`.

Contar compras distintas, nunca execucoes. Reenvios que retornaram `existing` nao aumentam gastos.
Totais de compras devem ser agregados antes de JOINs com itens/steps/execucoes: uma compra com
varios itens ou varias execucoes nao pode multiplicar `payable_amount`.

Nao somar quantidades de unidades diferentes. Quando necessario, devolver `quantity_by_unit`.
`item_line_count` e numero de linhas da nota, nao numero de unidades compradas.
Tributos ausentes nao sao zero: reportar soma dos tributos conhecidos e cobertura separadamente.
Nenhum endpoint retorna URL completa, payload opaco, imagem, HTML, object key ou chave de
criptografia. Chave fiscal normalizada pode constar no detalhe da compra e no filtro de busca.

## 4. Identidade de itens e regras de preco

Nao criar tabela `Product` nem IDs globais de produto nesta tarefa. Um grupo de item observado e:

1. Com codigo: `(store_cnpj, store_product_code, commercial_unit)`.
2. Sem codigo: `(store_cnpj, description exata persistida, commercial_unit)`, somente linhas sem
   codigo. Nao juntar este grupo com linhas que tenham codigo, mesmo se a descricao for igual.

Expor `identity.kind=store_code|source_description` e os componentes da chave. Para grupos com
codigo, a descricao de exibicao e a da observacao mais recente do conjunto filtrado, com desempate
por compra e linha;
incluir `description_variants_count`. Isto nao indica que descricoes foram canonicalizadas.

O preco e o `unit_price` comercial declarado na nota. Desconto global da compra nao e rateado entre
itens. Valor de ranking de itens e `gross_line_amount=SUM(line_total)`, nao gasto liquido.

Para series e variacoes, formar uma observacao por compra e identidade de item. Se a mesma compra
tem varias linhas do grupo, calcular:

`observed_unit_price = SUM(quantity * unit_price) / SUM(quantity)`.

Guardar totais intermediarios em Decimal e serializar o preco com seis casas. Retornar quantidade,
numero de linhas, `purchase_id`, data da emissao e descricao da observacao. Ordenacao cronologica
e `(issued_at, purchase_id)`. Minimo/maximo referem-se a esses precos observados por compra.
Media ponderada do periodo usa as linhas originais, nao a media simples de medias por compra.

Variacao: `delta=current-previous`, `delta_percent=(current-previous)/previous*100`.
Divisao por zero retorna percentual `null`; nunca infinito. Nao comparar lojas distintas, UN com KG,
embalagens inferidas, textos parecidos ou codigos ausentes com codigos existentes.

## 5. Catalogo de endpoints de consulta

### GET `/purchases`

Filtros: `date_from`, `date_to`, `store_cnpj`, `access_key`, `status`, `min_total`, `max_total`.
Faixa de valor se refere a `payable_amount` e e inclusiva. Validar `min_total<=max_total`.
Sort: `issued_at_desc` (default), `issued_at_asc`, `payable_amount_desc`, `payable_amount_asc`.
Retornar um resumo por compra: `purchase_id`, `fiscal_document_id`, emissao, status, CNPJ,
nome historico `issued_store_name`, quantidade de linhas e totais. Nao carregar itens nessa lista.

### GET `/purchases/{purchase_id}`

Retornar IDs persistidos, documento fiscal completo normalizado, Store atual, snapshot historico
`issued_store_details`, itens em `line_number` crescente com `item_id`, e totais da compra.
Manter snapshot historico separado do nome/endereco atuais da Store.

### GET `/purchase-items`

Filtros: datas, `purchase_id`, `store_cnpj`, `store_product_code`, `q`, `commercial_unit`, `status`.
Sort: `issued_at_desc` (default), `issued_at_asc`, `unit_price_asc`, `unit_price_desc`.
Retornar `item_id`, `purchase_id`, emissao/status, CNPJ/nome historico da loja e campos completos
da linha. Ordenar/desempatar por emissao, purchase_id e line_number quando aplicavel.
Esta e a consulta "por produto" da v1; resultados textuais sao ocorrencias, nao um catalogo.

### GET `/stores`

Filtros: `q`, `cnpj`, datas opcionais. Retornar CNPJ, nome/endereco atuais, `details_as_of` e resumo
de compras autorizadas no periodo: `purchase_count`, `payable_amount`, `last_purchase_at`.
Sem datas, resumo de todo o historico. Incluir lojas com resumo zero. Sort: `name_asc` (default),
`payable_amount_desc`, `purchase_count_desc`; desempate por CNPJ. Paginacao padrao.

### GET `/stores/{cnpj}`

Detalhes atuais e o mesmo resumo autorizado; aceitar datas opcionais. CNPJ conhecido sem compras
no periodo retorna 200 com resumo zero. Navegar para compras usando `/purchases?store_cnpj=...`.
Nao criar outra rota redundante de listagem de compras da loja.

### GET `/processings`

Filtros: datas da execucao, `purchase_id`, `state`, `result`, `error_code`, `capture_method`.
`result` filtra o campo ORM `outcome` usando os enums atuais. `error_code` usa ErrorCode existente.
Sort: `created_at_desc` (default), `created_at_asc`. Resumo seguro com `processamento_id`,
`purchase_id` opcional, estado, resultado, datas, duracao e erro resumido. Nao carregar steps.

### GET `/processings/{processamento_id}` (existente)

Preservar contrato e steps. Acrescentar apenas `purchase_id` opcional. Usar o detalhe atual para
investigar uma execucao selecionada no dashboard. Exibir vinculos desconhecidos como `null`.

## 6. Relatorios que alimentam graficos

### GET `/reports/spending`

Filtros: periodo financeiro, `store_cnpj`; `group_by=day|week|month|store`, default `day`.
Para `store`, `limit=10` default, maximo 50. Nao aplicar paginacao de lista a buckets temporais.

`summary`: `purchase_count`, `gross_items_amount`, `total_discount`, `payable_amount`,
`average_ticket`, `item_line_count`, `store_count`, `known_taxes_amount`,
`purchases_with_tax_info`. Ticket medio = soma de valor a pagar / quantidade de compras;
sem compras, valor `"0.00"`. Tributos conhecidos = `null` se nenhuma compra tem esse dado.

Grupos temporais: `bucket_start`, `bucket_end`, `purchase_count`, `gross_items_amount`,
`total_discount`, `payable_amount`, `average_ticket`. Preencher buckets sem movimento com zeros.
As somas de purchase_count, bruto, desconto e valor a pagar reconciliam os respectivos campos do
summary. Medias e contagens de lojas distintas nao sao somadas entre buckets.

Grupos por loja: `store_cnpj`, `store_name` atual, `last_purchase_at`, `purchase_count`,
`payable_amount`, `share_percent` sobre o total filtrado. Ordenar por valor a pagar desc/CNPJ asc.
Retornar `others` com `store_count`, `purchase_count`, `payable_amount`, `share_percent` agregados
de todas as lojas alem do limite. Sem lojas excedentes, others tem contagens/valor zero. Se o total e zero,
participacoes sao `null`; nao dividir por zero. Usar valores monetarios, e nao percentuais
arredondados, para a renderizacao de graficos de participacao.

Este endpoint alimenta grafico de gastos e ranking/distribuicao por loja. Nao criar endpoints
duplicados `/dashboards/spending-series` ou `/dashboards/stores` com a mesma consulta.

### GET `/reports/prices`

Obrigatorios: `store_cnpj`, `commercial_unit` e exatamente um de `store_product_code` ou
`description`. A descricao e exata e identifica somente itens sem codigo. Aceitar periodo.
`bucket=purchase|day|week|month`, default `purchase`.

Retornar `identity`, `summary` (`observation_count`, `quantity`, `minimum_unit_price`,
`maximum_unit_price`, `weighted_average_unit_price`, `last_unit_price`, `last_issued_at`,
`delta`, `delta_percent`) e serie cronologica. Variacao do summary compara as duas ultimas compras
DO PERIODO; com menos de duas observacoes, delta e delta_percent sao null.
Resumo e calculado sobre todo o filtro, antes da paginacao. Sem dados, `data=[]`, contagem/quantidade
zero e precos/datas/variacoes `null`.

`purchase`: uma observacao por compra, com `purchase_id`, `issued_at`, `description`,
`observed_unit_price`, `quantity`, `item_line_count`, paginacao padrao e ordem crescente.
Buckets temporais: `bucket_start`, `bucket_end`, `observed_unit_price` ponderado por quantidade
das linhas no bucket, `quantity` e `observation_count` de compras;
nao paginar, preencher ausencias com preco `null`. Quantidade e sempre da unidade solicitada.
Este endpoint alimenta o grafico detalhado de preco de um item local.

## 7. Endpoints especificos para dashboards

### GET `/dashboards/overview`

Filtros: periodo financeiro, `store_cnpj`.
Retornar um pacote de indicadores do periodo atual e do periodo anterior imediatamente adjacente
com igual numero de dias locais. Aplicar os mesmos filtros aos dois periodos.

KPIs: `purchase_count`, `payable_amount`, `gross_items_amount`, `total_discount`, `average_ticket`,
`item_line_count`, `store_count`, `known_taxes_amount`, `purchases_with_tax_info`,
`tax_info_coverage_percent`, `items_with_local_code_count`, `local_code_coverage_percent`.
Cobertura de codigo = linhas com codigo / linhas totais; cobertura de tributos = compras com esse
dado / compras totais. Percentuais de cobertura sao `null` quando o denominador e zero.

Comparacao para `purchase_count`, `payable_amount`, `average_ticket`: os valores estao em
`current` e `previous`; `comparison` contem `delta` e `delta_percent` por metrica.
Quando previous=0, delta_percent e `null`, inclusive se current=0. Delta de contagem e inteiro;
delta monetario tem duas casas.
O intervalo anterior nao faz parte dos totais do intervalo atual.

Exemplo parcial do envelope (demais KPIs devem ser incluidos na implementacao):

```json
{
  "meta": {
    "date_from": "2026-10-01",
    "date_to": "2026-10-10",
    "previous_date_from": "2026-09-21",
    "previous_date_to": "2026-09-30",
    "timezone": "America/Sao_Paulo",
    "currency": "BRL"
  },
  "current": {"purchase_count": 3, "payable_amount": "90.00"},
  "previous": {"purchase_count": 2, "payable_amount": "60.00"},
  "comparison": {
    "payable_amount": {"delta": "30.00", "delta_percent": "50.00"}
  }
}
```

### GET `/dashboards/items`

Filtros: periodo financeiro, `store_cnpj`, `q`, `commercial_unit`.
`metric=gross_line_amount|purchase_count|quantity`, default `gross_line_amount`.
`limit=10` default, maximo 50. Para ordenar por quantity, commercial_unit e obrigatoria (422).

Ranking por identidade da secao 4. Campos: `identity`, `description`, `description_variants_count`,
`item_line_count`, `purchase_count`, `quantity`, `commercial_unit`, `gross_line_amount`,
`first_issued_at`, `last_issued_at`, `last_unit_price` observado.
Resumo de TODO o filtro, antes do limite: `group_count`, `item_line_count`, `gross_line_amount`,
`quantity_by_unit`. Desempatar pela identidade.
O frontend deve apresentar os valores como bruto de itens; nunca como gasto liquido por produto.

### GET `/dashboards/price-changes`

Filtros: periodo financeiro, `store_cnpj`, `commercial_unit`, `q` da descricao de exibicao.
`direction=increase|decrease|all`, default `all`; `limit=10`, maximo 50.

Para cada identidade local, pegar a observacao mais recente DENTRO do periodo e a compra anterior
autorizada da mesma identidade em todo o historico, que pode estar antes de date_from. Aplicar
filtros de loja/unidade a ambas. Comparar compras distintas usando a media ponderada da secao 4.
Retornar `identity`, `description`, `current` e `previous` (cada um com `purchase_id`, `issued_at`,
`observed_unit_price`), `delta`, `delta_percent`.

Excluir grupos sem compra anterior, sem variacao ou com preco anterior zero do ranking percentual;
Retornar no summary `no_previous_count`, `unchanged_count`, `zero_baseline_count` e
`changed_group_count` antes do limite. `all` ordena por valor absoluto da
variacao percentual desc; `increase` por percentual desc; `decrease` por percentual asc. Usar a
identidade como desempate. Nao confundir variacao observada com inflacao ou oferta entre lojas.

### GET `/dashboards/processings`

Filtros: periodo de CRIACAO da execucao, `capture_method`; `bucket=day|week|month`, default `day`.
Nao aceitar filtros financeiros/por loja: falhas antes da leitura nao identificam estabelecimento.

Retornar `summary` com `processing_count`, `successful_count`, `failed_count`, `rejected_count`,
`in_progress_count`, `existing_return_count`, `success_rate_percent`, `duration_p50_ms`,
`duration_p95_ms`; `outcomes` com result/count (result pode ser null para andamento);
`error_distribution` com error_code/count de falhas/rejeicoes; `data` com buckets temporais,
contagens por situacao e success_rate_percent. Bucket usa bucket_start/bucket_end.

- Sucesso: outcome `created`, `existing` ou `cancelled_existing` com state `CONCLUIDO`.
- Falha/rejeicao: usar estados `FALHA` e `REJEITADO` existentes; nao inferir pelo HTTP da rota.
- Em andamento: estados nao terminais.
- Denominador da taxa de sucesso: somente execucoes terminais (sucesso+falha+rejeicao).
- Retornos existentes: outcomes `existing` e `cancelled_existing`; nao sao novas compras.
- Sem execucoes terminais, taxa e percentis sao `null`.
- Calcular p50/p95 por interpolacao linear (`percentile_cont` do PostgreSQL), somente duracoes nao
  nulas de execucoes terminais. Fazer cast do resultado SQL para Numeric e serializar
  milissegundos com duas casas.
- Na serie, buckets vazios tem contagens zero e taxa de sucesso `null`.

## 8. Mudancas de persistencia e compatibilidade

### IDs das respostas

Hoje PurchaseData e tambem usada ANTES da persistencia, pelo normalizador. Nao exigir IDs nesse
momento nem gerar UUIDs novos para respostas de compras existentes.

Adicionar `purchase_id`, `fiscal_document_id` e `item_id` aos respectivos dados persistidos.
Uma solucao conservadora e campos opcionais nos contratos internos atuais, preenchidos em
`persistence_service._purchase_data`, com schemas de leitura que exigem IDs nos novos GETs.
No POST, qualquer Purchase retornada deve ter os IDs reais do banco, inclusive `existing`.
Preservar todos os campos atuais; a mudanca do POST e somente aditiva.

### Vinculo entre execucao e compra

Processing atualmente nao tem FK para Purchase. Adicionar `purchase_id` nullable, indexada,
FK para `purchases.id` com `ON DELETE SET NULL`, preservando o historico.
Preencher na mesma transacao de conclusao para `created`, `existing` e `cancelled_existing`.
Falhas/rejeicoes sem compra associavel permanecem `null`. Nao usar hash de imagem para adivinhar
a compra nem usar tempo de execucao como criterio de associacao.

Backfill: associar somente rows historicas com outcome de sucesso e input_access_key que
corresponda exatamente a uma Purchase via FiscalDocument. Muitos processamentos antigos por
imagem nao guardam essa chave; seu vinculo deve continuar `null`. Documentar esta limitacao no
OpenAPI da listagem por compra e na entrega. Nao alterar a politica de dados da imagem.

### Indices e query plans

Criar nova migration Alembic, com upgrade e downgrade. Candidatos iniciais:

- `fiscal_documents(issued_at, id)` para periodo/ordenacao.
- `processing_records(created_at, id)` e indice de `purchase_id`.
- `purchase_items(store_product_code, commercial_unit, purchase_id)` se o plano de busca o usar.

Reusar indices/constraints atuais e conferir redundancia. Validar com `EXPLAIN` em fixtures maiores.
Nao instalar pg_trgm/unaccent, materialized views, Redis ou cache externo nesta versao. Busca
textual pode fazer scan na base pequena; deixar otimizacao por volume registrada como evolucao.

## 9. Organizacao da implementacao

Manter routers finos, schemas Pydantic de entrada/saida e consultas/calculos em services.
Sugestao de arquivos, ajustavel somente para acompanhar melhor o padrao encontrado no repo:

- `api/routes_purchases.py`, `routes_purchase_items.py`, `routes_stores.py`.
- Ampliar `routes_processings.py`; criar `routes_reports.py` e `routes_dashboards.py`.
- `schemas/queries.py`, `schemas/reports.py`, `schemas/dashboards.py`.
- `services/query_service.py`, `services/reporting_service.py`.

Compartilhar filtros, identidade local, intervalos e agregacoes entre relatorios/dashboard.
Nao implementar a mesma soma em varios routers. Usar SQLAlchemy com agregacoes no banco e
subqueries/CTEs adequadas; nao carregar todo o historico em Python para depois somar.
`selectinload` pode ser usado no detalhe; listagens devem selecionar so os campos necessarios.
Evitar N+1 em lojas, compras e rankings. Ordenacao recebida pelo cliente usa allowlist.

Agregacoes com varios blocos devem usar o mesmo conjunto filtrado e uma transacao read-only
`REPEATABLE READ` no PostgreSQL quando houver multiplos SELECTs; alternativamente uma unica
consulta com CTEs. Nao mudar globalmente o isolamento da sessao usada pelo POST.

OpenAPI deve documentar defaults, exemplos, limites, nulos, unidade comercial e semantica de
cancelamentos/precos brutos. Respostas de agregacao sempre informam os filtros efetivos.

## 10. Etapas de execucao

1. Criar schemas/filtros compartilhados e migration do vinculo/indices. Acrescentar IDs ao POST
   e cobrir criacao, retorno existente e cancelamento posterior.
2. Implementar compras, itens, lojas e listagem de execucoes. Preservar GET de execucao existente.
3. Implementar agregacao de gastos e identidade/precos locais com testes de calculo.
4. Implementar overview, ranking de itens, variacoes de preco e dashboard de execucoes reutilizando
   as consultas anteriores. Padronizar envelopes e documentar OpenAPI.
5. Validar migrations e queries em PostgreSQL descartavel, executar checks e smoke tests locais.
6. Atualizar documentacao com endpoints implementados, testes e qualquer limitacao objetiva.

Concluir uma etapa com seus testes antes de avancar. Nao deixar placeholders, numeros mockados,
TODOs de contrato ou endpoints que retornem dados fixos. Nao implementar frontend nesta tarefa.

## 11. Testes e criterios de aceite

Usar dados sinteticos. As fotos e chaves reais de anexos nao entram em fixtures versionadas.
Testes de calculo devem ter valores esperados explicitos, nao reexecutar a formula de producao.

Cobertura necessaria:

- IDs reais e estaveis no POST/GET, idempotencia sem nova Purchase e vinculo correto da execucao.
- Migration preservando dados; backfill seguro; imagem historica sem chave permanece sem vinculo.
- Limites de datas incluindo ultimo dia local, conversao de fuso, semana ISO e periodos parciais.
- Filtros combinados, ordenacao com empate, paginacao vazia/fora do alcance e 404/422.
- CNPJ/chave/UUID invalidos, intervalo invertido, total negativo e escapes de busca textual.
- Compra com varias linhas e varias execucoes nao multiplica seu gasto/quantidade de compras.
- Cancelamento posterior continua consultavel e sai de gastos, precos e comparacoes.
- Codigo identico em duas lojas nao combina; unidades diferentes nao combinam.
- Sem codigo: identidade por descricao exata, sem merge com linhas codificadas.
- Quantidade com decimais, precos de seis casas, descontos globais sem rateio por item.
- Varias linhas do mesmo codigo na mesma compra viram uma observacao ponderada.
- Comparacao contra periodo anterior com mesmo numero de dias; baseline zero -> percentual null.
- Buckets financeiros vazios com zero; preco ausente com null; tributos desconhecidos preservados.
- Ranking de lojas com `others` reconciliando o total; ranking de quantidade exige unidade.
- Variacao de preco com compra anterior fora do periodo e exclusao de canceladas/baseline zero.
- Dashboard de execucoes com reenvios, falhas, rejeicoes e andamento; denominator/percentis corretos.
- Respostas sem URLs completas, HTML, imagens, dados de consumidor ou segredos.

As queries/agrupamentos de PostgreSQL precisam de testes de integracao em PostgreSQL real.
SQLite pode apoiar testes de contrato, mas nao comprova timezone, Numeric e percentile_cont.
Preparar fixture/servico PostgreSQL descartavel para esses testes; nunca apagar o banco local do
usuario para obter isolamento. MinIO e portal devem ser mockados quando nao forem relevantes.

Checks: `pytest`, `ruff check src tests`, migration upgrade/downgrade/upgrade em banco descartavel,
OpenAPI e verificacao de consistencia entre summaries e grupos. Registrar testes de integracao
ignorados com motivo; nao declarar validacao PostgreSQL concluida se so SQLite foi executado.

## 12. Docker local e entrega

O stack ativo do projeto usa `docker-compose.local.yml` e `.env.local`, app em
`http://127.0.0.1:8001`. Usar explicitamente essa configuracao, sem confundir com o compose legado.
Nao mostrar valores de `.env.local`, nao versionar segredos nem anexos.

Depois dos testes/migrations descartaveis:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local build app
docker compose -f docker-compose.local.yml --env-file .env.local up -d --no-deps app
```

Executar o segundo comando apenas se o build concluir. O comando de startup do app aplica
`alembic upgrade head`; conferir os logs e readiness. Preservar volumes, os containers do legado
e todas as compras/execucoes existentes. Nao executar `down -v`, truncate, reset ou exclusao de
dados. Se houver migracao com lock significativo, resolver/testar o risco antes de aplica-la.

Smoke tests: readiness, listagem de compras, detalhe de um ID existente, busca de itens, uma
agregacao de gastos e um dashboard. Os GETs nao devem alterar contagens de Purchase/Processing.
Comparar uma soma manual do fixture com overview e spending; verificar respostas sem dados.

Versionar somente arquivos desta tarefa, seguindo autorizacao e estado atual do Git. A entrega
deve informar endpoints, commit/branch quando houver, migrations aplicadas, verificacoes,
limitacoes de historico e URL local de `/docs`.

## 13. Prompt de handoff

> Leia AGENTS.md e docs/plans/query-api-and-dashboards-v1.md. Implemente todas as etapas do plano
> de consultas e dashboards no MarkeTracking, preservando as decisoes do dominio e o fluxo NFC-e
> existente. Execute testes de contratos/calculos e integracoes em PostgreSQL descartavel,
> documente o OpenAPI e valide a API no Docker local com docker-compose.local.yml e .env.local.
> Preserve dados, segredos e alteracoes de terceiros. Informe os resultados e qualquer bloqueio
> real, sem substituir a implementacao por placeholders.

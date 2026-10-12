# Query and dashboard API v1

The query API reads persisted PostgreSQL data only. It does not consult SEFAZ, create processing
records, decode images, or change fiscal status. The interactive OpenAPI document is available at
`/docs` when the app is running.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/purchases` | Paged purchase summaries; filters issue dates, store CNPJ, validated access key, status, and inclusive payable amount. |
| `GET /api/v1/purchases/{purchase_id}` | Normalized fiscal document, current Store, historical issued-store snapshot, lines, and totals. |
| `GET /api/v1/purchase-items` | Paged source line occurrences, with literal description search. |
| `GET /api/v1/stores` | Current Store records and authorized-purchase summaries, including zero-activity stores. |
| `GET /api/v1/stores/{cnpj}` | One current Store and its authorized-purchase summary. |
| `GET /api/v1/processings` | Paged safe execution summaries; steps are not loaded. |
| `GET /api/v1/processings/{processamento_id}` | Existing execution detail and steps, now with optional `purchase_id`. |
| `GET /api/v1/reports/spending` | Authorized spending by local day, ISO week, month, or store. |
| `GET /api/v1/reports/prices` | Quantity-weighted price observations for one exact store-local item identity. |
| `GET /api/v1/dashboards/overview` | Current and adjacent equal-length previous-period purchase KPIs. |
| `GET /api/v1/dashboards/items` | Ranked local item identities, gross line values, recurrence, and quantities by unit. |
| `GET /api/v1/dashboards/price-changes` | Largest observed same-identity price increases or decreases. |
| `GET /api/v1/dashboards/processings` | Execution outcomes, errors, durations, and time buckets. |

## Shared query rules

List endpoints use `page=1`, `page_size=25`, and a maximum page size of 100. A page after the final
page is empty and retains the result totals. Invalid page values, UUIDs, enums, CNPJs, access keys,
negative totals, and inverted date ranges return 422. Missing resources return 404. Database
unavailability returns a generic 503 response.

List date filters are optional, inclusive Sao Paulo local dates, and may be used independently.
Financial reports and dashboards default to the latest 30 local dates including today; an isolated
`date_from` ends today and an isolated `date_to` begins 29 days earlier. These endpoints accept at
most 366 inclusive dates. Queries compare timestamp columns against local-midnight bounds, including
the full final date. Aggregation metadata returns effective dates, timezone, currency, and effective
filters.

List status defaults to `all`, so retained cancelled purchases and item lines remain visible.
Financial summaries, item rankings, price histories, and price comparisons require both Purchase
and Fiscal Document to be `authorized`. A purchase is counted once even when it has multiple lines
or processing attempts. Monetary results are BRL strings with two decimal places; commercial
quantities and unit prices use six. Percentages use a 0–100 scale. Missing tax amounts remain null,
with coverage reported separately.

Text `q` values are trimmed, case-insensitive literal substrings of 1–120 characters. `%` and `_`
are escaped. The search does not promise accent-insensitive matching.

## Item identity and prices

An item group is local to one store and commercial unit. A non-null store product code identifies
`(store_cnpj, store_product_code, commercial_unit)`. A line without a code uses exact persisted
description and unit and never merges with coded lines. No canonical catalog, inferred packaging,
cross-store matching, or unit conversion is provided.

Price series use the commercial `unit_price` from source lines. When a purchase contains multiple
lines for one identity, its observation is weighted by source quantity. Item rankings use gross
`line_total`; purchase-wide discounts are not apportioned to item lines. Empty financial buckets
contain zeros; empty price buckets contain a null price and zero observations.

Historical image executions that predate `purchase_id` and do not contain a persisted access key
remain unlinked (`purchase_id=null`). The migration backfills only successful executions whose
stored access key exactly matches a persisted Fiscal Document. New successful runs link to their
Purchase in the same transaction as completion.

## Validation

The schema change is Alembic revision `20261011_0002`. The disposable integration workflow in
`scripts/test-integration.ps1` applies upgrade, downgrade and re-upgrade; it runs synthetic tests on
PostgreSQL 16 and MinIO, including PostgreSQL timezone and numeric behavior, `percentile_cont`,
empty and partial buckets, and an `EXPLAIN (ANALYZE, BUFFERS)` range query. It never uses the local
Compose volumes. Run it with Docker Desktop available:

```powershell
.\scripts\test-integration.ps1
```

The normal checks are `python -m pytest -q` and `python -m ruff check src tests`. Integration tests
are marked and skip in a normal pytest run unless the disposable-service workflow enables them.

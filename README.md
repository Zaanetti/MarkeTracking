# MarkeTracking

MarkeTracking converts public Sao Paulo NFC-e documents into normalized Purchases for price
tracking. The current backend is synchronous and accepts either a consultation URL or an image
containing exactly one QR Code.

## Local Setup

1. Copy `.env.example` to `.env` and replace the local credentials before using a shared or
   production environment.
2. Generate a Fernet key for temporary failed-HTML diagnostics:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Put the output in `FAILED_HTML_ENCRYPTION_KEY`. Keep the key outside source control and preserve
   it across deployments so retained artifacts remain decryptable.
3. Start the app, PostgreSQL, and MinIO:

   ```powershell
   docker compose up --build
   ```
4. Apply the initial schema:

   ```powershell
   docker compose run --rm app alembic upgrade head
   ```

The API is available at `http://localhost:8000`; interactive API documentation is at
`http://localhost:8000/docs`.

## API

- `GET /api/health` reports application liveness; `GET /api/ready` verifies database readiness.
- `POST /api/v1/nfce/process` accepts `multipart/form-data` with exactly one `url` field or one
  `image` file field.
- `GET /api/v1/processings/{processamento_id}` returns safe execution metadata and stage history.
- Query endpoints provide purchases, purchase lines, stores, processing summaries, spending and
  price reports, plus overview, item, price-change and processing dashboards. Their filters,
  identity rules, date semantics and response fields are documented in
  [Query API v1](docs/api-query-v1.md).

The query endpoints are read-only and use stored PostgreSQL data. Financial aggregates and price
comparisons exclude cancelled records; purchase and line listings default to including them for
historical traceability. OpenAPI documents are served at `/docs`.

Successful first processing returns `201`; an existing document returns `200`. Invalid or
inconsistent input returns `422`; a missing document returns `404`; a newly observed cancellation
returns `409`; an incompatible portal response returns `502`; portal blocking, exhausted
transient retries, or persistence unavailability returns `503`.

Only Sao Paulo consultation URLs on `www.nfce.fazenda.sp.gov.br` are accepted. Access keys are
validated and compared with the key shown by the portal. Purchase and Fiscal Document have a
one-to-one relationship enforced by a unique database constraint, so repeated processing returns
the existing Purchase after reconsulting the portal.

## Data Handling

- Original images are processed in memory and are never persisted.
- URL query strings and opaque QR payloads are never stored or returned.
- Consumer identity fields are ignored and are absent from the database model.
- Successful records contain normalized fiscal and purchase data only.
- HTML from failed post-consultation processing is sanitized, encrypted with
  `FAILED_HTML_ENCRYPTION_KEY`, stored in MinIO under a 30-day lifecycle rule, and removed earlier
  when a later processing of that Access Key succeeds.
- Processing history is retained without the original image, full URL, query string, or QR payload.

## Development

Install the package and development tools with:

```powershell
python -m pip install -e ".[dev]"
```

Run deterministic tests and lint checks:

```powershell
python -m pytest
python -m ruff check src tests
```

The tests use sanitized synthetic portal HTML and do not call the live SEFAZ service. Any live
smoke test must be run deliberately, against an authorized public consultation, and without
persisting the consulted document.

Run the complete suite, including PostgreSQL and MinIO integration tests, with disposable local
containers (Docker Desktop required):

```powershell
.\scripts\test-integration.ps1
```

The script applies the migration to a temporary PostgreSQL instance, checks concurrent idempotency
and encrypted artifact retention against MinIO, and removes both containers afterward. It does not
use the Compose data volumes or modify `.env`.

In `APP_ENV=production`, startup rejects debug mode, missing/invalid failed-HTML encryption keys,
the scaffold's default PostgreSQL credentials, and MinIO's default credentials.

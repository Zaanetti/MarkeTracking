# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.12 FastAPI project packaged from `src/`. Application code lives in `src/marketracking/`: `main.py` creates the app, `api/` holds API routers, `web/` holds server-rendered page routes, `schemas/` contains Pydantic contracts, `db/` contains SQLAlchemy models and sessions, `services/` contains business logic, `parsers/` is reserved for source-specific receipt parsers, and `workers/` is for background processing. Jinja templates are in `src/marketracking/templates/`; CSS and other static assets are in `src/marketracking/static/`. Database migrations live in `alembic/versions/`. Tests are under `tests/`, currently organized as `tests/unit/`.

## Build, Test, and Development Commands

- `docker compose up --build`: builds and starts the app, PostgreSQL, and MinIO.
- `docker compose run --rm app alembic upgrade head`: applies database migrations.
- `python -m pip install -e ".[dev]"`: installs the package and development tools locally.
- `pytest`: runs the test suite configured in `pyproject.toml`.
- `ruff check src tests`: runs lint checks with the repository Ruff settings.
- `uvicorn marketracking.main:app --reload`: runs the app locally when dependencies and environment variables are already configured.

Copy `.env.example` to `.env` before running the Docker stack or local app.

## Coding Style & Naming Conventions

Use Python 3.12 syntax, 4-space indentation, type hints for public functions, and clear module boundaries matching the existing package layout. Ruff is configured for a 100-character line length and `py312` target. Use `snake_case` for modules, functions, variables, and test names; use `PascalCase` for Pydantic and SQLAlchemy model classes. Keep routes thin and place reusable business logic in `services/`.

## Testing Guidelines

Pytest is the test framework. Name test files `test_*.py` and test functions `test_*`. Put focused unit tests in `tests/unit/`; add integration-style tests in a separate directory if they require PostgreSQL, MinIO, or network access. Mock external NFC-e, QR, storage, and parser dependencies where possible so unit tests stay deterministic.

## Commit & Pull Request Guidelines

Existing commit history is informal and short, so prefer concise imperative messages going forward, such as `Add receipt parser service` or `Fix health route response`. Pull requests should include a brief summary, tests run, migration notes when Alembic changes are included, and screenshots for template or CSS changes. Link related issues when available and call out any `.env.example` updates.

## Security & Configuration Tips

Do not commit real secrets in `.env`. Keep shared defaults in `.env.example`. Treat MinIO credentials, database passwords, and external service URLs as environment-specific configuration loaded through `marketracking.core.config`.

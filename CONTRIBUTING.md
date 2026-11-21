# Contributing

Thanks for taking an interest in Table Review. This is a local-first financial PDF-to-Excel workbench, and the project welcomes focused issues and pull requests.

## Before you start

- For a substantial change, open an issue first so the approach can be discussed.
- For a security vulnerability, do **not** open a public issue. Use the repository's **Security** tab and GitHub's private vulnerability reporting.
- Keep pull requests focused. Unrelated refactors make review slower and riskier.

## Development setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/), Docker (for PostgreSQL), and Node 24+ if you are changing the frontend.

```powershell
uv sync --frozen --group dev
cd web && npm ci && cd ..
```

Start the local PostgreSQL container, migrations, warm extraction worker, and hot-reload frontend in one command (uses the instant `native_fast` backend for digital PDFs without requiring the 2 GiB Docling weights):

```powershell
# Windows PowerShell
.\scripts\start-local.ps1 --dev-ui

# macOS / Linux
./scripts/start-local.sh --dev-ui
```

To also run Docling/OCR extraction or tests requiring the optional ML stack, sync the `extraction` extra and pass `--with-docling`:

```powershell
uv sync --frozen --extra extraction --group dev
.\scripts\start-local.ps1 --with-docling --dev-ui
```

The hardened Docker Compose stack (PostgreSQL, API, static UI, and network-isolated worker) is documented in [docs/local-deployment.md](docs/local-deployment.md).

## Checks

Run these before opening a pull request:

```powershell
uv run --frozen --extra extraction --group dev ruff check src evaluation tests scripts
uv run --frozen --group dev pytest tests/domain tests/integration -q
uv run --frozen --group dev pytest tests/acceptance -m "not packaged" -q
```

Frontend changes should also pass:

```powershell
cd web
npm run typecheck
npm run build
npm run format:check
npm run test:e2e
```

If you changed FastAPI routes or response schemas, regenerate the checked-in OpenAPI contract and TypeScript types:

```powershell
uv run --frozen python scripts/generate_api_schema.py
cd web && npm run generate:types
```

Integration tests need PostgreSQL. Extraction tests need the preloaded models described in the local deployment guide. If a test cannot run in your environment, say so in the pull request rather than skipping it silently.

## Code style

- Python is formatted and linted with Ruff; the configuration is in `pyproject.toml`.
- TypeScript and CSS are checked with `tsc --noEmit` and Prettier (`npm run format:check`).
- Keep comments and documentation factual. Do not add claims about accuracy, adoption or time savings that are not backed by recorded results.
- Preserve the privacy and security boundaries: loopback-only one-time local onboarding (disabled in `hosted` mode and once any account exists), no report data in logs, no secrets or `.cache/` local deployment files committed.

## Pull requests

A useful pull request describes:

1. The problem or user need.
2. The change and any trade-offs.
3. How it was tested, including commands run.
4. Any migration, configuration or documentation impact.

By contributing, you agree that your contribution is licensed under the project's MIT license and that you will follow the [Code of Conduct](CODE_OF_CONDUCT.md).

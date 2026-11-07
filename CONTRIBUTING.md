# Contributing

Thanks for taking an interest in Table Review. This is a local-first tool for reviewing annual-report tables, and the project welcomes focused issues and pull requests.

## Before you start

- For a substantial change, open an issue first so the approach can be discussed.
- For a security vulnerability, do **not** open a public issue. Use the repository's **Security** tab and GitHub's private vulnerability reporting.
- Keep pull requests focused. Unrelated refactors make review slower and riskier.

## Development setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/), Docker with at least 8 GiB of available RAM, and Node 24 if you are changing the frontend.

```powershell
uv sync --frozen --extra extraction --group dev
```

The full local stack, including PostgreSQL and the isolated extraction worker, is described in [docs/local-deployment.md](docs/local-deployment.md).

For frontend work:

```powershell
cd web
npm ci
npm run dev
```

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
npm run build
npm run test:e2e
```

Integration tests need PostgreSQL. Extraction tests need the preloaded models described in the local deployment guide. If a test cannot run in your environment, say so in the pull request rather than skipping it silently.

## Code style

- Python is formatted and linted with Ruff; the configuration is in `pyproject.toml`.
- TypeScript is checked by the web build.
- Keep comments and documentation factual. Do not add claims about accuracy, adoption or time savings that are not backed by recorded results.
- Preserve the privacy boundaries: no public signup route, no report data in logs, no secrets or local deployment files committed.

## Pull requests

A useful pull request describes:

1. The problem or user need.
2. The change and any trade-offs.
3. How it was tested, including commands run.
4. Any migration, configuration or documentation impact.

By contributing, you agree that your contribution is licensed under the project's MIT license and that you will follow the [Code of Conduct](CODE_OF_CONDUCT.md).

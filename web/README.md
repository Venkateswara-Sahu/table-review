# Browser workspace

React UI with API-generated types. It uses the same owned document, task,
revision and review services tested by the backend. No external service is
provisioned by these commands.

From the repository root, install the locked Python environment and obtain the
local extraction models using the baseline setup instructions. Start the local
PostgreSQL service and configure its test URL via
`TABLE_REVIEW_TEST_DATABASE_URL`, or the ignored `.cache/test-db.json` used by
the development setup.

From `web`, install and verify:

```powershell
npm ci
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '../.cache/ms-playwright'
npx playwright install chromium
npm run build
npm run test:e2e -- source.spec.ts
```

Playwright starts disposable real API/database fixtures and a Vite server on
loopback ports 8765 and 5173. Its fixture creates two test accounts and uses
real offline CPU extraction for source tables. Tests perform a further upload
and extraction through the UI. A project-scoped teardown stops those test
servers and drops only their recorded random test schema. This is a test
environment, not a persistent user workspace.

Generate schemas after API changes, from the repository root:

```powershell
.venv/Scripts/python.exe scripts/generate_api_schema.py
```

Then from `web` run `npm run generate:types`. The committed generated file is
not a separately maintained browser domain model. Use `npm run format:check`
and `npm run build` for formatting and TypeScript verification.

Private requests use credentials and CSRF verification. Report/image responses
are not cached; image object URLs and pending requests are cleared when the
account changes. Source coordinates remain points on the original displayed,
cropped and rotated page. A table/page reference visibly says the exact cell
location is unavailable.

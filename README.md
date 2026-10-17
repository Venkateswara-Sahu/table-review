# Table Review

A private application for extracting English annual-report tables from digital PDFs and page images,
comparing them with the original page, correcting a saved revision and exporting
that exact revision to a spreadsheet. It runs locally on CPU with PostgreSQL,
FastAPI, React and a separately bounded Docling worker.

The [17 October private release record](docs/evidence/2026-10-17-private-product-release.md)
documents the current build, recovery tests and extraction limits. Open the local
deployment at <http://127.0.0.1:8000> after following the local runbook.

The workflow includes original-page highlights and zoom, region selection,
explicit manual grids, keyboard editing, immutable edit history, checked-cell
audit events, source discrepancy decisions, approval and workbook/CSV downloads.
Exports retain raw text, current text, source references, revision checksum and
review status. Warnings remain visible; marking cells checked is a user action,
not an automatic correctness guarantee.

Use the **Context** review tab to inspect a cell's canonical header path and
source-backed period, currency, scale and measure proposals. Confirmations are
explicit reviewer actions, carry actor/time and source evidence into XLSX/CSV,
and reopen after dependent edits. Confirming scale does not multiply values.
See the [source-context checkpoint](docs/evidence/2026-10-15-source-context.md).

Use **History** for an ordered cell/table timeline explaining saved changes and
stale review evidence. Workbook `Timeline` and CSV `timeline.json` retain the
events captured when the export was requested. Earlier history remains available
with an explicit recording boundary. See the
[timeline checkpoint](docs/evidence/2026-10-16-review-timeline.md).

**Checks** offers previous/next unresolved navigation and current-issue progress;
**Keyboard help** describes the working controls. **Remove table** removes a table
from active work with an inline explanation. Use **Removed tables** on its source
page to restore it while the report is retained. Cell reviews, issue decisions and
approval must be renewed; saved content/history and existing exports are retained.
Report deletion or expiry removes recovery. See the
[navigation/removal checkpoint](docs/evidence/2026-10-16-navigation-and-table-removal.md).

Use **Subtotals** to declare a signed sum of selected currency amounts and a
separate total, with an explicit absolute tolerance. Every participant needs
current confirmed source period, currency and scale. Unsupported/unreadable
inputs stay unavailable; edits reopen confirmation. Results and input evidence
are captured in workbook `Subtotals` and CSV `subtotals.json` when declarations
exist. A match checks this selected relation only. See the
[declared-subtotal checkpoint](docs/evidence/2026-10-16-declared-subtotal-checks.md).

In **Extraction history**, use **Review replacement** to compare a rerun candidate
with current saved work and explicitly adopt the complete original extraction as
a new draft. Earlier corrections remain in History. Reviews, context confirmations
and subtotal declarations reset; matching source areas require confirmation.
Ambiguous candidates need explicit selection. See the
[safe-adoption checkpoint](docs/evidence/2026-10-16-safe-extraction-adoption.md).

Start with the [local runbook](docs/runbooks/local.md). It gives exact dependency,
model preload, deployment and private-account commands. The product supports PDFs
up to 50 MiB/500 pages, one to five selected pages per run, and grids up to
500 rows/60 columns/10,000 slots. Image-based pages can use preloaded offline OCR;
selectable native content is preserved where usable. OCR recognition and routing
are fallible, and every extracted figure needs source-image review. An
interrupted extraction has a bounded retry; a missed table can be recovered
by selecting its region for OCR or creating a manual grid. Mixed text/image pages
include this recovery guidance. Omitted grid positions become uncertain editable
blanks; compare the image and explicitly confirm blank or enter the missing value.
The network-isolated worker has a hard 6 GiB container limit. Accounts,
owner checks, cancellation, deletion, retention and encrypted recovery belong to
the same workflow.

This is an implemented, locally tested private product; production readiness
is not established. Automatic extraction is unreliable on some layouts and
does not attach financial units automatically. The frozen 40-region evaluation
separates literal recovery, placement, associations, geometry and exact tables;
its reference is source-assisted agent annotation, not independent human gold.
The original held-out baseline is preserved. Results after inspecting those
reports are validation on known examples.

Evidence and claim boundaries:

- [Acceptance matrix](docs/evidence/acceptance-matrix.md)
- [Claims and limitations](docs/evidence/claims.md)
- [Extraction evaluation](docs/evidence/extraction-evaluation.md)
- [Review-study status](docs/evidence/review-study-status.md)
- [Final branch review](docs/evidence/final-branch-review.md)
- [Execution decisions and verification](docs/evidence/execution-ledger.md)
- [All implementation decisions and costs](docs/evidence/implementation-decisions.md)
- [Backup and recovery](docs/runbooks/backup-restore.md)

Human review-time savings, the proposed 20% improvement, demand and adoption are
unmeasured. There have been no external study participants. One current six-sheet
export has been opened and inspected in native Excel; broader workbook/version
compatibility and hosted service reliability remain unestablished.

Original reports, complete transcriptions, models, environments, passwords and
private execution artifacts are excluded from Git. `uv.lock`, the npm lockfile,
image digests and model hashes record dependencies; Debian package repositories
are not date-snapshot pinned, so later builds are not claimed bit-identical.
Evaluation reproduction and source hashes are in the linked evidence. This
repository has not been published or deployed to an external service.

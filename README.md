# Table Review

A private application for extracting English digital annual-report tables,
comparing them with the original page, correcting a saved revision and exporting
that exact revision to a spreadsheet. It runs locally on CPU with PostgreSQL,
FastAPI, React and a separately bounded Docling worker.

The workflow includes original-page highlights and zoom, region selection,
explicit manual grids, keyboard editing, immutable edit history, checked-cell
audit events, source discrepancy decisions, approval and workbook/CSV downloads.
Exports retain raw text, current text, source references, revision checksum and
review status. Warnings remain visible; marking cells checked is a user action,
not an automatic correctness guarantee.

Start with the [local runbook](docs/runbooks/local.md). It gives exact dependency,
model preload, deployment and private-account commands. The product supports PDFs
up to 50 MiB/500 pages, one to five selected pages per run, and grids up to
500 rows/60 columns/10,000 slots. Scans and OCR are outside this scope. An
interrupted extraction has a bounded retry; a missed table can be recovered
manually. The network-isolated worker has a hard 6 GiB container limit. Accounts,
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
unmeasured. There have been no external study participants. Local tests do not
establish hosted service reliability or native Microsoft Excel compatibility.

Original reports, complete transcriptions, models, environments, passwords and
private execution artifacts are excluded from Git. `uv.lock`, the npm lockfile,
image digests and model hashes record dependencies; Debian package repositories
are not date-snapshot pinned, so later builds are not claimed bit-identical.
Evaluation reproduction and source hashes are in the linked evidence. This
repository has not been published or deployed to an external service.

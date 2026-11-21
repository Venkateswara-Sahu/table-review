# Data and backups

## What is stored

The Docker deployment keeps state in named volumes:

| Volume | Contents |
|---|---|
| `table-review-local_db` | PostgreSQL database: reports, tables, revisions, reviews, decisions |
| `table-review-local_blobs` | Uploaded PDFs, rendered pages and exports |
| `table-review-local_journal` | Current lifecycle journal used for deletion and recovery |
| `table-review-local_keys` | Backup encryption key |
| `table-review-local_backups` | Encrypted `.trbackup` archives |
| `table-review-local_pulse` | Worker heartbeat |

Losing the backup key loses recovery access. Keep the key and the **current** journal on separately preserved storage; a database or blob restore must never replace the current journal.

## Retention

In `local` mode (`TABLE_REVIEW_DEPLOYMENT_MODE=local`), automatic document expiration is **disabled by default** (`TABLE_REVIEW_RETENTION_DAYS=none`), so your financial reports and review revisions stay available indefinitely until you delete them explicitly.

In `hosted` mode (or when `TABLE_REVIEW_RETENTION_DAYS` is set to a positive integer such as `30`), opening a report, editing, adding a manual table, or requesting an export extends its foreground retention window, and expiry becomes effective at the next worker maintenance sweep. The active-file cleanup target for deleted documents is 24 hours when the worker is healthy and storage is writable. Encrypted backups expire seven days after creation.

## Create a backup

Run the backup command inside the API container so it can reach the database, journal, key and backup volume:

```powershell
docker compose --env-file .cache/local.env -p table-review-local -f infra/compose.yaml exec api python -m table_review.lifecycle.backup_admin create --backup /backups/recovery.trbackup
```

The archive is encrypted and written only after the database rows and referenced blobs have been captured consistently. A live processing lease or busy table lock refuses the backup rather than taking a partial snapshot.

Purge expired authenticated backups with:

```powershell
docker compose --env-file .cache/local.env -p table-review-local -f infra/compose.yaml exec api python -m table_review.lifecycle.backup_admin purge
```

## Restore

Restore into a **new, disposable schema and blob directory** with no API or worker pointing at them. Never restore over the live schema or live blob root. The restore command authenticates the archive, rejects expired or incompatible snapshots and verifies every member hash before creating the target.

```powershell
docker compose --env-file .cache/local.env -p table-review-local -f infra/compose.yaml exec api python -m table_review.lifecycle.backup_admin restore --help
```

Use a schema named `test_` followed by 32 lowercase hex characters. A failed restore leaves a `.restore-pending` marker; leave the target offline and inspect the failure instead of deleting the marker.

The recovery workflow is exercised by:

```powershell
uv run --frozen --group dev pytest tests/integration/test_restore.py -q
```

## Deleting the deployment

Do not run `docker compose down -v` on a deployment containing saved work. It removes the database, blobs, journal, key and backup volumes. Stop the services with `uv run --frozen python scripts/deploy_local.py --stop` instead.

Backups contain password hashes and private report data. Restrict access to the backup volume and to any copy you move off the machine.

from uuid import UUID

from table_review.checks.engine import CHECK_VERSION, evaluate_checks
from table_review.checks.types import CheckContext
from table_review.extraction.types import SourceToken
from table_review.jobs.models import ExtractionRunRow
from table_review.storage.models import RevisionRow, TableRow
from table_review.tables.types import TableContent


class RevisionChecker:
    version = CHECK_VERSION

    def __call__(self, session, revision):
        table = session.get(TableRow, revision.table_id)
        original = session.get(RevisionRow, table.original_revision_id)
        source = TableContent.model_validate(original.content)
        run_id = original.extraction_manifest.get("extraction_run_id")
        run = session.get(ExtractionRunRow, UUID(run_id)) if run_id else None
        available = bool(
            run
            and run.owner_id == revision.owner_id
            and run.document_id == table.document_id
            and str(table.id) in run.source_tokens
        )
        tokens = (
            tuple(SourceToken.model_validate(item) for item in run.source_tokens[str(table.id)])
            if available
            else ()
        )
        regions = (
            (source.source_reference.box,)
            if source.source_reference and source.source_reference.box
            else ()
        )
        context = CheckContext(
            source_tokens=tokens,
            source_regions=regions,
            check_version=self.version,
            source_available=available,
        )
        versions = {
            "engine": self.version,
            "coverage": self.version if available and regions else "not_run",
            "source_identity": self.version if available else "not_run",
            "placement": self.version,
            "structural": self.version,
        }
        issues = evaluate_checks(TableContent.model_validate(revision.content), context)
        return [issue.model_dump(mode="json") for issue in issues], versions

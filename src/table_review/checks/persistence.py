from uuid import UUID

from table_review.checks.engine import CHECK_VERSION, evaluate_checks
from table_review.checks.types import CheckContext
from table_review.extraction.types import SourceToken
from table_review.jobs.models import ExtractionRunRow
from table_review.reviews.models import ReviewStateRow
from table_review.storage.models import RevisionRow, TableRow
from table_review.tables.types import TableContent


class RevisionChecker:
    version = CHECK_VERSION

    def __call__(self, session, revision):
        table = session.get(TableRow, revision.table_id)
        original = session.get(RevisionRow, table.original_revision_id)
        adopted = revision.extraction_manifest.get("adoption", {}).get("candidate_revision_id")
        if adopted:
            candidate = session.get(RevisionRow, UUID(adopted))
            source_table = session.get(TableRow, candidate.table_id) if candidate else None
            if (
                candidate
                and source_table
                and candidate.owner_id == revision.owner_id
                and source_table.document_id == table.document_id
            ):
                original = candidate
        source = TableContent.model_validate(original.content)
        run_id = original.extraction_manifest.get("extraction_run_id")
        run = session.get(ExtractionRunRow, UUID(run_id)) if run_id else None
        available = bool(
            run
            and run.owner_id == revision.owner_id
            and run.document_id == table.document_id
            and str(original.table_id) in run.source_tokens
        )
        tokens = (
            tuple(
                SourceToken.model_validate(item)
                for item in run.source_tokens[str(original.table_id)]
            )
            if available
            else ()
        )
        regions = (
            (source.source_reference.box,)
            if source.source_reference and source.source_reference.box
            else ()
        )
        state = session.get(ReviewStateRow, revision.id)
        context = CheckContext(
            source_tokens=tokens,
            source_regions=regions,
            check_version=self.version,
            source_available=available,
            unreadable_cell_ids=tuple(
                key
                for key, value in state.cell_reviews.items()
                if value.get("verdict") == "unreadable"
            )
            if state
            else (),
        )
        versions = {
            "engine": self.version,
            "coverage": self.version if available and regions else "not_run",
            "source_identity": self.version if available else "not_run",
            "placement": self.version,
            "structural": self.version,
        }
        content = TableContent.model_validate(revision.content)
        if content.subtotal_groups:
            versions["subtotal"] = "declared_sum_v1"
        issues = evaluate_checks(content, context)
        return [issue.model_dump(mode="json") for issue in issues], versions

from uuid import UUID

from sqlalchemy import select

from table_review.reviews.models import ReviewSnapshotRow, ReviewStateRow
from table_review.reviews.types import ReviewSnapshot, ReviewState
from table_review.storage.models import DocumentRow, RevisionRow, TableRow
from table_review.storage.repository import NotFound, RevisionConflict, _record


class ReviewIncomplete(RuntimeError):
    pass


class ReviewInputError(ValueError):
    pass


class ReviewService:
    def __init__(self, repository):
        self.repository = repository
        self.session_factory = repository.session_factory
        self.checker = None

    def _context(self, session, actor, revision_id, *, current=False):
        revision_id = UUID(str(revision_id))
        revision = session.scalar(
            select(RevisionRow)
            .join(TableRow, TableRow.id == RevisionRow.table_id)
            .join(DocumentRow, DocumentRow.id == TableRow.document_id)
            .where(
                RevisionRow.id == revision_id,
                RevisionRow.owner_id == actor.id,
                TableRow.owner_id == actor.id,
                DocumentRow.owner_id == actor.id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        if revision is None:
            raise NotFound("Revision not found")
        table = session.scalar(
            select(TableRow).where(TableRow.id == revision.table_id).with_for_update()
        )
        if current and table.current_revision_id != revision.id:
            raise RevisionConflict(table.current_revision_id)
        state = session.get(ReviewStateRow, revision.id)
        if state is None:
            state = ReviewStateRow(revision_id=revision.id, owner_id=actor.id)
            session.add(state)
            session.flush()
            if self.checker:
                issues, versions = self.checker(session, revision)
                state.issues, state.check_versions = list(issues), versions
        return table, revision, state

    @staticmethod
    def _unresolved(state):
        not_run = not state.check_versions or "not_run" in state.check_versions.values()
        count = sum(
            1
            for issue in state.issues
            if state.issue_decisions.get(issue["fingerprint"], {}).get("kind")
            not in ("dismiss", "resolved")
        )
        return not_run, count + int(not_run)

    def _view(self, session, table, state):
        not_run, unresolved = self._unresolved(state)
        snapshot = None
        if table.current_revision_id == state.revision_id and state.current_approval_id:
            snapshot = _record(
                ReviewSnapshot, session.get(ReviewSnapshotRow, state.current_approval_id)
            )
        return ReviewState(
            revision_id=state.revision_id,
            checked_cell_ids=tuple(state.checked_cell_ids),
            issue_decisions=state.issue_decisions,
            issues=tuple(state.issues),
            check_versions=state.check_versions,
            generation=state.generation,
            checks_not_run=not_run,
            unresolved_count=unresolved,
            current_approval=snapshot,
        )

    def state(self, actor, revision_id):
        with self.session_factory.begin() as session:
            table, _, state = self._context(session, actor, revision_id)
            return self._view(session, table, state)

    def mark_checked(self, actor, revision_id, cell_ids, checked):
        with self.session_factory.begin() as session:
            table, revision, state = self._context(session, actor, revision_id, current=True)
            valid = {cell["id"] for cell in revision.content["cells"]}
            if set(cell_ids) - valid:
                raise ReviewInputError("Selected cells are not in this revision")
            saved = set(state.checked_cell_ids)
            updated = saved | set(cell_ids) if checked else saved - set(cell_ids)
            if saved != updated:
                state.checked_cell_ids = sorted(updated)
                state.current_approval_id = None
                state.generation += 1
            return self._view(session, table, state)

    def set_checks(self, actor, revision_id, issues, check_versions):
        """Trusted check-engine integration, never a user-controlled API endpoint."""
        with self.session_factory.begin() as session:
            table, _, state = self._context(session, actor, revision_id, current=True)
            issues = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in issues
            ]
            if state.issues != issues or state.check_versions != check_versions:
                state.issues, state.check_versions = issues, dict(check_versions)
                valid_fingerprints = {issue["fingerprint"] for issue in issues}
                state.issue_decisions = {
                    fingerprint: decision
                    for fingerprint, decision in state.issue_decisions.items()
                    if fingerprint in valid_fingerprints
                }
                state.current_approval_id = None
                state.generation += 1
            return self._view(session, table, state)

    def after_revision(self, session, old_revision, new_revision, event):
        old_state = session.get(ReviewStateRow, old_revision.id)
        checked, decisions = [], {}
        if old_state:
            if not event.structural_change and event.operation["kind"] not in (
                "set_headers",
                "set_notes",
            ):
                ids = {cell["id"] for cell in new_revision.content["cells"]}
                checked = sorted(
                    set(old_state.checked_cell_ids) & ids - set(event.affected_cell_ids)
                )
            old_state.current_approval_id = None
        issues, versions = (
            self.checker(session, new_revision) if self.checker else ((), {"checks": "not_run"})
        )
        if old_state:
            fingerprints = {issue["fingerprint"] for issue in issues}
            decisions = {
                key: value
                for key, value in old_state.issue_decisions.items()
                if key in fingerprints
            }
        session.add(
            ReviewStateRow(
                revision_id=new_revision.id,
                owner_id=new_revision.owner_id,
                checked_cell_ids=checked,
                issue_decisions=decisions,
                issues=list(issues),
                check_versions=versions,
            )
        )

    def approve(self, actor, revision_id, allow_warnings=False):
        with self.session_factory.begin() as session:
            _, revision, state = self._context(session, actor, revision_id, current=True)
            cell_ids = {cell["id"] for cell in revision.content["cells"]}
            if cell_ids != set(state.checked_cell_ids):
                raise ReviewIncomplete(
                    "Check every current cell, including blanks, before approving"
                )
            not_run, unresolved = self._unresolved(state)
            if unresolved and not allow_warnings:
                raise ReviewIncomplete(
                    "Resolve remaining issues or explicitly approve with warnings"
                )
            row = ReviewSnapshotRow(
                revision_id=revision.id,
                content_checksum=revision.checksum,
                label="reviewed_with_warnings" if unresolved else "reviewed",
                checked_cell_ids=sorted(cell_ids),
                issue_decisions=state.issue_decisions,
                check_versions=state.check_versions,
                issues=state.issues,
                checks_not_run=not_run,
                unresolved_count=unresolved,
                actor_id=actor.id,
            )
            session.add(row)
            session.flush()
            state.current_approval_id = row.id
            return _record(ReviewSnapshot, row)

    def current_approval(self, actor, revision_id):
        return self.state(actor, revision_id).current_approval

    def snapshot(self, actor, snapshot_id):
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ReviewSnapshotRow).where(
                    ReviewSnapshotRow.id == UUID(str(snapshot_id)),
                    ReviewSnapshotRow.actor_id == actor.id,
                )
            )
            if row is None:
                raise NotFound("Approval not found")
            self._context(session, actor, row.revision_id)
            return _record(ReviewSnapshot, row)

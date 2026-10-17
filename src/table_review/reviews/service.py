from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from table_review.checks.types import IssueDecisionRecord
from table_review.reviews.history import bounded_events
from table_review.reviews.models import (
    CellReviewEventRow,
    IssueDecisionRow,
    ReviewSnapshotRow,
    ReviewStateRow,
)
from table_review.reviews.timeline import append_event, read_events
from table_review.reviews.types import (
    CellReviewEvent,
    CellReviewHistory,
    CellReviewInput,
    ReviewSnapshot,
    ReviewState,
    ReviewTimeline,
)
from table_review.reviews.verdicts import normalize_legacy, record_for, review_warnings
from table_review.storage.models import DocumentRow, RevisionRow, TableRow
from table_review.storage.repository import NotFound, RevisionConflict, _record


class ReviewIncomplete(RuntimeError):
    pass


class ReviewInputError(ValueError):
    pass


class ReviewService:
    history_bytes_limit = 8 * 1024**2

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
        table_identity = session.get(TableRow, revision.table_id)
        self.repository._lock_document(session, actor.id, table_identity.document_id)
        table = session.scalar(
            select(TableRow)
            .where(TableRow.id == revision.table_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if table.removed_at is not None:
            raise NotFound("Table not found")
        if current and table.current_revision_id != revision.id:
            raise RevisionConflict(table.current_revision_id)
        state = session.get(ReviewStateRow, revision.id)
        created = state is None
        if created:
            state = ReviewStateRow(revision_id=revision.id, owner_id=actor.id)
            session.add(state)
            session.flush()
        normalize_legacy(state, revision)
        expected_version = getattr(self.checker, "version", None)
        if self.checker and (
            created or (expected_version and state.check_versions.get("engine") != expected_version)
        ):
            before = {
                "versions": dict(state.check_versions),
                "fingerprints": sorted(issue["fingerprint"] for issue in state.issues),
                "decisions": dict(state.issue_decisions),
                "approval_id": str(state.current_approval_id)
                if state.current_approval_id
                else None,
            }
            issues, versions = self.checker(session, revision)
            fingerprints = {issue["fingerprint"] for issue in issues}
            state.issue_decisions = {
                key: decision
                for key, decision in state.issue_decisions.items()
                if key in fingerprints
            }
            state.issues, state.check_versions = list(issues), versions
            state.current_approval_id = None
            state.generation += int(not created)
            append_event(
                session,
                revision,
                "checks_changed",
                details={
                    "reason": "Checks initialized"
                    if created
                    else "Check engine changed; review evidence was refreshed",
                    "before": before,
                    "after": {"versions": versions, "fingerprints": sorted(fingerprints)},
                    "invalidated_approval_id": before["approval_id"],
                    "invalidated_issue_fingerprints": sorted(
                        set(before["decisions"]) - set(state.issue_decisions)
                    ),
                },
            )
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
        additional = review_warnings(
            session.get(RevisionRow, state.revision_id), state.cell_reviews
        )
        snapshot = None
        if table.current_revision_id == state.revision_id and state.current_approval_id:
            snapshot = _record(
                ReviewSnapshot, session.get(ReviewSnapshotRow, state.current_approval_id)
            )
        return ReviewState(
            revision_id=state.revision_id,
            checked_cell_ids=tuple(state.checked_cell_ids),
            issue_decisions=state.issue_decisions,
            issues=(*state.issues, *additional),
            check_versions=state.check_versions,
            generation=state.generation,
            checks_not_run=not_run,
            unresolved_count=unresolved + len(additional),
            current_approval=snapshot,
            cell_reviews=state.cell_reviews,
            unreadable_count=sum(
                record["verdict"] == "unreadable" for record in state.cell_reviews.values()
            ),
        )

    def state(self, actor, revision_id):
        with self.session_factory.begin() as session:
            table, _, state = self._context(session, actor, revision_id)
            return self._view(session, table, state)

    def mark_checked(self, actor, revision_id, cell_ids, checked):
        return self.decide_cells(
            actor,
            revision_id,
            CellReviewInput(
                cell_ids=cell_ids, verdict="confirmed_value" if checked else "unreviewed"
            ),
        )

    def decide_cells(self, actor, revision_id, decision):
        with self.session_factory.begin() as session:
            table, revision, state = self._context(session, actor, revision_id, current=True)
            cells = {cell["id"]: cell for cell in revision.content["cells"]}
            if set(decision.cell_ids) - cells.keys():
                raise ReviewInputError("Selected cells are not in this revision")
            for cell_id in decision.cell_ids:
                nonempty = bool(cells[cell_id]["text"].strip())
                if decision.verdict == "confirmed_value" and not nonempty:
                    raise ReviewInputError(
                        "Empty cells need an explicit confirmed blank or unreadable decision"
                    )
                if decision.verdict == "confirmed_blank" and nonempty:
                    raise ReviewInputError(
                        "Only empty cells can be confirmed blank; correct the literal first"
                    )
            records, changed = dict(state.cell_reviews), {}
            for cell_id in sorted(set(decision.cell_ids)):
                prior = records.get(cell_id)
                if decision.verdict == "unreviewed" and prior is None:
                    continue
                if (
                    prior
                    and prior["verdict"] == decision.verdict
                    and prior["reason"] == decision.reason
                    and prior["actor_id"] == str(actor.id)
                ):
                    continue
                record = record_for(
                    cells[cell_id],
                    revision.id,
                    decision.verdict,
                    actor_id=actor.id,
                    decided_at=datetime.now(UTC),
                    reason=decision.reason,
                )
                changed[cell_id] = record
                if decision.verdict == "unreviewed":
                    records.pop(cell_id, None)
                else:
                    records[cell_id] = record
            if changed:
                append_event(
                    session,
                    revision,
                    "cell_review",
                    actor_id=actor.id,
                    cell_ids=changed,
                    details={
                        "reason": "Cell review changed",
                        "before": {key: state.cell_reviews.get(key) for key in changed},
                        "after": {key: records.get(key) for key in changed},
                        "invalidated_approval_id": str(state.current_approval_id)
                        if state.current_approval_id
                        else None,
                    },
                )
                session.add(
                    CellReviewEventRow(
                        revision_id=revision.id,
                        actor_id=actor.id,
                        cell_ids=sorted(changed),
                        checked=decision.verdict != "unreviewed",
                        cell_reviews=changed,
                    )
                )
                state.cell_reviews = records
                state.checked_cell_ids = sorted(records)
                state.current_approval_id = None
                state.generation += 1
                if revision.content.get("subtotal_groups") and self.checker:
                    session.flush()
                    issues, versions = self.checker(session, revision)
                    if issues != state.issues:
                        before = {
                            "versions": state.check_versions,
                            "fingerprints": [item["fingerprint"] for item in state.issues],
                            "decisions": state.issue_decisions,
                        }
                        fingerprints = {item["fingerprint"] for item in issues}
                        state.issue_decisions = {
                            key: value
                            for key, value in state.issue_decisions.items()
                            if key in fingerprints
                        }
                        state.issues, state.check_versions = issues, versions
                        append_event(
                            session,
                            revision,
                            "checks_changed",
                            details={
                                "reason": "Cell readability changed declared subtotal availability",
                                "before": before,
                                "after": {
                                    "versions": versions,
                                    "fingerprints": sorted(fingerprints),
                                },
                                "invalidated_issue_fingerprints": sorted(
                                    set(before["decisions"]) - fingerprints
                                ),
                            },
                        )
            return self._view(session, table, state)

    def checked_history(self, actor, revision_id, offset):
        with self.session_factory.begin() as session:
            self._context(session, actor, revision_id)
            rows, more = bounded_events(
                session,
                revision_id,
                byte_limit=self.history_bytes_limit,
                offset=offset,
                page_size=100,
            )
            return CellReviewHistory(
                items=tuple(_record(CellReviewEvent, row) for row in rows),
                offset=offset,
                has_more=more,
            )

    def set_checks(self, actor, revision_id, issues, check_versions):
        """Trusted check-engine integration, never a user-controlled API endpoint."""
        with self.session_factory.begin() as session:
            table, _, state = self._context(session, actor, revision_id, current=True)
            issues = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in issues
            ]
            if state.issues != issues or state.check_versions != check_versions:
                before = {
                    "versions": dict(state.check_versions),
                    "fingerprints": sorted(issue["fingerprint"] for issue in state.issues),
                    "decisions": dict(state.issue_decisions),
                    "approval_id": str(state.current_approval_id)
                    if state.current_approval_id
                    else None,
                }
                state.issues, state.check_versions = issues, dict(check_versions)
                valid_fingerprints = {issue["fingerprint"] for issue in issues}
                state.issue_decisions = {
                    fingerprint: decision
                    for fingerprint, decision in state.issue_decisions.items()
                    if fingerprint in valid_fingerprints
                }
                state.current_approval_id = None
                state.generation += 1
                append_event(
                    session,
                    session.get(RevisionRow, revision_id),
                    "checks_changed",
                    details={
                        "reason": "Check results changed; prior approval was invalidated",
                        "before": before,
                        "after": {
                            "versions": state.check_versions,
                            "fingerprints": sorted(valid_fingerprints),
                        },
                        "invalidated_approval_id": before["approval_id"],
                        "invalidated_issue_fingerprints": sorted(
                            set(before["decisions"]) - set(state.issue_decisions)
                        ),
                    },
                )
            return self._view(session, table, state)

    def decide_issue(self, actor, revision_id, issue_id, decision):
        with self.session_factory.begin() as session:
            table, revision, state = self._context(session, actor, revision_id, current=True)
            issue = next(
                (item for item in state.issues if UUID(item["id"]) == UUID(str(issue_id))), None
            )
            if issue is None:
                raise NotFound("Issue not found")
            previous = state.issue_decisions.get(issue["fingerprint"])
            if (
                previous
                and previous["kind"] == decision.kind
                and previous["reason"] == decision.reason
                and previous["actor_id"] == str(actor.id)
            ):
                return self._view(session, table, state)
            record = IssueDecisionRecord(
                kind=decision.kind,
                reason=decision.reason,
                actor_id=actor.id,
                decided_at=datetime.now(UTC),
                issue_id=issue_id,
                fingerprint=issue["fingerprint"],
                revision_id=revision.id,
            ).model_dump(mode="json")
            session.add(
                IssueDecisionRow(
                    owner_id=actor.id,
                    revision_id=revision.id,
                    issue_id=issue_id,
                    fingerprint=issue["fingerprint"],
                    decision=record,
                )
            )
            append_event(
                session,
                revision,
                "issue_decision",
                actor_id=actor.id,
                cell_ids=issue.get("affected_cell_ids", []),
                details={
                    "reason": decision.reason,
                    "issue_id": str(issue_id),
                    "fingerprint": issue["fingerprint"],
                    "before": previous,
                    "after": record,
                    "invalidated_approval_id": str(state.current_approval_id)
                    if state.current_approval_id
                    else None,
                },
            )
            state.issue_decisions = {**state.issue_decisions, issue["fingerprint"]: record}
            state.current_approval_id = None
            state.generation += 1
            return self._view(session, table, state)

    def after_revision(self, session, old_revision, new_revision, event):
        old_state = session.get(ReviewStateRow, old_revision.id)
        checked, decisions, cell_reviews = [], {}, {}
        prior_approval = (
            str(old_state.current_approval_id)
            if old_state and old_state.current_approval_id
            else None
        )
        if old_state:
            normalize_legacy(old_state, old_revision)
            if not event.structural_change and event.operation["kind"] not in (
                "set_headers",
                "set_notes",
            ):
                ids = {cell["id"] for cell in new_revision.content["cells"]}
                checked = sorted(
                    set(old_state.checked_cell_ids) & ids - set(event.affected_cell_ids)
                )
                cell_reviews = {key: old_state.cell_reviews[key] for key in checked}
            old_state.current_approval_id = None
        new_state = ReviewStateRow(
            revision_id=new_revision.id,
            owner_id=new_revision.owner_id,
            checked_cell_ids=checked,
            cell_reviews=cell_reviews,
        )
        session.add(new_state)
        session.flush()
        issues, versions = (
            self.checker(session, new_revision) if self.checker else ((), {"checks": "not_run"})
        )
        if old_state and event.operation["kind"] != "adopt_extraction":
            fingerprints = {issue["fingerprint"] for issue in issues}
            decisions = {
                key: value
                for key, value in old_state.issue_decisions.items()
                if key in fingerprints
            }
        new_state.issue_decisions = decisions
        new_state.issues = list(issues)
        new_state.check_versions = versions

        old_cells = {cell["id"]: cell for cell in old_revision.content["cells"]}
        new_cells = {cell["id"]: cell for cell in new_revision.content["cells"]}
        changes = [
            {"cell_id": key, "before": old_cells.get(key), "after": new_cells.get(key)}
            for key in sorted(old_cells.keys() | new_cells.keys())
            if old_cells.get(key) != new_cells.get(key)
        ]
        old_context = {
            (item["cell_id"], item["kind"]): item
            for item in old_revision.content.get("context_bindings", [])
        }
        new_context = {
            (item["cell_id"], item["kind"]): item
            for item in new_revision.content.get("context_bindings", [])
        }
        context_changes = [
            {
                "cell_id": key[0],
                "kind": key[1],
                "before": old_context.get(key),
                "after": new_context.get(key),
            }
            for key in sorted(old_context.keys() | new_context.keys())
            if old_context.get(key) != new_context.get(key)
        ]
        invalidated = sorted(set(old_state.checked_cell_ids) - set(checked)) if old_state else []
        append_event(
            session,
            new_revision,
            "edit",
            actor_id=new_revision.owner_id,
            cell_ids=set(event.affected_cell_ids) | set(invalidated),
            baseline_revision_id=old_revision.id,
            details={
                "reason": "Saved "
                + event.operation["kind"].replace("_", " ")
                + (
                    "; dependent review evidence reopened"
                    if invalidated
                    or prior_approval
                    or old_context.keys() - new_context.keys()
                    or (old_state and set(old_state.issue_decisions) - set(decisions))
                    else ""
                ),
                "operation": event.operation,
                "base_revision_id": str(old_revision.id),
                "changes": changes,
                "context_changes": context_changes,
                "invalidated_cell_ids": invalidated,
                "invalidated_context_cell_ids": sorted(
                    {key[0] for key in old_context.keys() - new_context.keys()}
                ),
                "invalidated_issue_fingerprints": sorted(
                    set(old_state.issue_decisions) - set(decisions)
                )
                if old_state
                else [],
                "invalidated_approval_id": prior_approval,
                "before_notes": old_revision.content.get("unit_notes", []),
                "after_notes": new_revision.content.get("unit_notes", []),
                "before_headers": old_revision.content.get("header_rows", []),
                "after_headers": new_revision.content.get("header_rows", []),
                "before_labels": old_revision.content.get("row_label_columns", []),
                "after_labels": new_revision.content.get("row_label_columns", []),
                "before_subtotal_groups": old_revision.content.get("subtotal_groups", []),
                "after_subtotal_groups": new_revision.content.get("subtotal_groups", []),
            },
        )

    def approve(self, actor, revision_id, allow_warnings=False):
        with self.session_factory.begin() as session:
            table, revision, state = self._context(session, actor, revision_id, current=True)
            cell_ids = {cell["id"] for cell in revision.content["cells"]}
            if cell_ids != set(state.checked_cell_ids):
                raise ReviewIncomplete(
                    "Check every current cell, including blanks, before approving"
                )
            view = self._view(session, table, state)
            not_run, unresolved = view.checks_not_run, view.unresolved_count
            if unresolved and not allow_warnings:
                raise ReviewIncomplete(
                    "Resolve remaining issues or explicitly approve with warnings"
                )
            if (
                view.current_approval
                and view.current_approval.actor_id == actor.id
                and view.current_approval.content_checksum == revision.checksum
            ):
                return view.current_approval
            row = ReviewSnapshotRow(
                revision_id=revision.id,
                content_checksum=revision.checksum,
                label="reviewed_with_warnings" if unresolved else "reviewed",
                checked_cell_ids=sorted(cell_ids),
                cell_reviews=state.cell_reviews,
                unreadable_count=view.unreadable_count,
                issue_decisions=state.issue_decisions,
                check_versions=state.check_versions,
                issues=[issue.model_dump(mode="json") for issue in view.issues],
                checks_not_run=not_run,
                unresolved_count=unresolved,
                actor_id=actor.id,
            )
            session.add(row)
            session.flush()
            append_event(
                session,
                revision,
                "approval",
                actor_id=actor.id,
                details={
                    "reason": "Table approved with warnings" if unresolved else "Table approved",
                    "before": {
                        "approval_id": str(state.current_approval_id)
                        if state.current_approval_id
                        else None
                    },
                    "after": {
                        "approval_id": str(row.id),
                        "label": row.label,
                        "content_checksum": revision.checksum,
                        "generation": state.generation,
                        "unresolved_count": unresolved,
                    },
                },
            )
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

    def timeline(self, actor, revision_id, *, after=0, through=None, cell_id=None):
        with self.session_factory.begin() as session:
            revision = session.get(RevisionRow, revision_id)
            if revision is None or revision.owner_id != actor.id:
                raise NotFound("Revision not found")
            table = self.repository._table(session, actor.id, revision.table_id)
            self.repository._lock_document(session, actor.id, table.document_id)
            self.repository._table(session, actor.id, revision.table_id)
            events, more, cutoff = read_events(
                session,
                revision,
                after=after,
                through=through,
                cell_id=cell_id,
                page_size=100,
                byte_limit=self.history_bytes_limit,
            )
            return ReviewTimeline(
                items=events,
                through_sequence=cutoff,
                next_after_sequence=events[-1].sequence if events else after,
                has_more=more,
                coverage="recorded_since_boundary" if cutoff else "legacy_only",
            )

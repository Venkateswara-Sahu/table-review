"""Explicit whole-result adoption; no positional merge of reviewed corrections."""

import json
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import Field

from table_review.documents.types import ImmutableModel, SourceReference
from table_review.extraction.comparison import _overlap, compare_tables
from table_review.jobs.models import ExtractionRunRow
from table_review.storage.models import EditEventInput, RevisionRow
from table_review.storage.repository import NotFound, RevisionConflict
from table_review.tables.service import TableInputError
from table_review.tables.types import TableContent


class AdoptInput(ImmutableModel):
    base_revision_id: UUID
    candidate_table_id: UUID
    candidate_revision_id: UUID
    target_visibility_version: int = Field(ge=0)
    candidate_visibility_version: int = Field(ge=0)
    confirmed_same_table: Literal[True]
    confirmed_replacement: Literal[True]


class AdoptionPreview(ImmutableModel):
    base_revision_id: UUID
    candidate_table_id: UUID
    candidate_revision_id: UUID
    target_visibility_version: int
    candidate_visibility_version: int
    current_shape: tuple[int, int]
    candidate_shape: tuple[int, int]
    current_reference: SourceReference
    candidate_reference: SourceReference
    current_context_confirmations: int
    current_subtotal_declarations: int
    changed_cell_count: int
    difference: dict[str, Any]
    next_offset: int
    has_more: bool


class AdoptionService:
    def __init__(self, repository, tables):
        self.repository, self.tables = repository, tables

    def _pair(self, session, actor, target_id, candidate_id):
        target = self.repository._table(session, actor.id, target_id)
        candidate = self.repository._table(session, actor.id, candidate_id)
        if target.document_id != candidate.document_id:
            raise NotFound("Table not found")
        if target.id == candidate.id or candidate.origin != "extracted":
            raise TableInputError("Choose a separate original extraction as the candidate")
        current = session.get(RevisionRow, target.current_revision_id)
        original = session.get(RevisionRow, candidate.original_revision_id)
        before, after = map(TableContent.model_validate, (current.content, original.content))
        if (
            not before.source_reference
            or not after.source_reference
            or before.source_reference.precision == "page"
            or after.source_reference.precision == "page"
            or _overlap(before, after) <= 0
        ):
            raise TableInputError(
                "Choose overlapping source areas on the same original page; coarse references cannot confirm a replacement"
            )
        run_id = original.extraction_manifest.get("extraction_run_id")
        try:
            run = session.get(ExtractionRunRow, UUID(run_id)) if run_id else None
        except ValueError:
            run = None
        document = self.repository._document(session, actor.id, target.document_id)
        if not (
            run
            and run.owner_id == actor.id
            and run.document_id == document.id
            and run.source_sha256 == document.source_sha256
            and str(candidate.id) in run.table_ids
        ):
            raise TableInputError("Candidate has no retained extraction provenance for this report")
        return target, candidate, current, original, before, after

    def preview(self, actor, target_id, candidate_id, offset=0):
        with self.repository.session_factory.begin() as session:
            target = self.repository._table(session, actor.id, target_id)
            self.repository._lock_document(session, actor.id, target.document_id)
            target, candidate, current, original, before, after = self._pair(
                session, actor, target_id, candidate_id
            )
            diff = compare_tables(before, after)
            all_changes = diff["changed_cells"]
            selected, size = [], len(json.dumps(diff["table_changes"], ensure_ascii=True).encode())
            budget = 256 * 1024 - 4096
            if size > budget:
                raise TableInputError(
                    "Table metadata exceeds the comparison preview budget; inspect the saved tables directly"
                )
            for item in all_changes[offset : offset + 50]:
                length = len(json.dumps(item, ensure_ascii=True).encode())
                if size + length > budget:
                    if not selected:
                        raise TableInputError(
                            "A comparison entry exceeds the preview budget; inspect both saved tables directly"
                        )
                    break
                selected.append(item)
                size += length
            result = AdoptionPreview(
                base_revision_id=current.id,
                candidate_table_id=candidate.id,
                candidate_revision_id=original.id,
                target_visibility_version=target.removal_version,
                candidate_visibility_version=candidate.removal_version,
                current_shape=(before.rows, before.columns),
                candidate_shape=(after.rows, after.columns),
                current_reference=before.source_reference,
                candidate_reference=after.source_reference,
                current_context_confirmations=len(before.context_bindings),
                current_subtotal_declarations=len(before.subtotal_groups),
                changed_cell_count=len(all_changes),
                difference={**diff, "changed_cells": selected},
                next_offset=offset + len(selected),
                has_more=offset + len(selected) < len(all_changes),
            )
            if (
                len(json.dumps(result.model_dump(mode="json"), ensure_ascii=True).encode())
                > 256 * 1024
            ):
                raise TableInputError(
                    "Comparison exceeds the preview budget; inspect the saved tables directly"
                )
            return result

    def adopt(self, actor, target_id, payload: AdoptInput, key):
        operation = {"kind": "adopt_extraction", **payload.model_dump(mode="json")}
        prior = self.repository.get_edit_retry(
            actor.id, target_id, payload.base_revision_id, operation, key
        )
        if prior:
            return prior
        with self.repository.session_factory() as session:
            target, candidate, _current, original, before, after = self._pair(
                session, actor, target_id, payload.candidate_table_id
            )
            self._versions(target, candidate, original, payload)
            namespace = uuid5(actor.id, f"{target_id}/{payload.base_revision_id}/{key}")
            adopted = after.model_copy(
                update={
                    "cells": tuple(
                        cell.model_copy(update={"id": str(uuid5(namespace, cell.id))})
                        for cell in after.cells
                    ),
                    "context_bindings": (),
                    "subtotal_groups": (),
                }
            )

        def manifest(session, old_revision):
            target = self.repository._table(session, actor.id, target_id)
            candidate = self.repository._table(session, actor.id, payload.candidate_table_id)
            if candidate.document_id != target.document_id:
                raise NotFound("Table not found")
            original = session.get(RevisionRow, candidate.original_revision_id)
            self._versions(target, candidate, original, payload)
            return {
                **original.extraction_manifest,
                "adoption": {
                    "candidate_table_id": str(candidate.id),
                    "candidate_revision_id": str(original.id),
                    "target_base_revision_id": str(old_revision.id),
                    "policy": "whole_candidate_new_draft_no_review_transfer_v1",
                },
            }

        event = EditEventInput(
            idempotency_key=key,
            operation=operation,
            structural_change=True,
            affected_cell_ids=tuple(
                sorted({cell.id for cell in before.cells} | {cell.id for cell in adopted.cells})
            ),
        )
        return self.repository.save_revision(
            actor.id,
            target_id,
            payload.base_revision_id,
            adopted,
            event,
            on_saved=self.tables._after_save,
            prepare_manifest=manifest,
        )

    @staticmethod
    def _versions(target, candidate, original, payload):
        if (
            target.removal_version != payload.target_visibility_version
            or candidate.removal_version != payload.candidate_visibility_version
            or original.id != payload.candidate_revision_id
        ):
            raise RevisionConflict(target.current_revision_id)

"""One canonical header path for API, review invalidation and captured exports."""

import hashlib
import json
import re

from table_review.tables.context_types import CellContext, ContextProposal, ContextSource

_YEAR = re.compile(r"\b(?:18|19|20|21)\d{2}\b")
_CURRENCY = re.compile(
    r"\b(?:USD|EUR|GBP|INR|CAD|AUD|JPY|U\.S\.\s*dollars?)\b|\bUS\$|[$€£₹¥]", re.IGNORECASE
)
_SCALE = re.compile(r"\b(?:thousands?|millions?|billions?)\b", re.IGNORECASE)
_MEASURE = re.compile(r"%|\b(?:percent(?:age)?|amount|per[ -]share)\b", re.IGNORECASE)


def cell_source(cell):
    refs = cell.source_references or (cell.reference,)
    return ContextSource(
        id="cell:" + cell.id,
        text=cell.text[:800],
        raw_text=cell.raw_text[:800],
        references=refs[:32],
        origin="current_draft" if cell.text != cell.raw_text else "extracted",
        truncated=len(cell.text) > 800 or len(cell.raw_text) > 800 or len(refs) > 32,
    )


def proposal(kind, value, source):
    identity = json.dumps([kind, value, source.model_dump(mode="json")], sort_keys=True)
    return ContextProposal(
        id=hashlib.sha256(identity.encode()).hexdigest(),
        kind=kind,
        value=value,
        source_ids=(source.id,),
    )


class ContextIndex:
    def __init__(self, content):
        self.content = content
        self.cells = {cell.id: cell for cell in content.cells}
        self.headers = [cell for cell in content.cells if cell.row in content.header_rows]
        self.labels = [cell for cell in content.cells if cell.col in content.row_label_columns]
        self.bands = []
        for row in sorted(content.header_rows):
            if not self.bands or row != self.bands[-1][-1] + 1:
                self.bands.append([])
            self.bands[-1].append(row)
        self.headers_by_col, self.labels_by_row = {}, {}
        for header in self.headers:
            for col in range(header.col, header.col + header.col_span):
                self.headers_by_col.setdefault(col, []).append(header)
        for label in self.labels:
            for row in range(label.row, label.row + label.row_span):
                self.labels_by_row.setdefault(row, []).append(label)
        self.source_cache, self.proposal_cache = {}, {}
        self.bindings = {}
        for binding in content.context_bindings:
            self.bindings.setdefault(binding.cell_id, []).append(binding)

    def paths(self, cell_id):
        if cell_id not in self.cells:
            raise ValueError("Unknown cell ID")
        target = self.cells[cell_id]
        band = next((band for band in reversed(self.bands) if band[0] <= target.row), ())
        headers = sorted(
            (
                cell
                for cell in self.headers_by_col.get(target.col, ())
                if cell.row in band
                and cell.row + cell.row_span <= target.row
                and cell.col <= target.col
                and cell.col + cell.col_span >= target.col + target.col_span
                and cell.id != target.id
            ),
            key=lambda cell: (cell.row, cell.col, cell.id),
        )
        labels = sorted(
            (
                cell
                for cell in self.labels_by_row.get(target.row, ())
                if cell.row <= target.row
                and cell.row + cell.row_span >= target.row + target.row_span
                and cell.col + cell.col_span <= target.col
                and cell.id != target.id
            ),
            key=lambda cell: (cell.col, cell.row, cell.id),
        )
        return headers, labels

    def dependent_cells(self, changed):
        result = set(changed)
        if not any(cell.id in changed for cell in (*self.headers, *self.labels)):
            return result
        for cell_id in self.cells:
            headers, labels = self.paths(cell_id)
            if any(cell.id in changed for cell in (*headers, *labels)):
                result.add(cell_id)
        return result

    def cell(self, cell_id):
        headers, labels = self.paths(cell_id)

        def source_for(cell):
            if cell.id not in self.source_cache:
                self.source_cache[cell.id] = cell_source(cell)
            return self.source_cache[cell.id]

        column_headers, row_headers = (
            tuple(map(source_for, headers)),
            tuple(map(source_for, labels)),
        )
        sources = (*column_headers, *row_headers, *self.content.context_sources)
        proposals, warnings = [], []
        header_period = any(_YEAR.search(source.text) for source in (*column_headers, *row_headers))
        for source in sources:
            cache_key = (source.id, header_period)
            if cache_key in self.proposal_cache:
                cached_proposals, cached_warnings = self.proposal_cache[cache_key]
                proposals.extend(cached_proposals)
                warnings.extend(cached_warnings)
                continue
            proposal_start, warning_start = len(proposals), len(warnings)
            if source.truncated:
                warnings.append(
                    "Long context evidence is abbreviated; confirmation is unavailable."
                )
                continue
            if source.text.strip():
                years = set(_YEAR.findall(source.text))
                if len(years) > 1:
                    warnings.append(
                        "A source names several years; separate its headers before confirming a period."
                    )
                elif years and (not source.id.startswith("caption:") or not header_period):
                    proposals.append(proposal("period", source.text, source))
                for match in _CURRENCY.finditer(source.text):
                    proposals.append(proposal("currency", match.group(), source))
                if _SCALE.search(source.text):
                    proposals.append(proposal("scale", source.text, source))
                if _MEASURE.search(source.text):
                    proposals.append(proposal("measure", source.text, source))
            self.proposal_cache[cache_key] = (
                tuple(proposals[proposal_start:]),
                tuple(warnings[warning_start:]),
            )
        proposals = tuple({item.id: item for item in proposals}.values())
        if len(proposals) > 32:
            warnings.append("Context has many alternatives; only the first 32 proposals are shown.")
            proposals = proposals[:32]
        valid = {item.id: item for item in proposals}
        confirmed = tuple(
            binding
            for binding in self.bindings.get(cell_id, [])
            if binding.id in valid
            and binding.confirmed_by
            and binding.confirmed_at
            and binding.value == valid[binding.id].value
            and binding.kind == valid[binding.id].kind
            and binding.source_ids == valid[binding.id].source_ids
        )
        return CellContext(
            cell_id=cell_id,
            column_headers=column_headers,
            row_headers=row_headers,
            sources=sources,
            proposals=proposals,
            confirmed=confirmed,
            warnings=tuple(dict.fromkeys(warnings)),
        )

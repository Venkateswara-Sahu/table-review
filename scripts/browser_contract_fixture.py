"""Synthetic browser contracts only; never installed as a product extractor."""

import hashlib
from uuid import uuid4

from table_review.documents.pdf import page_info, source_tokens
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import ExtractedTable, ExtractionResult
from table_review.jobs.types import TaskOutcome
from table_review.tables.types import CellContent, TableContent


def contract_extractor(queue):
    def extract(task, lease, work_dir, limits):
        from table_review.accounts.types import Actor

        source = queue.documents.source_path(Actor(id=task.owner_id), task.document_id)
        page = task.input["pages"][0]
        info = page_info(source, page)
        tokens = source_tokens(source, page)
        values = (
            ("Metric", "2025", "2024"),
            ("Revenue", "1,234.50", "1,100.00"),
            ("Expense", "(123.45)", "(100.00)"),
            ("Total", "1,111.05", "1,000.00"),
        )
        cells = []
        for row, labels in enumerate(values):
            for col, literal in enumerate(labels):
                found = next(t for t in tokens if t.text == literal)
                cells.append(
                    CellContent(
                        id=str(uuid4()),
                        row=row,
                        col=col,
                        text=literal,
                        raw_text=literal,
                        role="column_header" if row == 0 else "row_header" if col == 0 else "data",
                        reference=found.provenance,
                    )
                )
        area = PageBox(
            page=page,
            left=min(c.reference.box.left for c in cells),
            top=min(c.reference.box.top for c in cells),
            right=max(c.reference.box.right for c in cells),
            bottom=max(c.reference.box.bottom for c in cells),
            page_width=info.width,
            page_height=info.height,
        )
        content = TableContent(
            rows=4,
            columns=3,
            cells=tuple(cells),
            header_rows=(0,),
            row_label_columns=(0,),
            source_reference=SourceReference(page=page, precision="table", box=area),
        )
        result = ExtractionResult(
            tables=(
                ExtractedTable(
                    id=str(uuid4()),
                    page_no=page,
                    region=area,
                    content=content,
                    source_tokens=tokens,
                ),
            ),
            page_info=(info,),
            warnings=("synthetic_contract_fixture",),
            manifest={
                "evidence_kind": "synthetic_browser_contract",
                "parser_executed": False,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
        )
        return TaskOutcome(status="succeeded", extraction=result.model_dump(mode="json"))

    return extract

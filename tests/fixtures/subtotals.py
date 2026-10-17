from datetime import UTC, datetime
from uuid import uuid4

from table_review.documents.types import SourceReference
from table_review.tables.context import ContextIndex
from table_review.tables.context_types import ContextBinding, ContextSource
from table_review.tables.types import CellContent, TableContent


def subtotal_table(*, confirmed=True, units="USD millions"):
    ref = SourceReference(page=2, precision="page")
    cells = []
    for row, pair in enumerate(
        (("Metric", "2025"), ("Revenue", "10.10"), ("Expense", "(2.05)"), ("Total", "8.05"))
    ):
        for col, text in enumerate(pair):
            cells.append(
                CellContent(
                    id=f"r{row}c{col}",
                    row=row,
                    col=col,
                    text=text,
                    raw_text=text,
                    role="column_header" if row == 0 else "row_header" if col == 0 else "data",
                    reference=ref,
                )
            )
    content = TableContent(
        rows=4,
        columns=2,
        cells=tuple(cells),
        header_rows=(0,),
        row_label_columns=(0,),
        source_reference=ref,
        context_sources=(
            ContextSource(
                id="caption:unit",
                text=units,
                raw_text=units,
                references=(ref,),
                origin="extracted",
            ),
        ),
    )
    if not confirmed:
        return content
    index = ContextIndex(content)
    actor, now = uuid4(), datetime.now(UTC)
    bindings = []
    for cell in ("r1c1", "r2c1", "r3c1"):
        for candidate in index.cell(cell).proposals:
            if candidate.kind in ("period", "currency", "scale"):
                bindings.append(
                    ContextBinding(
                        **candidate.model_dump(), cell_id=cell, confirmed_by=actor, confirmed_at=now
                    )
                )
    return content.model_copy(update={"context_bindings": tuple(bindings)})

from table_review.documents.types import SourceReference
from table_review.tables.types import CellContent, TableContent


def simple_table(rows=3, columns=3):
    return TableContent(
        rows=rows,
        columns=columns,
        cells=tuple(
            CellContent(
                id=f"r{r}c{c}",
                row=r,
                col=c,
                raw_text=f"{r}:{c}",
                text=f"{r}:{c}",
                reference=SourceReference(page=2, precision="page"),
            )
            for r in range(rows)
            for c in range(columns)
        ),
        unit_notes=("USD millions",),
    )

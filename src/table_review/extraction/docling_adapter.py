import hashlib
import importlib.metadata
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psutil
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from table_review.documents.pdf import inspect_pdf, page_info, source_tokens
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import ExtractedTable, ExtractionRequest, ExtractionResult
from table_review.tables.types import CellContent, TableContent, infer_header_roles


@lru_cache(maxsize=4)
def model_hashes(root: str):
    directory = Path(root)
    files = [
        file
        for file in directory.rglob("*")
        if file.is_file()
        and ".cache" not in file.parts
        and file.suffix in {".json", ".safetensors", ".onnx", ".pt", ".bin"}
    ]
    if not files:
        raise ValueError("Extraction models must be downloaded before starting the worker")
    hashes = {}
    for file in sorted(files):
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        hashes[file.relative_to(directory).as_posix()] = digest
    return hashes


def _vector_page(reader, page, region, destination):
    """Clip in vector space after making rotation explicit; map back with translation."""
    source = reader.pages[page.page_no - 1]
    source.transfer_rotation_to_content()
    left, bottom, right, top = map(float, source.cropbox)
    if region:
        if (
            abs(region.page_width - page.width) > 0.01
            or abs(region.page_height - page.height) > 0.01
        ):
            raise ValueError("Region dimensions do not match the source page")
        left, right = left + region.left, left + region.right
        bottom, top = top - region.bottom, top - region.top
    width, height = right - left, top - bottom
    if width <= 0 or height <= 0:
        raise ValueError("Region must have a positive area")
    source.cropbox = RectangleObject((left, bottom, right, top))
    writer = PdfWriter()
    target = writer.add_blank_page(width=width, height=height)
    target.merge_transformed_page(source, Transformation().translate(-left, -bottom))
    writer.write(destination)
    return width, height, region.left if region else 0, region.top if region else 0


class DoclingExtractor:
    def __init__(self):
        self._converters = {}

    def _converter(self, config):
        key = config.model_dump_json()
        if key not in self._converters:
            from docling.datamodel.accelerator_options import (
                AcceleratorDevice,
                AcceleratorOptions,
            )
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption

            from table_review.extraction.visible_backend import VisiblePdfBackend

            options = PdfPipelineOptions(
                artifacts_path=config.artifacts_path.resolve(),
                do_ocr=False,
                do_table_structure=True,
                do_code_enrichment=False,
                do_formula_enrichment=False,
                enable_remote_services=False,
                allow_external_plugins=False,
                accelerator_options=AcceleratorOptions(
                    device=AcceleratorDevice.CPU, num_threads=config.num_threads
                ),
            )
            options.table_structure_options.mode = TableFormerMode.ACCURATE
            options.table_structure_options.do_cell_matching = config.cell_matching
            self._converters[key] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=options, backend=VisiblePdfBackend
                    ),
                }
            )
        return self._converters[key]

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        info = inspect_pdf(request.source_path)
        if info.encrypted:
            raise ValueError("Encrypted PDFs are unsupported")
        if max(request.pages) > info.page_count:
            raise ValueError("Selected page is outside this PDF")
        hashes = model_hashes(str(request.config.artifacts_path.resolve()))
        source_hash = hashlib.sha256(request.source_path.read_bytes()).hexdigest()
        pages = tuple(page_info(request.source_path, page) for page in request.pages)
        if any(not page.digital_text_present for page in pages):
            raise ValueError("Selected page has no usable digital text; OCR is unsupported")
        started = time.monotonic()
        process = psutil.Process()
        peak = [process.memory_info().rss]
        stop = threading.Event()

        def monitor():
            while not stop.wait(0.05):
                peak[0] = max(peak[0], process.memory_info().rss)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        warnings, tables, transforms = [], [], []
        try:
            converter = self._converter(request.config)
            reader = PdfReader(request.source_path)
            for page in pages:
                tokens = source_tokens(request.source_path, page.page_no)
                pdf_page = reader.pages[page.page_no - 1]
                ordinary = pdf_page.rotation == 0 and list(pdf_page.cropbox) == list(
                    pdf_page.mediabox
                )
                if ordinary and request.region is None:
                    backend_page = page.page_no
                    w, h, offset_left, offset_top = page.width, page.height, 0, 0
                    result = converter.convert(
                        request.source_path, page_range=(backend_page, backend_page)
                    )
                else:
                    backend_page = 1
                    with tempfile.TemporaryDirectory() as temporary:
                        normalized = Path(temporary) / "selected.pdf"
                        w, h, offset_left, offset_top = _vector_page(
                            reader, page, request.region, normalized
                        )
                        result = converter.convert(normalized, page_range=(1, 1))
                    transforms.append(
                        {
                            "original_page": page.page_no,
                            "backend_page": 1,
                            "offset_left": offset_left,
                            "offset_top": offset_top,
                            "width": w,
                            "height": h,
                            "kind": "vector_clip_after_rotation",
                        }
                    )
                doc_page = result.document.pages.get(backend_page)
                geometry_known = (
                    doc_page is not None
                    and abs(doc_page.size.width - w) < 0.1
                    and abs(doc_page.size.height - h) < 0.1
                )
                if not geometry_known:
                    warnings.append(f"Page {page.page_no}: extractor geometry is coarse")

                def canonical(
                    box,
                    page=page,
                    geometry_known=geometry_known,
                    h=h,
                    offset_left=offset_left,
                    offset_top=offset_top,
                ):
                    if box is None or not geometry_known:
                        return None
                    box = box.to_top_left_origin(h)
                    try:
                        return PageBox(
                            page=page.page_no,
                            left=box.l + offset_left,
                            top=box.t + offset_top,
                            right=box.r + offset_left,
                            bottom=box.b + offset_top,
                            page_width=page.width,
                            page_height=page.height,
                        )
                    except ValueError:
                        return None

                for index, table in enumerate(result.document.tables):
                    if not table.prov or table.prov[0].page_no != backend_page:
                        raise ValueError("Backend did not preserve original page numbering")
                    table_id = str(uuid5(NAMESPACE_URL, f"{source_hash}:{page.page_no}:{index}"))
                    region = canonical(table.prov[0].bbox)
                    cells = []
                    for ci, cell in enumerate(table.data.table_cells):
                        box = canonical(cell.bbox)
                        reference = SourceReference(
                            page=page.page_no,
                            precision="cell" if box else "table" if region else "page",
                            box=box or region,
                        )
                        role = (
                            "column_header"
                            if cell.column_header
                            else "row_header"
                            if cell.row_header
                            else "section"
                            if cell.row_section
                            else "data"
                        )
                        cells.append(
                            CellContent(
                                id=str(uuid5(NAMESPACE_URL, f"{table_id}:{ci}")),
                                row=cell.start_row_offset_idx,
                                col=cell.start_col_offset_idx,
                                row_span=cell.row_span,
                                col_span=cell.col_span,
                                raw_text=cell.text,
                                text=cell.text,
                                role=role,
                                reference=reference,
                            )
                        )
                    table_tokens = tuple(
                        t
                        for t in tokens
                        if region is None
                        or (
                            t.box.left < region.right
                            and t.box.right > region.left
                            and t.box.top < region.bottom
                            and t.box.bottom > region.top
                        )
                    )
                    tables.append(
                        ExtractedTable(
                            id=table_id,
                            page_no=page.page_no,
                            region=region,
                            content=TableContent(
                                rows=table.data.num_rows,
                                columns=table.data.num_cols,
                                cells=tuple(cells),
                                header_rows=infer_header_roles(tuple(cells))[0],
                                row_label_columns=infer_header_roles(tuple(cells))[1],
                                source_reference=SourceReference(
                                    page=page.page_no,
                                    precision="table" if region else "page",
                                    box=region,
                                ),
                            ),
                            source_tokens=table_tokens,
                        )
                    )
                if not result.document.tables:
                    warnings.append(f"Page {page.page_no}: no tables detected")
        finally:
            stop.set()
            thread.join()
            peak[0] = max(peak[0], process.memory_info().rss)
        versions = {
            name: importlib.metadata.version(name)
            for name in ("docling", "docling-core", "docling-ibm-models", "torch", "pypdfium2")
        }
        return ExtractionResult(
            tables=tuple(tables),
            page_info=pages,
            warnings=tuple(warnings),
            manifest={
                "source_sha256": source_hash,
                "device": "cpu",
                "config": request.config.model_dump(mode="json"),
                "model_artifact_hashes": hashes,
                "versions": versions,
                "elapsed_seconds": time.monotonic() - started,
                "peak_rss_bytes": peak[0],
                "pages": request.pages,
                "ocr": False,
                "text_visibility_policy": "pdf_rendering_modes_3_and_7_excluded_v1",
                "remote_services": False,
                "coordinate_transforms": transforms,
                "region_transform": transforms[0] if request.region and transforms else None,
                "request_region": request.region.model_dump() if request.region else None,
            },
        )

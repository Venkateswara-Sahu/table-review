# Evaluation

This directory records frozen extraction and check evaluations for Table Review. The JSON files are recorded results, not a redistributable corpus. The source PDFs, page renderings and crops are excluded from Git because their redistribution licences were not verified.

## What is measured

- Literal recovery and placement of table values.
- Association of values with headers, periods, currency, scale and measure.
- Source geometry and exact table match.
- Check-engine behaviour on frozen cases.

## Data

- Development and held-out annual-report pages.
- A frozen 40-region evaluation for literal, placement, association and geometry.
- Source-assisted agent annotation, not independent human gold.
- `corpus-metadata.json` records the source references and hashes; the original PDFs stay outside the repository.

## Reproduce

The manifest (`evaluation/corpus.json`) and source PDFs are local-only. With both present, run:

```powershell
uv run --frozen --extra extraction python evaluation/run.py --manifest evaluation/corpus.json --output artifacts/evaluation --split development --mode full_page --matching on
```

Repeat with `--split heldout` for the held-out split and `--mode crop` or `--matching off` for the comparison variants.

## Files

| File | Purpose |
|---|---|
| `2026-10-18-v10-extraction.json` | Current extraction evaluation and comparison |
| `2026-10-18-v10-release.json` | Release verification record |
| `2026-10-17-product-v8-extraction.json` | Earlier frozen extraction results |
| `2026-10-18-v9-ocr.json` | OCR comparison against v8 |
| `checks-development.json` | Check-engine development cases |
| `corpus-metadata.json` | Report references, hashes and split metadata |
| `annotation-layout.json` | Frozen region layout for the 40-region set |
| `results-development-v1.json`, `results-heldout-v1.json` | Baseline result summaries |

## Limits

- The reference is source-assisted annotation, not an independent human gold standard.
- Results obtained after inspecting these reports are validation on known examples, not a general accuracy claim.
- No time-savings, adoption or hosted-reliability claim follows from these numbers.
- Automatic extraction remains fallible; every imported figure needs review against the source image.

"""Preload bundled OCR and a checksum-pinned English recognizer before offline runtime."""

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

import rapidocr

from table_review.runtime import write_model_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, default=Path("models"))
    args = parser.parse_args()
    source = Path(rapidocr.__file__).parent / "models"
    target = args.models / "rapidocr"
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "PP-OCRv6_det_small.onnx",
        "PP-OCRv6_rec_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    ):
        shutil.copyfile(source / name, target / name)
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "infra/ocr-mark-model.json").read_text()
    )
    english = target / manifest["name"]
    if (
        not english.is_file()
        or hashlib.sha256(english.read_bytes()).hexdigest() != manifest["sha256"]
    ):
        temporary = english.with_suffix(".download")
        try:
            with (
                urllib.request.urlopen(manifest["url"], timeout=60) as response,
                temporary.open("wb") as output,
            ):
                size = 0
                while chunk := response.read(65536):
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise ValueError("OCR model exceeds preload budget")
                    output.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != manifest["sha256"]:
                raise ValueError("OCR model differs from the pinned registry checksum")
            temporary.replace(english)
        finally:
            temporary.unlink(missing_ok=True)
    write_model_manifest(args.models)
    print("OCR checkpoints preloaded and verified; runtime uses local files only.")


if __name__ == "__main__":
    main()

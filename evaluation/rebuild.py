"""Rebuild local reference text from frozen public metadata and original PDFs.

Originals and full transcriptions remain local. This command refuses changed
sources, changed layout/tooling and altered reference content; it never tunes.
"""

import argparse
import hashlib
import json
from pathlib import Path

from evaluation.annotation import transcribe, visible_text_copy
from evaluation.manifest import CaseTruth, CorpusManifest, payload_digest


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rebuild(metadata_path: Path, output: Path):
    metadata_path = metadata_path.resolve()
    base = metadata_path.parent
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    layout_path = base / "annotation-layout.json"
    if file_digest(layout_path) != metadata["annotation_provenance"]["layout_sha256"]:
        raise ValueError("Frozen annotation layout changed")
    if (
        file_digest(Path(__file__).with_name("annotation.py"))
        != metadata["annotation_provenance"]["tool_sha256"]
    ):
        raise ValueError("Frozen annotation tooling changed")
    layouts = json.loads(layout_path.read_text(encoding="utf-8"))
    for report in metadata["reports"]:
        source = (base / report["source_path"]).resolve()
        if file_digest(source) != report["sha256"]:
            raise ValueError("Frozen source hash differs")
        if report["id"].startswith("intel"):
            destination = (
                base
                / next(
                    row["annotation_source_path"]
                    for row in layouts
                    if row["report_id"] == report["id"]
                )
            ).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            visible_text_copy(source, destination)
    cases = []
    for reference in metadata["cases"]:
        layout = next(row for row in layouts if row["id"] == reference["id"])
        if layout["source_audit"] != "agent_layout_and_transcription_audit":
            raise ValueError("An annotation has not passed the declared source audit")
        content, audit = transcribe(layout, (base / layout["annotation_source_path"]).resolve())
        digest = hashlib.sha256(content.model_dump_json().encode("utf-8")).hexdigest()
        if digest != reference["content_sha256"] or audit != reference["token_audit"]:
            raise ValueError("Rebuilt source annotation differs from its frozen reference")
        cases.append(
            CaseTruth.model_validate(
                {
                    **{
                        k: v
                        for k, v in reference.items()
                        if k not in ("content_sha256", "token_audit")
                    },
                    "content": content,
                }
            )
        )
    corpus = CorpusManifest.model_validate(
        {
            **{k: v for k, v in metadata.items() if k not in ("cases", "metadata_version")},
            "cases": cases,
        }
    )
    data = corpus.model_dump(mode="json")
    if payload_digest(data) != corpus.frozen_payload_sha256:
        raise ValueError("Rebuilt complete corpus differs from its frozen hash")
    serialized = corpus.model_dump_json(indent=2)
    if output.exists():
        if output.read_text(encoding="utf-8") != serialized:
            raise ValueError("Refusing to overwrite a different local corpus")
        return corpus
    output.write_text(serialized, encoding="utf-8")
    return corpus


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=Path("evaluation/corpus-metadata.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/corpus.json"))
    args = parser.parse_args()
    corpus = rebuild(args.metadata, args.output)
    print(
        json.dumps(
            {"cases": len(corpus.cases), "frozen_payload_sha256": corpus.frozen_payload_sha256}
        )
    )


if __name__ == "__main__":
    main()

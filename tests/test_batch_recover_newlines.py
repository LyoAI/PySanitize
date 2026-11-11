"""Batch recovery with --source-root: line breaks lost by a pre-fix sanitize.

The batch mirrors the input tree, so a job dir tells us which original file
backs its redacted.json / redacted.xlsx. Handing that file to recovery lets
it put back newlines the old redactor flattened — no re-sanitizing.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "batch_recover_script", ROOT / "scripts" / "batch_recover.py"
)
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


def test_source_for_mirrors_the_batch_layout(tmp_path: Path):
    in_root = tmp_path / "upload"
    (in_root / "a").mkdir(parents=True)
    (in_root / "a" / "report.json").write_text("{}", encoding="utf-8")
    out_root = tmp_path / "output"
    artifact = out_root / "a" / "report" / "redacted.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")

    assert br.source_for(artifact, out_root, in_root) == in_root / "a" / "report.json"


def test_source_for_handles_a_disambiguated_job_dir(tmp_path: Path):
    """report.pdf claims ``report/``; report.json gets ``report-json/``."""
    in_root = tmp_path / "upload"
    (in_root / "a").mkdir(parents=True)
    (in_root / "a" / "report.json").write_text("{}", encoding="utf-8")
    (in_root / "a" / "report.pdf").write_bytes(b"%PDF")
    artifact = tmp_path / "output" / "a" / "report-json" / "redacted.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")

    assert br.source_for(artifact, tmp_path / "output", in_root) == in_root / "a" / "report.json"


def test_source_for_ignores_documents_and_missing_sources(tmp_path: Path):
    in_root = tmp_path / "upload"
    in_root.mkdir()
    out_root = tmp_path / "output"

    md = out_root / "doc" / "sanitized.md"
    md.parent.mkdir(parents=True)
    md.write_text("x", encoding="utf-8")
    assert br.source_for(md, out_root, in_root) is None  # not a structured artifact

    json_artifact = out_root / "gone" / "redacted.json"
    json_artifact.parent.mkdir(parents=True)
    json_artifact.write_text("{}", encoding="utf-8")
    assert br.source_for(json_artifact, out_root, in_root) is None  # source absent


def test_batch_recover_restores_newlines_from_source_root(tmp_path: Path):
    from pysanitize.pipeline import sanitize_document

    in_root = tmp_path / "upload"
    in_root.mkdir()
    src = in_root / "report.json"
    src.write_text(
        json.dumps(
            {"员工": [{"备注": "第一行\n第二行 电话 13800001111"}]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    out_root = tmp_path / "output"
    sanitize_document(
        src, detector="rules", recoverable=True, recover_key="pw",
        out_dir=out_root / "report",
    )

    # Simulate the pre-fix artifact: the detection surface was written back.
    redacted = out_root / "report" / "redacted.json"
    data = json.loads(redacted.read_text(encoding="utf-8"))
    data["员工"][0]["备注"] = data["员工"][0]["备注"].replace("\n", " ")
    redacted.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert br.main([str(out_root), "--source-root", str(in_root)]) == 0
    recovered = out_root / "report" / "redacted_recovered.json"
    assert json.loads(recovered.read_text(encoding="utf-8")) == json.loads(
        src.read_text(encoding="utf-8")
    )

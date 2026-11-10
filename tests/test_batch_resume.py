"""Batch resume: completed files are skipped, interrupted runs are resumable."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "batch_sanitize_script", ROOT / "scripts" / "batch_sanitize.py"
)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)

JSON_BODY = {"姓名": "张三", "手机号": "13812345678"}


def _json(src: Path, name: str) -> Path:
    src.mkdir(parents=True, exist_ok=True)
    p = src / name
    p.write_text(json.dumps(JSON_BODY, ensure_ascii=False), encoding="utf-8")
    return p


def _done_job(job: Path, *, artifact: str | None = "redacted.json") -> Path:
    job.mkdir(parents=True, exist_ok=True)
    if artifact:
        (job / artifact).write_text("{}", encoding="utf-8")
    (job / "audit.json").write_text(
        json.dumps({"redaction": {"json": artifact}}), encoding="utf-8"
    )
    return job


def test_is_done_requires_audit(tmp_path: Path):
    src = _json(tmp_path / "in", "a.json")
    assert not bs.is_done(src, tmp_path / "out" / "a")


def test_is_done_requires_the_artifact_the_audit_names(tmp_path: Path):
    src = _json(tmp_path / "in", "a.json")
    job = tmp_path / "out" / "a"
    _done_job(job)
    (job / "redacted.json").unlink()  # deleted / truncated after the run
    assert not bs.is_done(src, job)


def test_is_done_tolerates_half_written_audit(tmp_path: Path):
    src = _json(tmp_path / "in", "a.json")
    job = tmp_path / "out" / "a"
    job.mkdir(parents=True)
    (job / "redacted.json").write_text("{}", encoding="utf-8")
    (job / "audit.json").write_text('{"redaction": ', encoding="utf-8")  # killed mid-write
    assert not bs.is_done(src, job)


def test_is_done_requires_markdown_for_document_sources(tmp_path: Path):
    src = tmp_path / "in" / "a.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"%PDF-1.4")
    job = tmp_path / "out" / "a"
    job.mkdir(parents=True)
    (job / "audit.json").write_text('{"redaction": {"pdf": null}}', encoding="utf-8")
    assert not bs.is_done(src, job)  # audit present, sanitized.md missing
    (job / "sanitized.md").write_text("# ok", encoding="utf-8")
    assert bs.is_done(src, job)


def test_rerun_skips_completed_files(tmp_path: Path):
    src, out = tmp_path / "in", tmp_path / "out"
    _json(src, "a.json")
    _json(src, "b.json")
    assert bs.main([str(src), "-o", str(out)]) == 0
    first = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    assert first["status"] == "complete"
    assert first["totals"]["sanitized"] == 2

    mtime = (out / "a" / "redacted.json").stat().st_mtime_ns
    assert bs.main([str(src), "-o", str(out)]) == 0
    second = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    assert second["totals"]["skipped"] == 2 and second["totals"]["sanitized"] == 0
    assert (out / "a" / "redacted.json").stat().st_mtime_ns == mtime  # untouched


def test_interrupted_run_is_recorded_and_resumes(tmp_path: Path, monkeypatch, capsys):
    src, out = tmp_path / "in", tmp_path / "out"
    _json(src, "a.json")
    _json(src, "b.json")

    calls = {"n": 0}

    def stub(src_path, job_dir, args):
        calls["n"] += 1
        if calls["n"] == 2:  # Ctrl-C lands inside the second file
            raise KeyboardInterrupt
        _done_job(job_dir)

        class R:
            redacted_json = Path("redacted.json")
            redacted_xlsx = redacted_pdf = sanitized_md = None
            detections: list = []

        return R()

    monkeypatch.setattr(bs, "run_one", stub)
    assert bs.main([str(src), "-o", str(out)]) == 130
    summary = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "interrupted"
    statuses = [r["status"] for r in summary["files"]]
    assert statuses == ["sanitized", "interrupted"]
    printed = capsys.readouterr().out
    assert "rerunning the same command" in printed

    # Rerun for real (no stub): the finished file is skipped, the interrupted
    # one is redone and completes.
    monkeypatch.undo()
    assert bs.main([str(src), "-o", str(out)]) == 0
    summary = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "complete"
    assert summary["totals"]["skipped"] == 1 and summary["totals"]["sanitized"] == 1
    assert (out / "b" / "audit.json").is_file()


def test_explicit_resume_flag_is_accepted_and_skips(tmp_path: Path, monkeypatch):
    src, out = tmp_path / "in", tmp_path / "out"
    _json(src, "a.json")
    ran: list[str] = []

    def stub(src_path, job_dir, args):
        ran.append(src_path.name)
        _done_job(job_dir)

        class R:
            redacted_json = Path("redacted.json")
            redacted_xlsx = redacted_pdf = sanitized_md = None
            detections: list = []

        return R()

    monkeypatch.setattr(bs, "run_one", stub)
    assert bs.main([str(src), "-o", str(out), "--resume"]) == 0
    assert bs.main([str(src), "-o", str(out), "--resume"]) == 0
    assert ran == ["a.json"]


def test_resume_and_force_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        bs.parse_args(["in", "--resume", "--force"])
    assert exc.value.code == 2


def test_force_redoes_completed_files(tmp_path: Path, monkeypatch):
    src, out = tmp_path / "in", tmp_path / "out"
    _json(src, "a.json")
    ran: list[str] = []

    def stub(src_path, job_dir, args):
        ran.append(src_path.name)
        _done_job(job_dir)

        class R:
            redacted_json = Path("redacted.json")
            redacted_xlsx = redacted_pdf = sanitized_md = None
            detections: list = []

        return R()

    monkeypatch.setattr(bs, "run_one", stub)
    assert bs.main([str(src), "-o", str(out)]) == 0
    assert bs.main([str(src), "-o", str(out)]) == 0
    assert ran == ["a.json"]  # second run skipped
    assert bs.main([str(src), "-o", str(out), "--force"]) == 0
    assert ran == ["a.json", "a.json"]  # --force redid it

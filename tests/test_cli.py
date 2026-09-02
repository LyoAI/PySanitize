"""CLI: parser shape, version, launch flags, and pipeline wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from pysanitize import __version__
from pysanitize.cli import build_parser, main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    main([])
    out = capsys.readouterr().out
    assert "launch" in out


def test_legacy_sanitize_subcommand_is_stripped():
    """``pysanitize sanitize a.pdf …`` keeps working as an alias."""
    args = build_parser().parse_args(["a.pdf", "--detector", "hybrid"])
    legacy = build_parser().parse_args(
        main.__globals__["_strip_legacy_subcommand"](["sanitize", "a.pdf", "--detector", "hybrid"])
    )
    assert legacy.file == args.file == "a.pdf"
    assert legacy.detector == args.detector == "hybrid"


def test_parser_shape():
    args = build_parser().parse_args(
        ["a.pdf", "--detector", "hybrid", "--fields", "phone,person_name",
         "--mask-images", "--image-backend", "yunet", "--audit", "--redact-pdf",
         "--out-dir", "o"]
    )
    assert args.file == "a.pdf"
    assert args.detector == "hybrid"
    assert args.mask_images is True
    assert args.image_backend == "yunet"
    assert args.audit is True
    assert args.redact_pdf is True
    assert args.out_dir == "o"
    assert args.launch is None


def test_launch_flag_parsing():
    assert build_parser().parse_args(["--launch", "tui"]).launch == "tui"
    assert build_parser().parse_args(["--launch", "webui"]).launch == "webui"


def test_defaults_to_none_for_config():
    args = build_parser().parse_args(["a.pdf"])
    assert args.detector is None
    assert args.mask_images is None
    assert args.audit is None
    assert args.redact_pdf is None
    assert args.llm_provider is None


def test_image_class_and_text_flags_map_to_pipeline_params():
    from pysanitize.cli import _image_classes, _image_text_fields

    parse = build_parser().parse_args
    # bare --image-text = mask all printed text → the implicit `text` class,
    # field detection explicitly off (all-text subsumes it)
    a = parse(["a.pdf", "--mask-images", "--image-text"])
    assert _image_classes(a) == ["text"]
    assert _image_text_fields(a) == []
    # field list → field-driven masking only
    a = parse(["a.pdf", "--image-text", "company_name,phone"])
    assert _image_classes(a) is None
    assert _image_text_fields(a) == ["company_name", "phone"]
    # object classes stay separate; both can combine
    a = parse(["a.pdf", "--image-classes", "face,person", "--image-text"])
    assert _image_classes(a) == ["face", "person", "text"]
    # nothing given → config defaults hold (None sentinels)
    a = parse(["a.pdf"])
    assert _image_classes(a) is None
    assert _image_text_fields(a) is None


def test_provider_model_flags():
    args = build_parser().parse_args(
        ["a.pdf", "--provider", "openrouter", "--model", "deepseek-v4-flash"]
    )
    assert args.llm_provider == "openrouter"
    assert args.llm_model == "deepseek-v4-flash"


def test_launch_webui_reports_not_available(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--launch", "webui"])
    assert e.value.code == 1
    assert "tui" in capsys.readouterr().err.lower()


def test_sanitize_runs_pipeline(monkeypatch, capsys):
    from pysanitize.pipeline import SanitizeResult

    called = {}

    def fake_sanitize(doc_path, **kw):
        called.update(kw)
        return SanitizeResult(
            doc_id="doc", out_dir=Path("/tmp/x"), sanitized_md=Path("/tmp/x/sanitized.md"),
            audit_path=Path("/tmp/x/audit.json"), sensitive_report_path=None,
            detections=[], images_total=0, images_masked=0, duration_s=1.0,
            detector="rules", fields=["phone"],
        )

    monkeypatch.setattr("pysanitize.pipeline.sanitize_document", fake_sanitize)
    with pytest.raises(SystemExit) as e:
        main(["sanitize", "a.pdf", "--detector", "rules"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "sanitized.md" in out
    assert called["detector"] == "rules"
    assert called["mask_images"] is None  # absent flag → config decides
    assert called["fields"] is None


def test_sanitize_fields_parsing(monkeypatch, capsys):
    from pysanitize.pipeline import SanitizeResult

    called = {}

    def fake_sanitize(doc_path, **kw):
        called.update(kw)
        return SanitizeResult(
            doc_id="d", out_dir=Path("/x"), sanitized_md=Path("/x/sanitized.md"),
            audit_path=Path("/x/audit.json"), sensitive_report_path=None,
            detections=[], images_total=0, images_masked=0, duration_s=0.1,
            detector="rules", fields=kw.get("fields") or [],
        )

    monkeypatch.setattr("pysanitize.pipeline.sanitize_document", fake_sanitize)
    with pytest.raises(SystemExit):
        main(["sanitize", "a.pdf", "--fields", "phone, company_name"])
    assert called["fields"] == ["phone", "company_name"]


def test_sanitize_handles_missing_file(monkeypatch, capsys):
    from pysanitize.pipeline import SanitizeResult

    def boom(**kw):
        raise FileNotFoundError("no such document: /nope.pdf")

    monkeypatch.setattr("pysanitize.pipeline.sanitize_document", boom)
    with pytest.raises(SystemExit) as e:
        main(["sanitize", "/nope.pdf"])
    assert e.value.code == 1

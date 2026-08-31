"""Command-line interface for PySanitize.

Usage::

    pysanitize sample.pdf                              # rules, text only
    pysanitize sample.pdf --detector hybrid            # + LLM-located spans
    pysanitize sample.pdf --mask-images                # + face mosaic on images
    pysanitize sample.pdf --fields person_name,phone --audit
    pysanitize --launch tui                            # interactive TUI
    pysanitize sanitize sample.pdf                     # legacy alias, still works

Flags the user does not pass fall back to ``config/pipeline.yaml``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from pysanitize import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pysanitize",
        description="Multi-format document desensitization: MinerU parse + rules/LLM field detection + text/image masking",
        epilog=(
            "examples:\n"
            "  pysanitize sample.pdf --detector hybrid --fields phone,person_name\n"
            "  pysanitize sample.pdf --mask-images --image-classes face --audit\n"
            "  pysanitize --launch tui\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "file", nargs="?", metavar="<file>",
        help="input document: PDF / image / DOCX / PPTX / XLSX",
    )
    parser.add_argument(
        "--launch", choices=("tui", "webui"), metavar="<mode>",
        help="launch an interactive frontend instead of running the CLI",
    )
    parser.add_argument(
        "--detector",
        choices=("rules", "llm", "hybrid"),
        help="text detection mode (default from config/pipeline.yaml)",
    )
    parser.add_argument(
        "--fields",
        help="field types to detect, comma-separated (default: all enabled fields)",
    )
    parser.add_argument(
        "--model", dest="llm_model",
        help="LLM model = config/llm/<model>.yaml filename (when detector is llm/hybrid)",
    )
    parser.add_argument(
        "--provider", dest="llm_provider",
        help="LLM provider = provider section in config/llm/<model>.yaml (openai | pingan, default openai)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mask-images", dest="mask_images", action="store_true",
        help="enable image detection + mosaicing",
    )
    group.add_argument(
        "--no-mask-images", dest="mask_images", action="store_false",
        help="disable image masking (overrides config)",
    )
    parser.set_defaults(mask_images=None)
    parser.add_argument(
        "--image-classes",
        help=(
            "image targets to mask, comma-separated: face | text (OCR text regions) | <YOLO class> "
            "(e.g. person,car). Empty by default = --mask-images does nothing to images"
        ),
    )
    parser.add_argument(
        "--image-fields",
        help=(
            "field types to detect *inside* images, comma-separated (e.g. phone,company_name). "
            "Default = same as --fields; may be a superset"
        ),
    )
    parser.add_argument(
        "--image-backend", choices=("auto", "yunet", "haar", "yolo"),
        help="face backend when classes include face (default auto: YuNet, offline fallback Haar)",
    )
    parser.add_argument("--image-model", help="detection weights (YuNet onnx / YOLO .pt; auto-downloaded by default)")
    parser.add_argument("--mosaic-factor", type=int, help="mosaic block size (default 16)")
    parser.add_argument("--score-threshold", type=float, help="detection confidence threshold (default 0.5)")
    ga = parser.add_mutually_exclusive_group()
    ga.add_argument("--audit", dest="audit", action="store_true",
                    help="also write sensitive_report.json with raw values (local audit)")
    ga.add_argument("--no-audit", dest="audit", action="store_false")
    parser.set_defaults(audit=None)
    rg = parser.add_mutually_exclusive_group()
    rg.add_argument("--redact-pdf", dest="redact_pdf", action="store_true",
                    help="write redacted.pdf for PDF inputs (default from config)")
    rg.add_argument("--no-redact-pdf", dest="redact_pdf", action="store_false",
                    help="skip redacted.pdf")
    parser.set_defaults(redact_pdf=None)
    parser.add_argument(
        "--redaction-style", choices=("mosaic", "block"),
        help="PDF redaction style (default mosaic)",
    )
    parser.add_argument("--out-dir", help="output directory (default output/<doc-name>/)")
    parser.add_argument("--parse-backend", help="MinerU backend (pipeline/vlm-engine/hybrid-engine)")
    parser.add_argument("--lang", default="ch", help="OCR language (default ch)")
    verb = parser.add_mutually_exclusive_group()
    verb.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    verb.add_argument("-q", "--quiet", action="store_true", help="WARNING+ logging only")
    return parser


def _strip_legacy_subcommand(argv: list[str] | None) -> list[str] | None:
    """Accept the pre-0.3 ``pysanitize sanitize <file> …`` form as an alias.

    The ``sanitize`` verb was the 0.2 subcommand; 0.3 promotes ``<file>`` to
    the top level, so a leading ``sanitize`` is simply dropped.
    """
    if argv and argv[0] == "sanitize":
        return argv[1:]
    return argv


def _set_log_level(args: argparse.Namespace) -> None:
    level = (
        "DEBUG" if getattr(args, "verbose", False)
        else "WARNING" if getattr(args, "quiet", False)
        else None
    )
    if level is None:
        return
    logger = logging.getLogger("PySanitize")
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


def _split_fields(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [f.strip() for f in raw.split(",") if f.strip()]


def _run_sanitize(args: argparse.Namespace) -> int:
    from pysanitize.pipeline import sanitize_document  # lazy: heavy imports

    result = sanitize_document(
        args.file,
        detector=args.detector,
        fields=_split_fields(args.fields),
        llm_model=args.llm_model,
        llm_provider=args.llm_provider,
        mask_images=args.mask_images,
        image_classes=_split_fields(args.image_classes),
        image_fields=_split_fields(args.image_fields),
        image_backend=args.image_backend,
        image_model_path=args.image_model,
        mosaic_factor=args.mosaic_factor,
        score_threshold=args.score_threshold,
        redact_pdf=args.redact_pdf,
        redaction_style=args.redaction_style,
        audit=args.audit,
        out_dir=args.out_dir,
        parse_backend=args.parse_backend,
        lang=args.lang,
    )
    print(f"Output directory: {result.out_dir}")
    print(f"  Sanitized document: {result.sanitized_md}")
    print(
        f"  Text masks {len(result.detections)} · images mosaiced "
        f"{result.images_masked}/{result.images_total} · took {result.duration_s:.1f}s"
    )
    if result.redacted_pdf:
        print(f"  Redacted PDF: {result.redacted_pdf}")
    print(f"  Audit report: {result.audit_path}")
    if result.sensitive_report_path:
        print(f"  Sensitive-value report: {result.sensitive_report_path}")
    return 0


def _launch_tui() -> int:
    """Start the Textual app; degrade to an install hint when it's absent."""
    try:
        from pysanitize.tui import PySanitizeApp
    except ImportError:
        print(
            "TUI mode requires the 'tui' extra. Install it with:\n"
            "  uv sync --extra tui        # (or: pip install pysanitize[tui])",
            file=sys.stderr,
        )
        return 1
    PySanitizeApp().run()
    return 0


def _launch_webui() -> int:
    print(
        "WebUI is planned for M3 and not available yet — use --launch tui "
        "for the interactive frontend.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> None:
    # Resolve sys.argv here so the legacy shim sees console-script calls too.
    argv = _strip_legacy_subcommand(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.launch:
        handlers: dict[str, Any] = {"tui": _launch_tui, "webui": _launch_webui}
        raise SystemExit(handlers[args.launch]())
    if not args.file:
        parser.print_help()
        return

    _set_log_level(args)
    try:
        rc = _run_sanitize(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        rc = 130
    except Exception as e:
        logging.getLogger("PySanitize").error("%s", e)
        rc = 1
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

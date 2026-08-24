"""Command-line interface for PySanitize.

Usage::

    pysanitize sanitize sample.pdf                     # rules, text only
    pysanitize sanitize sample.pdf --detector hybrid   # + LLM-located spans
    pysanitize sanitize sample.pdf --mask-images       # + face mosaic on images
    pysanitize sanitize sample.pdf --fields person_name,phone --audit

Flags the user does not pass fall back to ``config/pipeline.yaml``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from pysanitize import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pysanitize",
        description="Multi-format document desensitization: MinerU parse + rules/LLM field detection + text/image masking",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sp = sub.add_parser(
        "sanitize",
        help="run the full desensitization pipeline on one document",
        description=(
            "Parse the document -> locate sensitive fields -> mask text (+ optional image "
            "mosaicing) -> write sanitized.md / images_masked/ / audit.json"
        ),
    )
    sp.add_argument("file", help="input document: PDF / image / DOCX / PPTX / XLSX")
    sp.add_argument(
        "--detector",
        choices=("rules", "llm", "hybrid"),
        help="text detection mode (default from config/pipeline.yaml)",
    )
    sp.add_argument(
        "--fields",
        help="field types to detect, comma-separated (default: all enabled fields)",
    )
    sp.add_argument(
        "--model", dest="llm_model",
        help="LLM model = config/llm/<model>.yaml filename (when detector is llm/hybrid)",
    )
    sp.add_argument(
        "--provider", dest="llm_provider",
        help="LLM provider = provider section in config/llm/<model>.yaml (openai | pingan, default openai)",
    )
    group = sp.add_mutually_exclusive_group()
    group.add_argument(
        "--mask-images", dest="mask_images", action="store_true",
        help="enable image detection + mosaicing",
    )
    group.add_argument(
        "--no-mask-images", dest="mask_images", action="store_false",
        help="disable image masking (overrides config)",
    )
    sp.set_defaults(mask_images=None)
    sp.add_argument(
        "--image-classes",
        help=(
            "image targets to mask, comma-separated: face | text (OCR text regions) | <YOLO class> "
            "(e.g. person,car). Empty by default = --mask-images does nothing to images"
        ),
    )
    sp.add_argument(
        "--image-backend", choices=("auto", "yunet", "haar", "yolo"),
        help="face backend when classes include face (default auto: YuNet, offline fallback Haar)",
    )
    sp.add_argument("--image-model", help="detection weights (YuNet onnx / YOLO .pt; auto-downloaded by default)")
    sp.add_argument("--mosaic-factor", type=int, help="mosaic block size (default 16)")
    sp.add_argument("--score-threshold", type=float, help="detection confidence threshold (default 0.5)")
    ga = sp.add_mutually_exclusive_group()
    ga.add_argument("--audit", dest="audit", action="store_true",
                    help="also write sensitive_report.json with raw values (local audit)")
    ga.add_argument("--no-audit", dest="audit", action="store_false")
    sp.set_defaults(audit=None)
    sp.add_argument("--out-dir", help="output directory (default output/<doc-name>/)")
    sp.add_argument("--parse-backend", help="MinerU backend (pipeline/vlm-engine/hybrid-engine)")
    sp.add_argument("--lang", default="ch", help="OCR language (default ch)")
    verb = sp.add_mutually_exclusive_group()
    verb.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    verb.add_argument("-q", "--quiet", action="store_true", help="WARNING+ logging only")
    return parser


def _set_log_level(args) -> None:
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


def _run_sanitize(args) -> int:
    from pysanitize.pipeline import sanitize_document  # lazy: heavy imports

    result = sanitize_document(
        args.file,
        detector=args.detector,
        fields=_split_fields(args.fields),
        llm_model=args.llm_model,
        llm_provider=args.llm_provider,
        mask_images=args.mask_images,
        image_classes=_split_fields(args.image_classes),
        image_backend=args.image_backend,
        image_model_path=args.image_model,
        mosaic_factor=args.mosaic_factor,
        score_threshold=args.score_threshold,
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
    print(f"  Audit report: {result.audit_path}")
    if result.sensitive_report_path:
        print(f"  Sensitive-value report: {result.sensitive_report_path}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
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

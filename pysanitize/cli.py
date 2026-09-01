"""Command-line interface for PySanitize.

Usage::

    pysanitize sample.pdf                              # rules, text only
    pysanitize sample.pdf --detector hybrid            # + LLM-located spans
    pysanitize sample.pdf --mask-images                # + face mosaic on images
    pysanitize sample.pdf --fields person_name,phone --audit
    pysanitize sample.pdf --recoverable                # reversible masking
    pysanitize output/sample/sanitized.md --recover    # restore the original
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
            "  pysanitize sample.pdf --redact-pdf --redaction-style block\n"
            "  pysanitize sample.pdf --recoverable        # reversible tokens\n"
            "  pysanitize output/sample/sanitized.md --recover\n"
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
    parser.add_argument(
        "--mask-images", dest="mask_images", action="store_true",
        help="enable image detection + mosaicing (off by default)",
    )
    parser.set_defaults(mask_images=None)
    parser.add_argument(
        "--image-classes", dest="image_classes",
        help=(
            "object classes to mask, comma-separated: face | <YOLO class> (e.g. "
            "person,car) — detection models. Empty by default = --mask-images "
            "does nothing to images"
        ),
    )
    parser.add_argument(
        "--image-text", dest="image_text", nargs="?", const="all", default=None,
        metavar="[<field-list>]",
        help=(
            "mask text inside images (OCR): bare = all printed text; or a "
            "comma-separated field list (e.g. company_name,phone) to mask only "
            "matching fields. Default = no text masking"
        ),
    )
    parser.add_argument(
        "--image-backend", choices=("auto", "yunet", "haar", "yolo"),
        help=(
            "object detector backend (auto: YuNet for face, offline fallback "
            "Haar; every other class uses YOLO — custom weights via --image-model)"
        ),
    )
    parser.add_argument("--image-model", help="detection weights (YuNet onnx / YOLO .pt; auto-downloaded by default)")
    parser.add_argument("--mosaic-factor", type=int, help="mosaic block size (default 16)")
    parser.add_argument("--score-threshold", type=float, help="detection confidence threshold (default 0.5)")
    parser.add_argument("--audit", dest="audit", action="store_true",
                        help="also write sensitive_report.json with raw values (local audit)")
    parser.set_defaults(audit=None)
    parser.add_argument("--recoverable", dest="recoverable", action="store_true",
                        help=(
                            "make the run reversible: the document keeps its "
                            "normal placeholder while audit.json records each "
                            "value's ciphertext + position, so --recover can "
                            "restore it (needs the 'recover' extra)"
                        ))
    parser.set_defaults(recoverable=None)
    parser.add_argument(
        "--recover-key", dest="recover_key",
        help=(
            "recovery passphrase (otherwise PYSANITIZE_RECOVER_KEY env, or a "
            "generated one); whichever way, the effective key is stored in "
            ".recover.key beside the audit — --recover only reads that file; "
            "avoid this flag in shared shells — the value lands in history)"
        ),
    )
    parser.add_argument("--recover", dest="recover", action="store_true",
                        help=(
                            "restore a sanitized document: <file> is the "
                            "sanitized.md / redacted.pdf, audit.json must sit "
                            "beside it (or pass --recover-audit)"
                        ))
    parser.add_argument(
        "--recover-audit", dest="recover_audit",
        help="audit.json to recover from (default: audit.json next to <file>)",
    )
    parser.add_argument("--redact-pdf", dest="redact_pdf", action="store_true",
                        help="also write a layout-preserving redacted.pdf for PDF inputs (off by default)")
    parser.set_defaults(redact_pdf=None)
    parser.add_argument(
        "--redaction-style", choices=("mosaic", "block"),
        help="PDF redaction style (default mosaic)",
    )
    parser.add_argument("--out-dir", help="output directory (default output/<doc-name>/)")
    parser.add_argument(
        "--mineru-backend", dest="mineru_backend",
        help="MinerU backend (pipeline/vlm-engine/hybrid-engine; default MINERU_BACKEND from .env)",
    )
    parser.add_argument(
        "--mineru-out-dir", dest="mineru_out_dir",
        help=(
            "MinerU parse output root (default .cache/<source folder name>/ — "
            "same-named files in different folders stay separate)"
        ),
    )
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


def _image_classes(args: argparse.Namespace) -> list[str] | None:
    """``--image-classes`` list; bare ``--image-text`` contributes the ``text`` class."""
    classes = _split_fields(args.image_classes)
    if args.image_text == "all":
        classes = (classes or []) + ["text"]
    return classes


def _image_text_fields(args: argparse.Namespace) -> list[str] | None:
    """``--image-text`` → ``image_fields``: None (follow text fields) / [] (all-text mode) / field list."""
    if args.image_text is None:
        return None
    if args.image_text == "all":
        return []  # masking all text subsumes any field match
    return _split_fields(args.image_text)


def _run_sanitize(args: argparse.Namespace) -> int:
    from pysanitize.pipeline import sanitize_document  # lazy: heavy imports

    result = sanitize_document(
        args.file,
        detector=args.detector,
        fields=_split_fields(args.fields),
        llm_model=args.llm_model,
        llm_provider=args.llm_provider,
        mask_images=args.mask_images,
        image_classes=_image_classes(args),
        image_fields=_image_text_fields(args),
        image_backend=args.image_backend,
        image_model_path=args.image_model,
        mosaic_factor=args.mosaic_factor,
        score_threshold=args.score_threshold,
        redact_pdf=args.redact_pdf,
        redaction_style=args.redaction_style,
        audit=args.audit,
        recoverable=args.recoverable,
        recover_key=args.recover_key,
        out_dir=args.out_dir,
        mineru_backend=args.mineru_backend,
        mineru_out_dir=args.mineru_out_dir,
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
    if result.redaction_leftovers:
        pages = ",".join(str(p) for p in sorted({p for _, p in result.redaction_leftovers}))
        print(
            f"  ⚠ {len(result.redaction_leftovers)} sensitive values still "
            f"present in redacted.pdf (pages {pages})"
        )
    print(f"  Audit report: {result.audit_path}")
    if result.sensitive_report_path:
        print(f"  Sensitive-value report: {result.sensitive_report_path}")
    return 0


def _run_recover(args: argparse.Namespace) -> int:
    from pysanitize.recover import recover_file  # lazy: optional extra

    result = recover_file(
        args.file,
        audit_path=args.recover_audit,
        passphrase=args.recover_key,
    )
    print(f"Recovered document: {result.output}")
    print(
        f"  Restored values {result.restored} · unresolved spans "
        f"{result.unresolved} ({result.kind})"
    )
    if result.unresolved:
        print(
            "  unresolved = audit.json spans that could not be placed back "
            "(document edited after sanitizing? ciphertext undecryptable?)"
        )
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
        rc = _run_recover(args) if args.recover else _run_sanitize(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        rc = 130
    except Exception as e:
        logging.getLogger("PySanitize").error("%s", e)
        rc = 1
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

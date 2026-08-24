"""Command-line interface for PySanitize.

Usage::

    pysanitize sanitize 样例.pdf                     # rules, text only
    pysanitize sanitize 样例.pdf --detector hybrid   # + LLM-located spans
    pysanitize sanitize 样例.pdf --mask-images       # + face mosaic on images
    pysanitize sanitize 样例.pdf --fields person_name,phone --audit

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
        description="多格式文档脱敏工具：MinerU 解析 + 规则/LLM 敏感字段检测 + 文本/图片脱敏",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sp = sub.add_parser(
        "sanitize",
        help="对一份文档执行完整脱敏 pipeline",
        description=(
            "解析文档 → 定位敏感字段 → 掩码文本（+ 可选图片打码）→ 输出 "
            "sanitized.md / images_masked/ / audit.json"
        ),
    )
    sp.add_argument("file", help="输入文档：PDF / 图片 / DOCX / PPTX / XLSX")
    sp.add_argument(
        "--detector",
        choices=("rules", "llm", "hybrid"),
        help="文本检测模式（默认取 config/pipeline.yaml）",
    )
    sp.add_argument(
        "--fields",
        help="限定检测的字段类型，逗号分隔（默认全部启用字段）",
    )
    sp.add_argument(
        "--model", dest="llm_model",
        help="LLM 模型 = config/llm/<model>.yaml 文件名（detector 为 llm/hybrid 时）",
    )
    sp.add_argument(
        "--provider", dest="llm_provider",
        help="LLM 供应商 = config/llm/<model>.yaml 里的 provider 段（openai | pingan，默认 openai）",
    )
    group = sp.add_mutually_exclusive_group()
    group.add_argument(
        "--mask-images", dest="mask_images", action="store_true",
        help="开启图片人脸检测 + 马赛克",
    )
    group.add_argument(
        "--no-mask-images", dest="mask_images", action="store_false",
        help="关闭图片打码（覆盖配置）",
    )
    sp.set_defaults(mask_images=None)
    sp.add_argument(
        "--image-classes",
        help=(
            "图片打码目标，逗号分隔：face | text(OCR 文字区域) | <YOLO 类别>"
            "（如 person,car）。默认空 = 即使 --mask-images 也不处理图片"
        ),
    )
    sp.add_argument(
        "--image-backend", choices=("auto", "yunet", "haar", "yolo"),
        help="人脸后端（classes 含 face 时，默认 auto：YuNet，离线降级 Haar）",
    )
    sp.add_argument("--image-model", help="检测模型权重（YuNet onnx / YOLO .pt，默认自动下载）")
    sp.add_argument("--mosaic-factor", type=int, help="马赛克块大小（默认 16）")
    sp.add_argument("--score-threshold", type=float, help="检测置信度阈值（默认 0.5）")
    ga = sp.add_mutually_exclusive_group()
    ga.add_argument("--audit", dest="audit", action="store_true",
                    help="额外写出含敏感原文的 sensitive_report.json（本地审计）")
    ga.add_argument("--no-audit", dest="audit", action="store_false")
    sp.set_defaults(audit=None)
    sp.add_argument("--out-dir", help="输出目录（默认 output/<文档名>/）")
    sp.add_argument("--parse-backend", help="MinerU 后端（pipeline/vlm-engine/hybrid-engine）")
    sp.add_argument("--lang", default="ch", help="OCR 语言（默认 ch）")
    verb = sp.add_mutually_exclusive_group()
    verb.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    verb.add_argument("-q", "--quiet", action="store_true", help="只输出 WARNING+ 日志")
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
    print(f"输出目录：{result.out_dir}")
    print(f"  脱敏文档：{result.sanitized_md}")
    print(
        f"  文本掩码 {len(result.detections)} 处 · 图片打码 "
        f"{result.images_masked}/{result.images_total} · 耗时 {result.duration_s:.1f}s"
    )
    print(f"  审计报告：{result.audit_path}")
    if result.sensitive_report_path:
        print(f"  敏感原文报告：{result.sensitive_report_path}")
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
        print("\n已中断", file=sys.stderr)
        rc = 130
    except Exception as e:
        logging.getLogger("PySanitize").error("%s", e)
        rc = 1
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

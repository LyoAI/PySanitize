"""Pipeline orchestration: parse → detect → mask text → mask images → report.

The public entry is :func:`sanitize_document`, which turns one document into a
job directory containing ``sanitized.md`` + ``images_masked/`` + ``audit.json``.
Every parameter defaults to ``config/pipeline.yaml``; explicit kwargs (set by the
CLI only when the user actually passed a flag) override it.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from pysanitize.config import MINERU_BACKEND, OUT_DIR, load_pipeline_config
from pysanitize.detector.base import Detection
from pysanitize.detector.image import DetectedObject, build_detectors
from pysanitize.detector.llm import LLMDetector
from pysanitize.detector.registry import DetectionRegistry
from pysanitize.detector.rules import RuleDetector
from pysanitize.detector.specs import MaskSpec, load_field_specs, select_specs
from pysanitize.masker.image import ImageMasker
from pysanitize.masker.text import TextMasker
from pysanitize.parser.blocks import META_TYPES
from pysanitize.parser.document import parse_document
from pysanitize.report import AuditInfo, write_audit, write_sensitive_report
from pysanitize.utils import get_logger

logger = get_logger()

DETECTOR_MODES = ("rules", "llm", "hybrid")


@dataclass
class SanitizeResult:
    """Outcome of one ``sanitize_document`` call."""

    doc_id: str
    out_dir: Path
    sanitized_md: Path
    audit_path: Path
    sensitive_report_path: Path | None  # only with ``audit=True``
    detections: list[Detection]
    images_total: int
    images_masked: int
    duration_s: float
    detector: str
    fields: list[str] = field(default_factory=list)


def sanitize_document(
    doc_path: str | Path,
    *,
    detector: str | None = None,          # rules | llm | hybrid (config default)
    fields: list[str] | None = None,      # subset of field types to detect
    llm_model: str | None = None,         # LLM model (llm / hybrid)
    llm_provider: str | None = None,      # provider section: openai | pingan (config/llm/<model>.yaml)
    mask_images: bool | None = None,      # None → config image.enabled
    image_classes: list[str] | None = None,  # mask targets face|text|<yolo class>; empty = no masking
    image_backend: str | None = None,     # face backend auto | yunet | haar | yolo
    image_model_path: str | Path | None = None,
    score_threshold: float | None = None,
    mosaic_factor: int | None = None,
    audit: bool | None = None,            # None → config output.audit
    verify_checksums: bool | None = None,
    out_dir: str | Path | None = None,    # job output root (default OUT_DIR/<stem>)
    parse_backend: str | None = None,
    lang: str | None = None,
    skip_existing: bool = True,
) -> SanitizeResult:
    """Run the full pipeline on one document.

    Args:
        doc_path: PDF / image / docx / pptx / xlsx file.
        detector: ``rules`` (local regex+dictionary), ``llm`` (deepseek locates
            spans), or ``hybrid`` (both, rules wins on ties).
        fields: restrict detection to these field types (default: all enabled).
        llm_provider: provider section in ``config/llm/<model>.yaml`` —
            ``openai`` (default) or ``pingan``.
        mask_images: also detect sensitive regions + mosaic the document's images.
        image_classes: what to mask in images — ``face``, ``text`` (OCR text
            regions), and/or YOLO class names. Empty means no image masking,
            even with ``mask_images=True``.
        audit: additionally write ``sensitive_report.json`` with raw values.
        out_dir: where ``sanitized.md`` / ``images_masked/`` / ``audit.json`` go.

    Returns:
        :class:`SanitizeResult` with paths to every artifact.
    """
    doc_path = Path(doc_path)
    cfg = load_pipeline_config()
    text_cfg = cfg.get("text", {})
    image_cfg = cfg.get("image", {})
    output_cfg = cfg.get("output", {})

    detector = detector or text_cfg.get("detector", "rules")
    if detector not in DETECTOR_MODES:
        raise ValueError(f"detector must be one of {DETECTOR_MODES}, got {detector!r}")
    llm_model = llm_model or text_cfg.get("model") or LLMDetector.DEFAULT_MODEL
    llm_provider = (
        llm_provider
        or text_cfg.get("provider")
        or LLMDetector.DEFAULT_PROVIDER
    )
    verify_checksums = (
        text_cfg.get("verify_checksums", True)
        if verify_checksums is None
        else verify_checksums
    )
    mask_images = (
        image_cfg.get("enabled", False) if mask_images is None else mask_images
    )
    image_classes = (
        [c for c in image_cfg.get("classes", []) if c]
        if image_classes is None
        else [c for c in (image_classes or []) if c]
    )
    image_backend = image_backend or image_cfg.get("detector", "auto")
    image_model_path = (
        Path(image_model_path)
        if image_model_path is not None
        else (Path(image_cfg.get("model_path")) if image_cfg.get("model_path") else None)
    )
    score_threshold = (
        float(image_cfg.get("score_threshold", 0.5))
        if score_threshold is None
        else float(score_threshold)
    )
    mosaic_factor = (
        int(image_cfg.get("mosaic_factor", 16))
        if mosaic_factor is None
        else int(mosaic_factor)
    )
    audit = output_cfg.get("audit", False) if audit is None else audit

    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    start = time.monotonic()

    doc = parse_document(
        doc_path,
        backend=parse_backend or MINERU_BACKEND,
        lang=lang or "ch",
        skip_existing=skip_existing,
    )

    # ---- text: detect + mask -------------------------------------------------
    specs = select_specs(load_field_specs(), fields)
    registry = DetectionRegistry()
    if detector in ("rules", "hybrid"):
        registry.add(RuleDetector(specs=specs, verify_checksums=verify_checksums))
    if detector in ("llm", "hybrid"):
        registry.add(LLMDetector(model=llm_model, provider=llm_provider, fields=fields))
    detections = registry.detect(doc)

    mask_map = {name: spec.mask for name, spec in specs.items()}
    masked_text = TextMasker(mask_map).mask(doc.text, detections)

    # ---- images: face-detect + mosaic ----------------------------------------
    out_dir = Path(out_dir) if out_dir else (OUT_DIR / doc.doc_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    masked_image_names: dict[str, Path] = {}
    masked_images: list[Path] = []
    if mask_images:
        masked_images, masked_image_names = _mask_images(
            doc,
            out_dir,
            classes=image_classes,
            backend=image_backend,
            model_path=image_model_path,
            score_threshold=score_threshold,
            factor=mosaic_factor,
        )

    sanitized_md = out_dir / "sanitized.md"
    sanitized_md.write_text(
        _build_markdown(doc, detections, mask_map, masked_image_names, out_dir),
        encoding="utf-8",
    )

    duration = time.monotonic() - start
    info = AuditInfo(
        doc_id=doc.doc_id,
        source=doc_path.name,
        detector=detector,
        fields=[f for f in specs],
        pages=doc.pages,
        blocks=len(doc.blocks),
        text_chars=len(doc.text),
        images_total=len(doc.images),
        images_masked=len(masked_images),
        detections=detections,
        duration_s=duration,
        started_at=started_at,
    )
    audit_path = write_audit(info, out_dir)
    sensitive_path = write_sensitive_report(info, out_dir) if audit else None

    logger.success(
        "%s: %d sensitive spans masked, %d/%d images mosaiced -> %s",
        doc.doc_id,
        len(detections),
        len(masked_images),
        len(doc.images),
        out_dir,
    )
    return SanitizeResult(
        doc_id=doc.doc_id,
        out_dir=out_dir,
        sanitized_md=sanitized_md,
        audit_path=audit_path,
        sensitive_report_path=sensitive_path,
        detections=detections,
        images_total=len(doc.images),
        images_masked=len(masked_images),
        duration_s=duration,
        detector=detector,
        fields=list(specs),
    )


def _mask_images(
    doc,
    out_dir: Path,
    *,
    classes: list[str],
    backend: str,
    model_path: Path | None,
    score_threshold: float,
    factor: int,
) -> tuple[list[Path], dict[str, Path]]:
    """Detect the requested classes in every extracted image and mosaic them.

    ``classes`` is the list of targets (``face`` / ``text`` / YOLO names); an
    empty list means the user opted out — no image masking happens, not even a
    plain copy, so ``sanitized.md`` keeps pointing at MinerU's originals.

    Returns ``(masked_paths, name_map)`` where ``name_map`` maps the original
    image filename to its copy under ``out_dir/images_masked/`` — the markdown
    rewriter points every image link there so sanitized.md stays self-contained.
    """
    if not doc.images:
        return [], {}
    if not classes:
        logger.warning(
            "Image masking is enabled but no targets were given (image.classes / --image-classes), "
            "skipping image processing"
        )
        return [], {}
    detectors = build_detectors(
        classes, backend=backend, model_path=model_path, score_threshold=score_threshold
    )
    if not detectors:
        logger.warning("No image detectors available, skipping image masking")
        return [], {}
    dst_dir = out_dir / "images_masked"
    dst_dir.mkdir(parents=True, exist_ok=True)
    masked: list[Path] = []
    name_map: dict[str, Path] = {}
    masker = ImageMasker(factor=factor)
    for img in doc.images:
        src = img.path
        if not src.is_file():
            continue
        dst = dst_dir / src.name
        boxes: list[DetectedObject] = []
        for det in detectors:
            try:
                boxes.extend(det.detect(src))
            except Exception as e:  # a broken image shouldn't fail the whole run
                logger.warning("detection failed on %s: %s", src.name, e)
        if boxes:
            masker.mask_file(src, dst, boxes)
            masked.append(dst)
            labels = ",".join(sorted({b.label for b in boxes}))
            logger.debug("mosaicked %s (%d regions: %s)", src.name, len(boxes), labels)
        else:
            shutil.copy2(src, dst)  # nothing found — keep the original alongside
        name_map[src.name] = dst
    return masked, name_map


def _build_markdown(
    doc,
    detections: list[Detection],
    mask_map: dict[str, MaskSpec],
    image_names: dict[str, Path],
    out_dir: Path,
) -> str:
    """Assemble sanitized.md block by block.

    Text blocks are masked with *block-relative* offsets: fixed-length masks
    (e.g. ``****``) change the total string length, so offsets into a globally
    masked text drift as you move past an earlier span. Masking each block's
    own text keeps every offset exact. Image blocks become markdown references
    to ``images_masked/`` copies (when available) so the output is
    self-contained; captions are still masked.
    """
    from pysanitize.masker.text import mask_text

    parts: list[str] = []
    for block in doc.blocks:
        if block.type in META_TYPES:
            continue
        block_dets = [
            Detection(
                field_type=d.field_type,
                value=d.value,
                start=d.start - block.char_start,
                end=d.end - block.char_start,
                page=d.page,
                source=d.source,
                confidence=d.confidence,
            )
            for d in detections
            if block.char_start <= d.start and d.end <= block.char_end
        ]
        seg = mask_text(block.text, block_dets, mask_map)
        if block.type == "image":
            dst = image_names.get(block.image_path.name) if block.image_path else None
            if dst is not None:
                rel = dst.relative_to(out_dir)
                caption = seg.strip()
                link = (
                    f"![{caption}]({rel.as_posix()})"
                    if caption
                    else f"![]({rel.as_posix()})"
                )
                parts.append(link)
                continue
        if seg.strip():
            parts.append(seg)
    return "\n\n".join(parts)

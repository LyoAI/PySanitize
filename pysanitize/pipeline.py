"""Pipeline orchestration: parse → detect → mask text → mask images → redact → report.

The public entry is :func:`sanitize_document`, which turns one document into a
job directory containing ``sanitized.md`` + ``images_masked/`` + ``audit.json``
and, for PDF sources when enabled, a layout-preserving ``redacted.pdf`` (true
glyph removal + mosaic). Every parameter defaults to ``config/pipeline.yaml``;
explicit kwargs (set by the CLI only when the user actually passed a flag)
override it.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from pysanitize.config import (
    MINERU_BACKEND,
    OUT_DIR,
    get_image_config,
    get_output_config,
    get_text_config,
)
from pysanitize.detector.base import Detection
from pysanitize.detector.image import DetectedObject, build_detectors, build_ocr_field_detector
from pysanitize.detector.llm import LLMDetector
from pysanitize.detector.registry import DetectionRegistry
from pysanitize.detector.rules import RuleDetector
from pysanitize.detector.specs import MaskSpec, load_field_specs, select_specs
from pysanitize.masker.image import ImageMasker
from pysanitize.masker.text import Placements, mask_text, mask_text_placed
from pysanitize.parser.blocks import META_TYPES
from pysanitize.parser.document import ParsedDocument, parse_document
from pysanitize.redact import Redaction, resolve_rects, verify_redaction
from pysanitize.redact import redact_pdf as _write_redacted_pdf  # param name shadows the import
from pysanitize.report import AuditInfo, write_audit, write_sensitive_report
from pysanitize.utils import get_logger

logger = get_logger()

DETECTOR_MODES = ("rules", "llm", "hybrid")

# Block types whose only content is an embedded image — rendered as markdown
# images. Everything else (tables, paragraphs, ...) renders as masked text even
# when MinerU also stored an ``image_source`` for it.
IMAGE_TYPES = frozenset({"image", "chart"})


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
    redacted_pdf: Path | None = None  # PDF sources only, when regions were found
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
    image_fields: list[str] | None = None,   # field types to detect inside images (None → follow ``fields``)
    image_backend: str | None = None,     # detector: auto/yunet/haar are face models, other classes → YOLO
    image_model_path: str | Path | None = None,
    score_threshold: float | None = None,
    mosaic_factor: int | None = None,
    redact_pdf: bool | None = None,       # None → config output.redact_pdf
    redaction_style: str | None = None,   # mosaic | block (None → config)
    audit: bool | None = None,            # None → config output.audit
    recoverable: bool | None = None,      # None → config output.recoverable
    recover_key: str | None = None,       # passphrase; else env PYSANITIZE_RECOVER_KEY / .recover.key
    verify_checksums: bool | None = None,
    out_dir: str | Path | None = None,    # job output root (default OUT_DIR/<stem>)
    mineru_backend: str | None = None,
    lang: str | None = None,
    skip_existing: bool = True,
) -> SanitizeResult:
    """Run the full pipeline on one document.

    Args:
        doc_path: PDF / image / docx / pptx / xlsx file.
        detector: ``rules`` (local regex+dictionary), ``llm`` (llm locates
            spans), or ``hybrid`` (both, rules wins on ties).
        fields: restrict detection to these field types (default: all enabled).
        llm_provider: provider section in ``config/llm/<model>.yaml`` —
            ``openai`` (default) or ``pingan``.
        mask_images: also detect sensitive regions + mosaic the document's images.
        image_classes: what to mask in images — ``face``, ``text`` (OCR text
            regions), and/or YOLO class names. Empty means no image masking,
            even with ``mask_images=True``.
        image_fields: field types to detect *inside* images (OCR → same text
            rules → mosaic the matching spans). ``None`` follows ``fields``;
            an explicit list may be a superset (company names / registered
            addresses appear in images too). ``image.classes`` is unaffected.
        redact_pdf: for PDF sources, also write a layout-preserving
            ``redacted.pdf`` (true glyph removal + mosaic) alongside the md.
            Off unless passed true (CLI ``--redact-pdf``) or enabled in config.
        redaction_style: ``mosaic`` (pixelated) or ``block`` (solid box).
        audit: additionally write ``sensitive_report.json`` with raw values.
        recoverable: make the run reversible — the document keeps the normal
            placeholder, while audit.json additionally records each value's
            AES-GCM ciphertext and its position (md range / PDF rects), so
            ``pysanitize --recover`` can restore the originals (md exactly;
            pdf best-effort). Needs the ``recover`` extra. The passphrase
            comes from ``recover_key``, else ``PYSANITIZE_RECOVER_KEY``,
            else one is generated — whichever way, the effective key is
            materialized into ``.recover.key`` inside the output directory
            (audit.json carries only public cipher parameters), so
            ``--recover`` just reads that file.
        recover_key: passphrase for the recovery cipher (see ``recoverable``).
        out_dir: where ``sanitized.md`` / ``images_masked/`` / ``audit.json``
            (and ``redacted.pdf``) go.

    Returns:
        :class:`SanitizeResult` with paths to every artifact.
    """
    doc_path = Path(doc_path)
    text_cfg = get_text_config()
    image_cfg = get_image_config()
    output_cfg = get_output_config()

    detector = detector or text_cfg.get("detector", "rules")
    if detector not in DETECTOR_MODES:
        raise ValueError(f"detector must be one of {DETECTOR_MODES}, got {detector!r}")
    llm_model = llm_model or text_cfg.get("model")
    llm_provider = llm_provider or text_cfg.get("provider")
    chunking_cfg = text_cfg.get("chunking", {})
    chunk_size = int(chunking_cfg.get("chunk_size", 6000))
    title_level_limit = chunking_cfg.get("title_level_limit", "auto")
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
    # image.fields: the config sentinel (None) follows the text field set; an
    # explicit list may differ (or be a superset) — company names / addresses
    # appear in images too.
    if image_fields is None:
        image_fields = image_cfg.get("fields")
        if image_fields is None:
            image_fields = fields
    if isinstance(image_fields, str):
        image_fields = [f.strip() for f in image_fields.split(",") if f.strip()]
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
    recoverable = (
        output_cfg.get("recoverable", False) if recoverable is None else recoverable
    )

    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    start = time.monotonic()

    doc = parse_document(
        doc_path,
        backend=mineru_backend or MINERU_BACKEND,
        lang=lang or "ch",
        skip_existing=skip_existing,
    )

    # ---- text: detect + mask -------------------------------------------------
    out_dir = Path(out_dir) if out_dir else (OUT_DIR / doc.doc_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Recovery mode needs the output directory first. Whatever way the key was
    # supplied (arg > env > existing keyfile > generate), the *effective* key is
    # materialized into .recover.key (0600), so --recover only ever reads that
    # file.
    cipher = None
    if recoverable:
        from pysanitize.recover.crypto import KEYFILE_NAME, TokenCipher, obtain_passphrase

        passphrase, generated = obtain_passphrase(recover_key, out_dir / KEYFILE_NAME)
        if generated:
            logger.info(
                "recovery key written to %s/%s — keep it safe: the sanitized "
                "output cannot be restored without it",
                out_dir.name,
                KEYFILE_NAME,
            )
        cipher = TokenCipher.from_passphrase(passphrase)

    specs = select_specs(load_field_specs(), fields)
    registry = DetectionRegistry()
    if detector in ("rules", "hybrid"):
        registry.add(RuleDetector(specs=specs, verify_checksums=verify_checksums))
    if detector in ("llm", "hybrid"):
        registry.add(
            LLMDetector(
                model=llm_model,
                provider=llm_provider,
                fields=fields,
                chunk_size=chunk_size,
                title_level_limit=title_level_limit,
            )
        )
    # LLM values match verbatim and may contain the "\n\n" separator between
    # blocks; masking is per-block, so a straddling span would be skipped by
    # both blocks and leak. Split such spans first — every piece then masks,
    # audits and recovers exactly like any other detection.
    detections = _split_straddling(doc, registry.detect(doc))

    # Masking is identical in recovery mode: the document keeps the normal
    # placeholder, and the ciphertext is recorded per span in the audit only.
    mask_map = {name: spec.mask for name, spec in specs.items()}
    # One full-text pass so every detection carries its placeholder in
    # ``masked_value`` (the audit / recovery bookkeeping reads it). sanitized.md
    # itself is assembled per block in _build_markdown — block-relative offsets
    # stay exact there, while this pass also covers spans straddling blocks.
    mask_text(doc.text, detections, mask_map)

    # ---- images: copy under images_masked/ (mosaic on request) ----------------

    image_specs = select_specs(load_field_specs(), image_fields)
    masked_images, image_names = _prepare_images(
        doc,
        out_dir,
        mask=mask_images,
        classes=image_classes,
        field_specs=image_specs or None,
        verify_checksums=verify_checksums,
        backend=image_backend,
        model_path=image_model_path,
        score_threshold=score_threshold,
        factor=mosaic_factor,
        page_dims=doc.page_dimensions,
    )

    sanitized_md = out_dir / "sanitized.md"
    md_text, md_places = _build_markdown(doc, detections, mask_map, image_names, out_dir)
    sanitized_md.write_text(md_text, encoding="utf-8")

    # ---- PDF redaction: true glyph removal + mosaic, layout preserved --------
    redacted_pdf: Path | None = None
    redacted_pages = redaction_regions = 0
    rects: list[Redaction] = []
    if doc.source_suffix == ".pdf" and doc_path.is_file():
        do_redact = (
            output_cfg.get("redact_pdf", False) if redact_pdf is None else redact_pdf
        )
        if do_redact or cipher is not None:
            # Also resolved in recovery mode (redaction off) so the audit can
            # record each span's page rectangles for --recover.
            rects = resolve_rects(doc, detections, doc.page_dimensions)
        if do_redact:
            style = (
                output_cfg.get("redaction_style", "mosaic")
                if redaction_style is None
                else redaction_style
            )
            if style not in ("mosaic", "block"):
                raise ValueError(
                    f"redaction_style must be 'mosaic' or 'block', got {style!r}"
                )
            # Regions of images that were *actually* mosaiced are stamped back
            # so the PDF page matches the md's images_masked/.
            for img in doc.images:
                if img.bbox is None:
                    continue
                dst = image_names.get(img.path.name)
                if dst is not None and dst in masked_images:
                    rects.append(Redaction(page=img.page - 1, rect=img.bbox, image=dst))
            if rects:
                redacted_pdf = _write_redacted_pdf(
                    doc_path,
                    rects,
                    out_dir / "redacted.pdf",
                    style=style,
                    factor=mosaic_factor,
                )
                redacted_pages = len({r.page for r in rects})
                redaction_regions = len(rects)
                leftover = verify_redaction(redacted_pdf, [d.value for d in detections])
                if leftover:
                    logger.warning(
                        "redaction verification: %d sensitive values still present "
                        "in %s (missing geometry? scanned page?)",
                        len(leftover),
                        redacted_pdf.name,
                    )
            elif detections:
                logger.warning(
                    "PDF redaction enabled but no regions resolvable (parse output "
                    "has no geometry); skipping redacted.pdf"
                )

    rect_by_span: dict[tuple[int, int], list[list[float]]] = {}
    for r in rects:
        rect_by_span.setdefault((r.start, r.end), []).append(
            [r.rect.x0, r.rect.y0, r.rect.x1, r.rect.y1]
        )

    # Recovery bookkeeping, keyed by the detection's original offsets: the
    # ciphertext, where its placeholder sits in sanitized.md, and which page
    # rectangles were redacted — everything --recover needs besides the key.
    recover_spans: dict[tuple[int, int], dict] | None = None
    if cipher is not None:
        # Exact placeholder positions recorded while the markdown was built —
        # a post-hoc search could match a look-alike string from the document.
        md_ranges = md_places
        recover_spans = {}
        for d in detections:
            entry: dict = {"encrypted_value": cipher.token(d.value)}
            if (md_range := md_ranges.get((d.start, d.end))) is not None:
                entry["md"] = md_range
            if rect_by_span.get((d.start, d.end)):
                entry["rects"] = rect_by_span[(d.start, d.end)]
            recover_spans[(d.start, d.end)] = entry

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
        redacted_pdf=redacted_pdf.name if redacted_pdf else None,
        redacted_pages=redacted_pages,
        redaction_regions=redaction_regions,
        recovery=cipher.meta() if cipher else None,
        recover_spans=recover_spans,
    )
    audit_path = write_audit(info, out_dir)
    sensitive_path = write_sensitive_report(info, out_dir) if audit else None

    logger.success(
        "%s: %d sensitive spans masked, %d/%d images mosaiced%s -> %s",
        doc.doc_id,
        len(detections),
        len(masked_images),
        len(doc.images),
        f", {redaction_regions} PDF regions redacted" if redacted_pdf else "",
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
        redacted_pdf=redacted_pdf,
        duration_s=duration,
        detector=detector,
        fields=list(specs),
    )


def _prepare_images(
    doc,
    out_dir: Path,
    *,
    mask: bool,
    classes: list[str],
    backend: str,
    model_path: Path | None,
    score_threshold: float,
    factor: int,
    field_specs=None,
    verify_checksums: bool = True,
    page_dims=None,
) -> tuple[list[Path], dict[str, Path]]:
    """Copy every extracted image under ``out_dir/images_masked/``, mosaicing on request.

    Mirrors MinerU's layout (an ``images*`` dir next to the markdown), so
    ``sanitized.md`` references ``images_masked/<name>`` directly and stays
    self-contained. With ``mask`` the requested targets are detected and
    mosaiced into the copy — either the class-driven backends
    (``classes``: face / text / YOLO) and/or the field-driven OCR detector
    (``field_specs``: which sensitive *fields* to look for in the image text).
    Otherwise (or with no targets / no detectors) the original is copied as-is.

    Returns ``(masked_paths, name_map)`` where ``name_map`` maps the original
    image filename to its copy under ``out_dir/images_masked/``.
    """
    if not doc.images:
        return [], {}
    dst_dir = out_dir / "images_masked"
    dst_dir.mkdir(parents=True, exist_ok=True)
    class_detectors: list = []
    field_detector = None
    if mask:
        if not classes and not field_specs:
            logger.warning(
                "Image masking is enabled but no targets were given "
                "(image.classes / --image-classes / image.fields); "
                "copying originals as-is"
            )
        else:
            if classes:
                class_detectors = build_detectors(
                    classes,
                    backend=backend,
                    model_path=model_path,
                    score_threshold=score_threshold,
                )
            if field_specs:
                field_detector = build_ocr_field_detector(
                    field_specs, verify_checksums=verify_checksums
                )
            if not class_detectors and field_detector is None:
                logger.warning(
                    "No image detectors available; copying originals as-is"
                )
    masked: list[Path] = []
    name_map: dict[str, Path] = {}
    masker = ImageMasker(factor=factor)
    for img in doc.images:
        src = img.path
        if not src.is_file():
            continue
        dst = dst_dir / src.name
        boxes: list[DetectedObject] = []
        # Field-driven OCR first (skipped on near-full-page scans — their text
        # is already handled by the document-text path).
        if field_detector is not None and not _near_full_page(img, page_dims):
            try:
                boxes.extend(field_detector.detect(src))
            except Exception as e:  # a broken image shouldn't fail the whole run
                logger.warning("field detection failed on %s: %s", src.name, e)
        for det in class_detectors:
            try:
                boxes.extend(det.detect(src))
            except Exception as e:
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


def _near_full_page(img, page_dims, threshold: float = 0.8) -> bool:
    """A near-full-page image is a scanned page — OCR-ing it again would double
    the document-text work, so the field detector skips it."""
    if img.bbox is None or not page_dims or not 0 < img.page <= len(page_dims):
        return False
    pw, ph = page_dims[img.page - 1]
    if not pw or not ph:
        return False
    return img.bbox.width / pw > threshold and img.bbox.height / ph > threshold


def _split_straddling(
    doc: ParsedDocument, detections: list[Detection]
) -> list[Detection]:
    """Split detections that cross block boundaries into per-block pieces.

    ``sanitized.md`` is masked block by block, so a span straddling two blocks
    (an LLM value matching verbatim across the ``"\\n\\n"`` separator) would be
    skipped by *both* blocks and leak verbatim. Each piece is clamped inside
    one block; the audit then records one entry per piece and recovery stays
    exact. Rule hits never straddle (``\\n`` is a boundary char); this guards
    the LLM path.
    """
    out: list[Detection] = []
    for d in detections:
        blocks = doc.span(d.start, d.end)
        if len(blocks) <= 1:
            out.append(d)
            continue
        for b in blocks:
            start, end = max(d.start, b.char_start), min(d.end, b.char_end)
            if end > start:
                out.append(
                    replace(d, value=doc.text[start:end], start=start, end=end)
                )
    return out


def _build_markdown(
    doc,
    detections: list[Detection],
    mask_map: dict[str, MaskSpec],
    image_names: dict[str, Path],
    out_dir: Path,
) -> tuple[str, dict[tuple[int, int], list[int]]]:
    """Assemble sanitized.md block by block, recording exact placeholder spans.

    Text blocks are masked with *block-relative* offsets: fixed-length masks
    (e.g. ``****``) change the total string length, so offsets into a globally
    masked text drift as you move past an earlier span. Masking each block's
    own text keeps every offset exact. Titles become ``#``-level headings and
    image-bearing blocks (image/chart) become ``images_masked/`` references —
    every extracted image has a local copy there (mosaiced or original);
    image blocks whose extracted file is missing are skipped, never linked to a
    path that doesn't exist.

    Returns ``(markdown, places)``: ``places`` maps each detection's global
    ``(start, end)`` span to the ``[out_start, out_end)`` range its placeholder
    occupies in the built markdown. Positions are recorded *while building* —
    never searched for afterwards — so a look-alike string from the document
    (e.g. a literal ``***``) can never be mistaken for a placeholder.
    Detections in dropped blocks get no entry: their placeholder does not exist.
    """
    parts: list[str] = []
    places: dict[tuple[int, int], list[int]] = {}
    md_pos = 0  # where the part being appended starts in "\n\n".join(parts)

    def record(
        pairs: list[tuple[Detection, int, int]],
        seg_places: Placements,
        base: int,
        shift: int,
    ) -> None:
        """Lift block-relative placements (+``shift``) into md coordinates."""
        for d, rel_start, rel_end in pairs:
            if (hit := seg_places.get((rel_start, rel_end))) is not None:
                places[(d.start, d.end)] = [
                    base + shift + hit[0],
                    base + shift + hit[1],
                ]

    for block in doc.blocks:
        if block.type in META_TYPES:
            continue
        pairs = [
            (
                d,
                d.start - block.char_start,
                d.end - block.char_start,
            )
            for d in detections
            if block.char_start <= d.start and d.end <= block.char_end
        ]
        block_dets = [
            Detection(
                field_type=d.field_type,
                value=d.value,
                start=rel_start,
                end=rel_end,
                page=d.page,
                source=d.source,
                confidence=d.confidence,
            )
            for d, rel_start, rel_end in pairs
        ]
        seg, seg_places = mask_text_placed(block.text, block_dets, mask_map)
        prefix_len = 0
        if block.type == "title" and block.level:
            prefix = f"{'#' * block.level} "
            seg = prefix + seg
            prefix_len = len(prefix)
        if block.image_path is not None and (block.type in IMAGE_TYPES or not seg.strip()):
            target = image_names.get(block.image_path.name)
            if target is None:
                continue  # no local copy for this image — nothing to reference
            rel = Path(os.path.relpath(target, out_dir)).as_posix()
            caption = seg.strip()
            link = f"![{caption}]({rel})" if caption else f"![]({rel})"
            # The caption sits after "![" and loses seg's leading whitespace;
            # seg_places are pre-prefix coordinates, hence prefix_len too.
            lead = len(seg) - len(seg.lstrip())
            record(pairs, seg_places, md_pos, shift=2 + prefix_len - lead)
            parts.append(link)
            md_pos += len(link) + 2
            continue
        if seg.strip():
            record(pairs, seg_places, md_pos, shift=prefix_len)
            parts.append(seg)
            md_pos += len(seg) + 2
    return "\n\n".join(parts), places

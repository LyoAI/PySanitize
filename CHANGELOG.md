# Changelog

## [Unreleased]

middle.json is the primary parse source with per-line geometry, PDF inputs gain a layout-preserving `redacted.pdf`, and image masking becomes field-driven.

### Added

- **PDF redaction** (`pysanitize/redact/`): opt-in for PDF sources (`--redact-pdf` / `output.redact_pdf`) — the pipeline writes `redacted.pdf` beside `sanitized.md` with sensitive spans truly *deleted* from the content stream (`apply_redactions(text=0)`) and replaced with a mosaic (or solid box, `--redaction-style block`); table borders and vector graphics survive (`graphics=0`); overlapping image pixels are cleared (`images=2`). `verify_redaction()` re-reads the output and downgrades leftovers to a warning. `SanitizeResult.redacted_pdf` + audit `redaction` stats
- **Field-driven image masking** (`image.fields` / `--image-text <field-list>`): each extracted image is OCR'd (PaddleOCR) and the **same text field detectors** run over the recognized text; only the matching spans are mosaiced. Default (`null`) follows the text field set; an explicit list may be a superset; `[]` disables it. Near-full-page scan images are skipped (their text is already handled as document text). Complementary to the existing class-driven detectors
- **`pysanitize/parser/middle.py`**: MinerU `middle.json` projection — per-line geometry projected into `Block.line_boxes` (`LineBox`), TOC (`index`) dropped from text but kept as empty placeholders so per-page order aligns with v2 records, image/chart blocks carry `image_path` + `image_bbox`, and table markdown is recovered from v2 `html` (±1 positional retry, caption-only fallback). Page sizes surface as `ParsedDocument.page_dimensions`
- **TUI ③ Image tab**: image masking targets (enable switch, class list, all-text switch, detector) plus a "Same as text" toggle and a field `SelectionList` for image-specific fields; tabs renumbered to display order (① Fields ② Options ③ Image ④ Run ⑤ Results); `shape_params` keeps explicit empty lists (`image_fields=[]`) so "no field-driven masking" survives
- **`pymupdf` promoted to a runtime dependency** (was dev-only) — the only dependency that both reads and faithfully rewrites a PDF; AGPL-licensed, documented in the README

### Changed

- **Parse source is `middle.json`**, not `content_list_v2` — every span carries page coordinates, so detections map onto page rectangles (`resolve_rects` → in-line proportional sub-boxes / whole-block bbox for tables) instead of a coordinate-less derived projection. v2 remains for table cell text and as a fallback when a backend emits no `pdf_info`
- **CLI**: image flags follow the model boundary — `--image-classes face,person` (detection models) + `--image-text [fields]` (OCR: bare = all printed text, field list = only matching fields), replacing `--image-classes` / `--image-fields`; boolean switches are uniform store_true opt-ins — `--mask-images`, `--audit`, `--redact-pdf` (replacing `--no-redact-pdf`) mean "on" by presence, absent always means "config decides"); `--parse-backend` renamed `--mineru-backend`; `_run_sanitize` prints the `redacted.pdf` path
- **`config/pipeline.yaml`**: `output.redact_pdf` (default false — redaction is opt-in) + `output.redaction_style` (mosaic), `image.fields` (null = follow the text fields)
- **`<file>` promoted to the top-level CLI command**: `pysanitize sample.pdf --detector hybrid`; the pre-0.3 `pysanitize sanitize sample.pdf` form keeps working as an alias; `--launch tui|webui` selects an interactive frontend
- **All pipeline tunables centralized in `config/pipeline.yaml`** (`text.chunking.*`, `text.min/max_value_len`, `text.max_completion_tokens`, `image.mosaic_factor`, `image.haar/ocr/yolo.*`, …); Python modules keep only sentinel defaults resolved from config at call time — no scattered module-level constants
- Removed the dead `utils/skill_loader.py`

### Fixed

- **Short values are never emitted unmasked**: `MaskSpec.mask` could return a value verbatim when `keep_head + keep_tail` covered the whole string — e.g. a 7-digit hotline `9555526` with the phone mask (keep 3+4) passed through unchanged in `sanitized.md`. Now the head is kept and the remainder is masked (`955****`), with at least one character always hidden

### Tests

- 143 tests (from 109): `tests/test_parser_middle.py` (line geometry / flatten offsets / TOC skip / table v2 alignment / office no-bbox), `tests/test_redact_pdf.py` (real pymupdf documents → detect → redact → `get_text` proves glyph removal + neighbor preservation + block style + multi-page), `tests/test_image_fields.py` (mock OCR → field rules → sub-line mosaic boxes; graceful degradation without paddleocr), pipeline redaction + `image.fields` resolution, TUI five-tab mount + Image-pane collect

## [0.2.0] - 2026-08-24

Image desensitization generalized from "faces only" to **class-driven**: the user
names the targets, the detection model locates them, and they get mosaiced.

### Added

- **detector/image**: `FaceBox` → `DetectedObject` (new `label`: `face` / `text` / a YOLO class name; `FaceBox` kept as an alias)
- **YOLO class filtering**: `YOLODetector(classes=[...])` supports arbitrary weights + per-class filtering (`row.cls` → `model.names`); `YOLOFaceDetector` alias kept
- **OCR text regions**: `OCRTextDetector` (PaddleOCR, `--extra image-ocr`); the `text` class mosaics every printed-text region in an image (company names / seals / screenshots)
- **`build_detectors(classes, ...)`**: class → backend routing — `face` → YuNet/Haar/YOLO, `text` → OCR, anything else → YOLO class filter; missing backends degrade with a warning instead of crashing
- **No image detection by default**: with `image.classes` empty, `--mask-images` leaves images untouched (better to miss than to wrongly mosaic); CLI gains `--image-classes face,text,person` (comma-separated)

### Changed

- **LLM provider/model selectable**: CLI adds `--provider` (`openai`/`pingan`) and `--model`, matching finsearch-bench — `--provider` picks a provider section in `config/llm/<model>.yaml`; `sanitize_document(llm_provider=...)` + `config/pipeline.yaml` `text.provider`
- `config/pipeline.yaml` gains `image.classes` (default `[]`); `image.detector` is now the "face backend" setting
- All image detectors/maskers emit and consume `DetectedObject` (with `label`, so the audit can tell targets apart)

### Tests

- 74 unit tests (+13): routing fallback (empty classes / missing paddleocr / missing ultralytics), YOLO class filtering, OCR box building / confidence filtering / empty pages, `FaceBox` alias, images untouched when no classes

## [0.1.0] - 2026-08-24

M1 MVP: multi-format document desensitization pipeline (sanitized markdown + mosaiced images + audit report).

### Added

- **parser**: MinerU CLI wrapper (`-p doc -o out -b backend -l ch`) supporting PDF/image/DOCX/PPTX/XLSX; `content_list_v2` projected to `Block`s, images paired with image blocks in reading order; `ParsedDocument.text` gives the full text + per-block char offsets (page headers/footers excluded from the desensitized text)
- **detector/rules**: regex + dictionary heuristics, 8 default field types; ID-card (GB 11643) and USCC (GB 32100) checksums toggleable; surname-context person names, company-suffix backtracking with boundary/blacklist pruning
- **detector/llm**: LLM only locates (returns `{field_type, value}` verbatim substrings); chunking (6000 chars + 300 overlap) + verbatim re-match for exact offsets; values that don't re-match are dropped (hallucination hard gate); `temperature=0` + `response_format=json_object`
- **detector/registry**: aggregates detectors, exact dedup + containment resolution (rules win over LLM)
- **masker/text**: left-to-right reconstruction by offset; fixed-length placeholders keep markdown tables aligned; defensive merging of overlaps
- **detector/image**: YuNet (ONNX, auto-downloaded on first use) preferred, offline fallback to Haar; optional `[image-yolo]` extra
- **masker/image**: PIL NEAREST block mosaic (default 16px), covering only detected boxes
- **pipeline + cli + report**: `sanitize_document()` orchestration; CLI `pysanitize sanitize <file> [--detector rules|llm|hybrid] [--fields a,b] [--mask-images] [--audit]`; output `sanitized.md` + `images_masked/` + `audit.json` (public summary, no raw values), with `--audit` adding a `sensitive_report.json`
- **config**: `config/fields.yaml` (field specs), `config/pipeline.yaml` (stage switches), `config/llm/*.yaml` (`${ENV_VAR}` placeholders, no plaintext keys); `.env.example`

### Fixed

- Scaffolding leftovers: `finsearch.*` imports → `pysanitize.*`; declared `openai>=1` / `mineru[pipeline]` deps
- Pinned `opencv-python` `<5.0` (5.x removed Haar `CascadeClassifier`, breaking the offline fallback)
- Explicit `six` dependency (missing transitive dep of `mineru[pipeline]` broke the pipeline backend)

### Tests

- 60 unit tests: parser projection/document offsets, rules detector (checksums/blacklists/boundaries), LLM detector (chunking/re-match/hallucination filter), registry resolution, text/image masking, pipeline orchestration, CLI; end-to-end sample PDF verified against real MinerU

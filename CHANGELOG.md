# Changelog

## [Unreleased]

Config centralized in YAML, prompts externalized, and an interactive TUI alongside the CLI.

### Added

- **Interactive TUI** (`--launch tui`, Textual, `--extra tui`): four tabs — Fields (checkbox-select from `config/fields.yaml`), Options (file/detector/LLM/image settings), Run (free-form requirements appended to the LLM prompt, live log), Results (per-field counts + output paths); runs the pipeline in a background worker via `pysanitize.core.run_sanitizer`, the shared frontend facade for the future WebUI
- **`pysanitize/prompts/`**: LLM system/user prompts move out of `detector/llm.py` into `system.md` / `user.md` templates; `set_extra_requirements()` lets the TUI append custom requirements to the system prompt
- **`pysanitize/core.py`**: `run_sanitizer()` facade wrapping `sanitize_document()` with `extra_requirements` injection

### Changed

- **`<file>` promoted to the top-level CLI command**: `pysanitize sample.pdf --detector hybrid`; the pre-0.3 `pysanitize sanitize sample.pdf` form keeps working as an alias; `--launch tui|webui` selects an interactive frontend
- **All pipeline tunables centralized in `config/pipeline.yaml`** (`text.chunking.*`, `text.min/max_value_len`, `text.max_completion_tokens`, `image.mosaic_factor`, `image.haar/ocr/yolo.*`, …); Python modules keep only sentinel defaults resolved from config at call time — no scattered module-level constants
- Removed the dead `utils/skill_loader.py`

### Tests

- 109 tests (from 89): config layer (`tests/test_config.py`), prompt templates (`tests/test_prompts.py`), TUI panes under Textual's Pilot harness (`tests/test_tui.py`), rewritten CLI parser tests

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

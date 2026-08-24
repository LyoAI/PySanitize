<div align="center">

# 🛡️ PySanitize

**Multi-format document desensitization · local parsing · rules + LLM locating · class-driven image mosaicing**

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.2.0-4A90D9)
![Tests](https://img.shields.io/badge/tests-78%20passed-brightgreen)
![Parse](https://img.shields.io/badge/parse-local%20MinerU-2E7D32)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-blue)
![CI](https://github.com/LyoAI/PySanitize/actions/workflows/ci.yml/badge.svg)

**PDF · DOCX · Excel · scans** → **sanitized Markdown + mosaiced images + audit report**

English | [中文](./README_ZH.md)

</div>

PySanitize desensitizes sensitive information in **PDF / DOCX / Excel / scanned documents**, fully **local** parsing on top of [MinerU](https://github.com/opendatalab/MinerU) — no cloud calls.

## ✨ Features

| | |
|---|---|
| 🧩 **Multi-format input** | PDF / image / DOCX / PPTX / XLSX parsed into one unified structure |
| 🔍 **Rules + LLM dual engine** | regex + dictionary heuristics (offline-ready); the LLM only *locates*, never rewrites, with verbatim re-match against hallucination |
| 🖼️ **Class-driven image masking** | `face` (faces) / `text` (OCR text) / any YOLO class → mosaic |
| 🔌 **Switchable providers** | `--provider openai \| pingan`, flip between intranet/extranet |
| 📊 **Audit-friendly** | public summary has no raw values; the raw-value report is only written with `--audit` |
| 🛡️ **Fault-tolerant** | missing keys / optional deps / model downloads degrade with a warning, never a hard crash |

## 🔄 Pipeline

```text
input(PDF/DOCX/Excel/scan)
  → [parser]      MinerU → ParsedDocument{text, blocks, images/}
  → [detector]    rules + LLM locate sensitive fields → exact char offsets
  → [masker]      mask by field type (138****5678 / **** / keep head-tail N)
  → [image]       class-driven detection (face/OCR text/YOLO objects) → PIL mosaic
  → output        sanitized.md + images_masked/ + audit.json
```

## 📦 Installation

Requires Python ≥ 3.12; [uv](https://docs.astral.sh/uv/) recommended:

```bash
git clone https://github.com/LyoAI/PySanitize.git && cd PySanitize
uv sync                    # all deps (incl. mineru[pipeline], large)
```

`config/` and `.env` are **git-ignored and optional** — the tool ships built-in defaults (field specs, pipeline switches), so a fresh clone and CI work out of the box. To customize or add real keys, create them locally:

- `config/fields.yaml`, `config/pipeline.yaml`, `config/llm/<model>.yaml` — override the built-in defaults (see the config sections below; the default field specs live in `pysanitize/detector/specs.py`).
- `.env` — API keys referenced by `config/llm/*.yaml` `${VAR}` placeholders:

```
DEEPSEEK_API_KEY=...      # deepseek-v4-flash (default model)
DASHSCOPE_API_KEY=...     # qwen3-max / qwen3.6-27b
MINERU_BACKEND=pipeline   # pipeline (CPU) | vlm-engine / hybrid-engine (GPU)
MODELS_DIR=~/Models       # downloaded local models (YuNet ONNX, ...)
LLM_TIMEOUT_S=180         # per-call LLM request timeout (seconds)
```

Optional features, unlock on demand:

```bash
uv sync --extra image-yolo      # YOLO general object detection (ultralytics)
uv sync --extra image-ocr       # OCR text-region detection (paddleocr, large)
```

## 🚀 Quick start

```bash
# 1) Pure local rules detection (offline, no LLM)
uv run pysanitize sanitize sample.pdf

# 2) Hybrid: rules + LLM locating (default openai/deepseek-v4-flash)
uv run pysanitize sanitize sample.pdf --detector hybrid

#    pick the LLM provider + model (finsearch-bench style)
uv run pysanitize sanitize sample.pdf --detector hybrid --provider pingan --model qwen3.6-27b
uv run pysanitize sanitize sample.pdf --detector llm     --provider openai  --model qwen3-max

# 3) Image masking: no image is detected by default — name your targets explicitly
uv run pysanitize sanitize sample.pdf --mask-images --image-classes face   # faces
uv run pysanitize sanitize sample.pdf --mask-images --image-classes text   # printed text (needs --extra image-ocr)

# 4) Restrict fields + write a raw-value audit report (local review only, do not share)
uv run pysanitize sanitize sample.xlsx --fields person_name,phone --audit
```

Each run produces a job directory (default `output/<doc-name>/`):

```
output/<doc-name>/
├── sanitized.md            # sanitized Markdown (image links point into images_masked/)
├── images_masked/          # every extracted image — masked copies, or originals when masking is off
└── audit.json              # public summary: hit counts + masked text, no raw values
```

With `--audit`, an extra `sensitive_report.json` is written (raw values + char offsets, for local review — **do not share**). Any flag you don't pass falls back to `config/pipeline.yaml`; explicit flags win.

## 🐍 Python API

```python
from pysanitize.pipeline import sanitize_document

result = sanitize_document(
    "contract.pdf",
    detector="hybrid",          # rules | llm | hybrid
    llm_model="qwen3.6-27b",    # config/llm/<model>.yaml filename
    llm_provider="pingan",      # provider section in that yaml: openai | pingan
    fields=["phone", "company_name", "person_name"],
    mask_images=True,
    image_classes=["face"],     # image targets: face | text | <YOLO class>; empty = none
    audit=False,
)
print(result.sanitized_md)     # Path
print(result.detections)       # each with field_type / start / end / masked_value
```

## 🔍 Detection modes

| mode | description |
|---|---|
| `rules` | pure-local regex + dictionary heuristics (ID/USCC checksums, surname dict, company suffix dict), **offline-ready** |
| `llm` | LLM *locates* spans per chunk: returns `{"field_type", "value"}`, value must be a verbatim substring, then re-matched for exact offsets |
| `hybrid` | runs both; rules win on overlap |

The LLM only locates, never rewrites, with a **hallucination hard gate**: a value that doesn't re-match the source is dropped (rather miss than be wrong); `temperature=0` + `response_format=json_object`.

Chunking is block-aware and adapts to each document's heading structure: `text.chunking.title_level_limit` (`auto` by default, or a fixed level, 0 = top) picks which title level opens a new LLM call, so major chapters split calls while minor headings accumulate; tables always stand alone and every chunk is an exact slice of the document text. `text.chunking.chunk_size` sets the target chars per call.

## 📋 Default sensitive fields (`config/fields.yaml`)

| field_type | label | default mask |
|---|---|---|
| `phone` | phone number `1[3-9]xxxxxxxxx` | `138****5678` (keep head 3 tail 4) |
| `id_card` | national ID number, GB 11643 checksum (toggleable) | `110105********1239` (keep head 6 tail 4) |
| `credit_code` | unified social credit code, GB 32100 checksum | `************000N` (keep tail 4) |
| `email` | email address | `****@***` |
| `stock_code` | A-share code starting `60/68/00/30` | `******` |
| `bank_account` | 16-19 digit number (low confidence, **off by default**) | `6222***********5678` (keep head 4 tail 4) |
| `person_name` | surname dictionary + context heuristics | `***` |
| `company_name` | company-suffix dictionary + boundary pruning | `****` |

Fields are fully configurable — add/remove/edit a line in `config/fields.yaml`; `--fields a,b` detects only what you name.

## 🖼️ Image masking (class-driven)

Images aren't only faces — they can hold company names, seals, screenshots of text. List the targets to mask in `image.classes` / `--image-classes`; **no targets = no image is touched** (rather miss than wrongly mosaic).

| class | description | dependency |
|---|---|---|
| `face` | faces: `auto` (default, YuNet auto-downloaded ~340KB on first use, offline fallback Haar) / `yunet` / `haar` / `yolo` | opencv (bundled) |
| `text` | OCR text regions: **all** printed text in company names, seals, screenshots | `--extra image-ocr` |
| other (`person`, `car`…) | YOLO general object detection, filtered by class name | `--extra image-yolo` + `--image-model` |

```bash
uv run pysanitize sanitize contract.pdf --mask-images --image-classes face
uv run pysanitize sanitize contract.pdf --mask-images --image-classes text
uv run pysanitize sanitize contract.pdf --mask-images --image-classes person,car --image-model yolov8n.pt
```

The mosaic is a NEAREST block mosaic (default 16px) that covers only the detected boxes — everything outside is preserved.

## ⚙️ LLM provider config

`--model` = filename of `config/llm/<model>.yaml`; `--provider` = a **provider section** in that yaml (`openai:` / `pingan:`). One yaml can hold several sections, so switching intranet/extranet is just a flag change:

```bash
uv run pysanitize sanitize contract.pdf --detector hybrid --provider pingan --model qwen3.6-27b
```

- `api_key` always uses a `${ENV_VAR}` placeholder, expanded from the environment at runtime — **plaintext keys never enter the repo**
- A missing section errors clearly: `no 'pingan' section in .../qwen3.6-27b.yaml; have: openai`
- Global defaults live in `config/pipeline.yaml` under `text.model` / `text.provider`

## 🔒 Security boundaries & known limits

- **Scans** (no text layer): M1 desensitizes only the OCR'd Markdown; M2 will do coordinate-level redaction with `middle.json` bboxes
- **Person / company names** are dictionary heuristics with limited precision; use `hybrid` for sensitive material
- **LLM hallucination**: the verbatim re-match gate means misses are far likelier than false positives
- **Image masking is off by default**: you must pass `--image-classes`; `text` mosaics **all** printed text in an image
- **Long-number false positives** (18-digit figures in finance tables): checksums on by default + `bank_account` off by default
- MinerU downloads models on first run; `mineru[pipeline]` is heavy (torch/opencv)

## 🛠️ Development

```bash
uv run pytest          # unit tests green
```

```
pysanitize/
├── parser/     MinerU wrapper + ParsedDocument (text / blocks / image pairing / offset mapping)
├── detector/   rules / llm / registry (overlap resolution) / image (face / OCR text / YOLO classes)
├── masker/     text (offset masking) / image (mosaic)
├── pipeline.py sanitize_document() orchestration (the only public entry)
├── cli.py      argparse CLI
├── llm/        LLM facade (openai / pingan providers)
└── report.py   audit.json / sensitive_report.json
config/         local overrides (git-ignored, optional): fields.yaml (field specs) / pipeline.yaml (stage switches) / llm/*.yaml (model config); built-in defaults apply without it
```

Extension points: **add a field** → edit `fields.yaml`; **add a detector** → write a class into the registry; **add an image target** → write a class into the `build_detectors` route; **add an output format** → add a renderer in M2. Every interface has a single method; nothing touches the core.

## 🗺️ Roadmap

- **M2**: preserve original layout — PDF redaction via `middle.json` bboxes + PyMuPDF `apply_redactions` (true deletion); DOCX/Excel in-place editing; metadata cleaning, anonymized output names
- **M3**: WebUI (upload + task queue + progress)

---

<div align="center">

Made with 🛡️ for safer documents · [Report an issue](https://github.com/LyoAI/PySanitize/issues)

</div>

<div align="center">

# 🛡️ PySanitize

**Multi-format document desensitization · local parsing · rules + LLM locating · class- and text-driven image mosaicing · layout-preserving PDF redaction**

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.2.0-4A90D9)
![Tests](https://img.shields.io/badge/tests-143%20passed-brightgreen)
![Parse](https://img.shields.io/badge/parse-local%20MinerU-2E7D32)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-blue)
![CI](https://github.com/LyoAI/PySanitize/actions/workflows/ci.yml/badge.svg)

**PDF · DOCX · Excel · scans** → **sanitized Markdown + mosaiced images + audit report + redacted PDF**

English | [中文](./README_ZH.md)

</div>

PySanitize desensitizes sensitive information in **PDF / DOCX / Excel / scanned documents**, fully **local** parsing on top of [MinerU](https://github.com/opendatalab/MinerU) — no cloud calls.

## ✨ Features

| | |
|---|---|
| 🧩 **Multi-format input** | PDF / image / DOCX / PPTX / XLSX parsed into one unified structure |
| 🔍 **Rules + LLM dual engine** | regex + dictionary heuristics (offline-ready); the LLM only *locates*, never rewrites, with verbatim re-match against hallucination |
| 🖼️ **Image masking** | `face` / any YOLO class (detection models) **and** text-driven — bare `--image-text` mosaics all printed text, or a field list mosaics only matching fields (e.g. a company name on a seal) → mosaic |
| 📄 **PDF redaction** | `--redact-pdf` also yields a layout-preserving `redacted.pdf` — sensitive glyphs truly *deleted*, mosaic on top, table borders intact |
| 🔁 **Reversible masking** | `--recoverable` records each value's ciphertext in `audit.json`; `pysanitize --recover` restores the original with the passphrase — the sanitized document itself looks like any normal run |
| 🔌 **Switchable providers** | `--provider openai \| pingan`, flip between intranet/extranet |
| 📊 **Audit-friendly** | public summary has no raw values; the raw-value report is only written with `--audit` |
| 🛡️ **Fault-tolerant** | missing keys / optional deps / model downloads degrade with a warning, never a hard crash |

## 🔄 Pipeline

```text
input(PDF/DOCX/Excel/scan)
  → [parser]      MinerU middle.json (per-line geometry) → ParsedDocument{text, blocks, images/}
  → [detector]    rules + LLM locate sensitive fields → exact char offsets
  → [masker]      mask by field type (138****5678 / **** / keep head-tail N)
  → [image]       class- (face/YOLO) + text-driven (OCR → all text or matching fields) → PIL mosaic
  → [redact]      (PDF only, opt-in) offsets → page rects → redacted.pdf (glyphs deleted, mosaic)
  → output        sanitized.md + images_masked/ + audit.json
                  (--recoverable: audit.json also carries the ciphertext → restorable with --recover)
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
PYSANITIZE_RECOVER_KEY=... # --recoverable / --recover passphrase (optional; else --recover-key / .recover.key)
```

Optional features, unlock on demand:

```bash
uv sync --extra image-yolo      # YOLO general object detection (ultralytics)
uv sync --extra image-ocr       # OCR text-region detection (paddleocr, large)
uv sync --extra tui             # interactive TUI frontend (textual)
uv sync --extra recover         # reversible masking (--recoverable / --recover; cryptography)
```

## 🚀 Quick start

```bash
# 1) Pure local rules detection (offline, no LLM)
uv run pysanitize sample.pdf

# 2) Hybrid: rules + LLM locating (default openai/deepseek-v4-flash)
uv run pysanitize sample.pdf --detector hybrid

#    pick the LLM provider + model (finsearch-bench style)
uv run pysanitize sample.pdf --detector hybrid --provider pingan --model qwen3.6-27b
uv run pysanitize sample.pdf --detector llm     --provider openai  --model qwen3-max

# 3) Image masking: no image is detected by default — name your targets explicitly
uv run pysanitize sample.pdf --mask-images --image-classes face   # faces
uv run pysanitize sample.pdf --mask-images --image-text         # all printed text (needs --extra image-ocr)

# 4) Restrict fields + write a raw-value audit report (local review only, do not share)
uv run pysanitize sample.xlsx --fields person_name,phone --audit

# 5) Layout-preserving PDF redaction (opt-in for PDF inputs)
uv run pysanitize sample.pdf --redact-pdf                     # writes output/<stem>/redacted.pdf
uv run pysanitize sample.pdf --redact-pdf --redaction-style block   # solid box instead of mosaic

# 6) Field-driven image masking: OCR the images, mask only the matching fields
uv run pysanitize sample.pdf --mask-images --image-text company_name,address

# 7) Reversible masking + restore (needs --extra recover)
uv run pysanitize sample.pdf --recoverable
#    → output looks like a normal run; audit.json records each value's ciphertext
uv run pysanitize output/sample/sanitized.md --recover          # md restores exactly
uv run pysanitize output/sample/redacted.pdf --recover          # pdf: values back, layout approximate
```

Each run produces a job directory (default `output/<doc-name>/`):

```
output/<doc-name>/
├── sanitized.md            # sanitized Markdown (image links point into images_masked/)
├── images_masked/          # every extracted image — masked copies, or originals when masking is off
├── redacted.pdf            # PDF inputs with --redact-pdf: original layout, regions deleted + mosaiced
├── audit.json              # public summary: hit counts + masked text, no raw values
│                             with --recoverable: also each span's ciphertext + position
└── .recover.key            # with --recoverable: generated passphrase (0600) when none was supplied
```

With `--audit`, an extra `sensitive_report.json` is written (raw values + char offsets, for local review — **do not share**). Any flag you don't pass falls back to `config/pipeline.yaml`; explicit flags win.

## 🖥️ Interactive TUI

```bash
uv sync --extra tui        # one-time: installs Textual
uv run pysanitize --launch tui
```

A six-tab terminal UI (Textual): **Fields** — checkbox-select the sensitive field types from `config/fields.yaml`; **Options** — input file, detection mode, LLM endpoint, output, plus the **Recoverable** switch and a password-masked **Recovery key** (blank = env / `.recover.key` / auto-generate); **③ Image** — image masking targets (enable, class list, all-text switch, detector) plus a "Same as text" toggle that lets you pick a different (possibly larger) field set to OCR inside images; **Run** — type free-form requirements that are appended to the LLM prompt, then run with a live log; **Results** — per-field hit counts and output paths; **⑥ Recover** — point at a `sanitized.md` / `redacted.pdf` (its `audit.json` beside it), optionally type the passphrase, and restore the original in place of the CLI's `--recover`. Quit with the ✕ button or `ctrl+c` (the default `ctrl+q` is swallowed by some terminals, and `cmd+q` belongs to macOS). The plain CLI stays the primary interface — the TUI is a convenience layer over the same pipeline (`pysanitize.core.run_sanitizer`).

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
    image_fields=["company_name", "address"],  # None = same as fields; [] = no image-field masking
    redact_pdf=True,            # opt-in: also write redacted.pdf for PDF sources
    redaction_style="mosaic",   # mosaic | block
    audit=False,
    recoverable=True,           # audit.json records ciphertext for --recover (needs the recover extra)
    recover_key="passphrase",   # else $PYSANITIZE_RECOVER_KEY, else generated .recover.key
)
print(result.sanitized_md)     # Path
print(result.redacted_pdf)     # Path | None (PDF sources with resolvable regions)
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

## 🖼️ Image masking

Images aren't only faces — they can hold company names, seals, screenshots of text. There are two complementary ways to decide what gets mosaiced; **with no targets at all, no image is touched** (rather miss than wrongly mosaic).

### Class-driven (`--image-classes`)

`--image-classes` lists the *objects* to mask — anything a detection model can name (faces, people, door plates, storefront signs …):

| class | description | dependency |
|---|---|---|
| `face` | faces: `auto` (default, YuNet auto-downloaded ~340KB on first use, offline fallback Haar) / `yunet` / `haar` / `yolo` | opencv (bundled) |
| anything else (`person`, `car`, …) | YOLO general object detection, filtered by class name; non-standard targets (door plates, signage) need custom weights | `--extra image-yolo` + `--image-model` |

```bash
uv run pysanitize contract.pdf --mask-images --image-classes face
uv run pysanitize contract.pdf --mask-images --image-classes person,car --image-model yolov8n.pt
```

### Text-driven (`--image-text`)

A company name or registered address rarely fits a *class* — it's text inside a logo or seal. Text in images is handled by OCR (`--extra image-ocr`), at either granularity:

```bash
uv run pysanitize contract.pdf --mask-images --image-text                        # ALL printed text
uv run pysanitize contract.pdf --mask-images --image-text company_name,address   # only matching fields
```

- **Bare** `--image-text` mosaics every OCR'd text region (seals, screenshots, stamps)
- **With a field list**, the **same field detectors** as the text pipeline run over the OCR'd text and only the matching spans are mosaiced
- With neither form, field-driven masking **follows the text field set** (`--fields`); `image.fields: []` in `config/pipeline.yaml` disables it
- An explicit list may be a **superset** (an address stamped on a seal that never appears in the body text)
- `--image-classes` is unaffected and still runs alongside
- Near-full-page scan images are skipped (their text is already handled as document text)

The mosaic is a NEAREST block mosaic (default 16px) that covers only the detected boxes — everything outside is preserved.

## 📄 PDF redaction (`redacted.pdf`)

With `--redact-pdf`, the pipeline additionally writes `redacted.pdf` next to `sanitized.md` for PDF sources: the **original layout is preserved** while every detected span is truly *deleted* from the content stream and replaced with a mosaic (`--redaction-style mosaic`, default) or a solid box (`block`). Table borders and vector graphics stay; overlapping image pixels are cleared.

- Coordinates come from MinerU's `middle.json` per-line bboxes; in-line hits are placed by proportional char width (CJK is effectively monospaced)
- **Tables**: middle 3.x exposes no cell coordinates, so a hit in a table redacts the whole table bbox — conservative over-redaction, safe by design
- **Images**: regions of images that were *actually* mosaiced are stamped back, so the PDF page matches `images_masked/`; unmasked images stay untouched
- Scanned pages (no text layer) are naturally skipped by verification, which downgrades any leftover to a warning, never a failure
- Opt-in: pass `--redact-pdf`, or set `output.redact_pdf: true` in `config/pipeline.yaml` to make it the default; office inputs never produce one

### Why PyMuPDF (AGPL)

MinerU only **reads** PDFs — it has no writer, so re-rendering would lose fonts, table lines and backgrounds and still need a writer. PyMuPDF is the only dependency that reads *and* faithfully rewrites a PDF (true glyph deletion). It is **AGPL-licensed**: fine for internal tooling, but review the implications before embedding PySanitize in a closed-source product.

## 🔁 Recoverable masking (`--recoverable` / `--recover`)

By default masking is one-way. With `--recoverable` (needs `uv sync --extra recover`), the sanitized document **looks exactly like a normal run** — same user-configured placeholders (`138****5678`, `***`, …) — but every value's ciphertext is recorded in `audit.json`, so the original can be restored later with the passphrase. Encryption is AES-256-GCM, keyed via scrypt from the passphrase; the same value always yields the same ciphertext within a run.

```bash
uv run pysanitize sample.pdf --recoverable                        # key generated into output/<doc>/.recover.key
uv run pysanitize sample.pdf --recoverable --recover-key s3cret   # or pass a passphrase
PYSANITIZE_RECOVER_KEY=s3cret uv run pysanitize sample.pdf --recoverable   # or via the environment

# restore later — audit.json must sit beside the file (or pass --recover-audit):
uv run pysanitize output/sample/sanitized.md --recover
uv run pysanitize output/sample/redacted.pdf --recover --recover-key s3cret
```

What the audit records (recovery mode only):

- A `recovery` block — algorithm, KDF name, scrypt salt and parameters, ciphertext format. The salt is public by design; **key material never touches the audit**. Anyone holding the passphrase can recover; nobody without it can.
- Per span: `encrypted_value` (the `ENC(v1:…)` ciphertext — `masked_value` stays the normal placeholder), `start`/`end` (offsets in the original text), `md` (where the placeholder sits in `sanitized.md`), and `rects` (the redacted PDF rectangles).

How restoration works:

- **Markdown restores exactly** — the decrypted value is spliced back at the recorded `md` range (each splice verifies the placeholder is still there, so an edited document counts as unresolved instead of corrupting text).
- **PDF restores best-effort** — the original glyphs were truly deleted at sanitize time; recovery clears the recorded `rects` and re-inserts the decrypted value with a fitted font: the *values* come back, the original typography does not. **Images are not recoverable** (mosaicing is destructive by nature).
- Recovery is an independent consumer (`pysanitize/recover/`): it never imports the sanitize pipeline — only the audit + passphrase — and it never invents a key: with no passphrase available it fails instead.

> ⚠️ With `--recoverable`, **`audit.json` carries the ciphertext** of every sensitive value. Its secrecy reduces to passphrase strength: distribute it only where the document may be restored, and keep the generated `.recover.key` (0600) as confidential as the data itself. The sanitized document itself remains as shareable as any normal run.

## ⚙️ LLM provider config

`--model` = filename of `config/llm/<model>.yaml`; `--provider` = a **provider section** in that yaml (`openai:` / `pingan:`). One yaml can hold several sections, so switching intranet/extranet is just a flag change:

```bash
uv run pysanitize contract.pdf --detector hybrid --provider pingan --model qwen3.6-27b
```

- `api_key` always uses a `${ENV_VAR}` placeholder, expanded from the environment at runtime — **plaintext keys never enter the repo**
- A missing section errors clearly: `no 'pingan' section in .../qwen3.6-27b.yaml; have: openai`
- Global defaults live in `config/pipeline.yaml` under `text.model` / `text.provider`

## 🔒 Security boundaries & known limits

- **Scans** (no text layer): the OCR'd Markdown is desensitized; `redacted.pdf` verification naturally finds nothing to remove there
- **Tables redact whole-table in the PDF** (cell coordinates don't exist in middle 3.x); the `sanitized.md` output is still cell-precise
- **Person / company names** are dictionary heuristics with limited precision; use `hybrid` for sensitive material
- **LLM hallucination**: the verbatim re-match gate means misses are far likelier than false positives
- **Image masking is off by default**: you must pass `--image-classes` and/or `--image-text`; bare `--image-text` mosaics **all** printed text in an image
- **Long-number false positives** (18-digit figures in finance tables): checksums on by default + `bank_account` off by default
- **PyMuPDF is AGPL-licensed** — used only for `redacted.pdf`; fine for internal tooling, review before closed-source distribution
- **`--recoverable` puts ciphertext in `audit.json`** — the document stays shareable, but the audit + `.recover.key` (0600) must be guarded like the data itself
- MinerU downloads models on first run; `mineru[pipeline]` is heavy (torch/opencv)

## 🛠️ Development

```bash
uv run pytest          # unit tests green
```

```
pysanitize/
├── parser/     MinerU wrapper (middle.json projection + v2 fallback) + ParsedDocument (line geometry / image pairing / offset mapping)
├── detector/   rules / llm / registry (overlap resolution) / image (face / YOLO classes / OCR text & field-driven)
├── masker/     text (offset masking) / image (mosaic)
├── redact/     offsets → page rects, PyMuPDF redaction + verification
├── recover/    reversible tokens (AES-GCM + scrypt) and pipeline-independent restoration
├── pipeline.py sanitize_document() orchestration (the only public entry)
├── cli.py      argparse CLI
├── llm/        LLM facade (openai / pingan providers)
└── report.py   audit.json / sensitive_report.json
config/         local overrides (git-ignored, optional): fields.yaml (field specs) / pipeline.yaml (all pipeline tunables) / llm/*.yaml (model config); built-in defaults apply without it
```

Extension points: **add a field** → edit `fields.yaml`; **add a detector** → write a class into the registry; **add an image target** → write a class into the `build_detectors` route; **add an output format** → add a renderer in M2. Every interface has a single method; nothing touches the core.

## 🗺️ Roadmap

- **M2**: PDF redaction is shipped (`redacted.pdf`); remaining: DOCX/Excel in-place editing, metadata cleaning, anonymized output names
- **M3**: WebUI (upload + task queue + progress)

---

<div align="center">

Made with 🛡️ for safer documents · [Report an issue](https://github.com/LyoAI/PySanitize/issues)

</div>

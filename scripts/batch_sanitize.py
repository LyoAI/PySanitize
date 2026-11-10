#!/usr/bin/env python
"""Batch-sanitize every supported document under a folder.

Walks the input folder (recursively), dispatches each file to the pipeline
according to its type — the same adaptivity the pipeline itself applies:

- structured sources (``.xlsx`` / ``.json``) redact **in place** (native
  producers, no MinerU, no markdown): ``redacted.xlsx`` / ``redacted.json``
- document sources (``.pdf`` / ``.docx`` / ``.pptx`` / images) go through
  MinerU → ``sanitized.md`` (+ ``redacted.pdf`` with ``--redact-pdf``)

Each source file gets one job directory under the output root. A flat input
maps 1:1 (``upload_materials/report.xlsx`` → ``output/report/``); nested
input keeps its relative layout, so same-named files in different folders
never collide (``upload/a/report.xlsx`` → ``output/a/report/``).

Per-file failures are isolated: one broken document never aborts the batch.
The batch is **resumable**: a file whose job dir holds a complete result
(``audit.json`` plus every artifact the audit names) is skipped, so an
interrupted run — Ctrl-C, a crash, a reboot — is continued simply by rerunning
the same command; the file that was in flight when it stopped is redone (it
never got its audit). ``--resume`` states that behaviour explicitly (it is the
default); ``--force`` redoes everything instead. The summary is rewritten
after every file, so even an interrupted run leaves a usable
``batch_summary.json`` (``"status": "interrupted"``) with per-file status and
field hit counts (masked values only, no raw PII).

Usage:
    uv run python scripts/batch_sanitize.py upload_materials -o output
    uv run python scripts/batch_sanitize.py upload_materials -o output --resume
    uv run python scripts/batch_sanitize.py upload_materials --detector hybrid --recoverable
    uv run python scripts/batch_sanitize.py upload_materials --force --audit
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from pysanitize.parser.mineru import SUPPORTED_SUFFIXES as _MINERU_SUFFIXES
from pysanitize.pipeline import SanitizeResult, sanitize_document

STRUCTURED_SUFFIXES = {".xlsx", ".json"}  # in-place redaction, no MinerU / no md
SUPPORTED_SUFFIXES = set(_MINERU_SUFFIXES) | STRUCTURED_SUFFIXES

SUMMARY_NAME = "batch_summary.json"
_DONE_MARKER = "audit.json"  # + the artifacts it names — see is_done()


# ---------------------------------------------------------------- collection


def collect_files(in_root: Path, out_root: Path) -> tuple[list[Path], list[Path]]:
    """Supported files under ``in_root`` (recursive, sorted), plus the
    unsupported ones worth mentioning (hidden/cache files are ignored outright).
    Files living under ``out_root`` are skipped — never re-sanitize outputs.
    """
    supported: list[Path] = []
    unsupported: list[Path] = []
    for p in sorted(in_root.rglob("*")):
        if not p.is_file() or out_root in p.parents:
            continue
        if any(part.startswith((".", "__")) for part in p.relative_to(in_root).parts):
            continue  # .git, .DS_Store, __pycache__, .cache …
        (supported if p.suffix.lower() in SUPPORTED_SUFFIXES else unsupported).append(p)
    return supported, unsupported


def job_dir_for(src: Path, in_root: Path, out_root: Path, claimed: dict[Path, Path]) -> Path:
    """One job directory per source file: ``<out_root>/<rel parent>/<stem>``.

    If two same-named stems meet (``report.pdf`` + ``report.json`` in one
    folder), the later one is disambiguated by suffix (``report-json/``).
    """
    d = out_root / src.parent.relative_to(in_root) / src.stem
    if claimed.get(d) not in (None, src):
        d = d.with_name(f"{d.name}-{src.suffix.lstrip('.')}")
    claimed[d] = src
    return d


# ------------------------------------------------------------------- the run


def is_done(src: Path, job_dir: Path) -> bool:
    """Is this job dir's result complete enough to skip on a resumed run?

    ``audit.json`` alone is not enough: it is written last in the pipeline,
    but an artifact it names can still be missing — deleted by hand, eaten by
    a half-full disk, truncated by a crash mid-write, or an audit from a
    killed run that never finished. Such a file is *redone*, not skipped;
    skipping it would leave the operator with an audit for an artifact that
    does not exist.
    """
    audit = job_dir / _DONE_MARKER
    if not audit.is_file():
        return False
    try:
        redaction = json.loads(audit.read_text(encoding="utf-8")).get("redaction") or {}
    except (OSError, json.JSONDecodeError):
        return False  # unreadable/truncated audit — redo the file
    named = (redaction.get("pdf"), redaction.get("xlsx"), redaction.get("json"))
    if any(name and not (job_dir / name).is_file() for name in named):
        return False
    # Document sources always ship sanitized.md beside the audit.
    if src.suffix.lower() not in STRUCTURED_SUFFIXES and not (job_dir / "sanitized.md").is_file():
        return False
    return True


def write_summary(out_root: Path, summary: dict) -> Path:
    """(Re)write ``batch_summary.json`` — called after every file, so an
    interrupted run still leaves the operator a usable record."""
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / SUMMARY_NAME
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_one(src: Path, job_dir: Path, args: argparse.Namespace) -> SanitizeResult:
    """Dispatch one file with the CLI's argument mapping (absent flag → None
    → config decides; present flag → True)."""
    return sanitize_document(
        src,
        detector=args.detector,
        fields=args.fields,
        llm_provider=args.provider,
        llm_model=args.model,
        mask_images=True if args.mask_images else None,
        image_classes=args.image_classes,
        redact_pdf=True if args.redact_pdf else None,
        redaction_style=args.redaction_style,
        audit=True if args.audit else None,
        recoverable=True if args.recoverable else None,
        recover_key=args.recover_key,
        out_dir=job_dir,
    )


def artifact_of(r: SanitizeResult) -> str:
    """Human names of the artifacts this run produced."""
    names = []
    if r.redacted_json is not None:
        names.append("redacted.json")
    if r.redacted_xlsx is not None:
        names.append("redacted.xlsx")
    if r.redacted_pdf is not None:
        names.append("redacted.pdf")
    if r.sanitized_md is not None:
        names.append("sanitized.md")
    return " + ".join(names) or "(no artifact — nothing detected?)"


def field_counts(r: SanitizeResult) -> dict[str, int]:
    return dict(sorted(Counter(d.field_type for d in r.detections).items()))


# ---------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-sanitize a folder: each file routed by type "
        "(xlsx/json in place; pdf/docx/pptx/images via MinerU).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_dir", help="folder to walk (e.g. upload_materials)")
    parser.add_argument("-o", "--out-dir", default="output", help="output root; one job dir per file")
    parser.add_argument("--detector", default="rules", choices=["rules", "llm", "hybrid"],
                        help="rules runs fully offline — the batch default")
    parser.add_argument("--fields", help="comma-separated field types (default: all enabled)")
    parser.add_argument("--provider", help="LLM provider section (llm/hybrid only)")
    parser.add_argument("--model", help="LLM model yaml name (llm/hybrid only)")
    parser.add_argument("--redact-pdf", action="store_true",
                        help="also write redacted.pdf for PDF sources")
    parser.add_argument("--redaction-style", choices=["mosaic", "block"], help="PDF redaction style")
    parser.add_argument("--mask-images", action="store_true", help="enable image masking")
    parser.add_argument("--image-classes", help="comma-separated image targets (face/text/YOLO classes)")
    parser.add_argument("--audit", action="store_true",
                        help="write sensitive_report.json (raw values — local review only)")
    parser.add_argument("--recoverable", action="store_true",
                        help="record ciphertext for later --recover (needs the recover extra)")
    parser.add_argument("--recover-key", help="passphrase for --recoverable (else generated → .recover.key)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true",
                      help="continue an interrupted batch: skip files whose result "
                           "is already complete (this is the DEFAULT — pass the flag "
                           "to state it explicitly; rerun the same command after "
                           "Ctrl-C and it resumes)")
    mode.add_argument("--force", action="store_true",
                      help="redo every file, including ones already sanitized")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    in_root = Path(args.input_dir).resolve()
    out_root = Path(args.out_dir).resolve()
    if not in_root.is_dir():
        print(f"error: not a folder: {in_root}", file=sys.stderr)
        return 2
    if in_root == out_root or out_root in in_root.parents:
        print(f"error: output root must not contain the input folder", file=sys.stderr)
        return 2
    if args.fields:
        args.fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    if args.image_classes:
        args.image_classes = [c.strip() for c in args.image_classes.split(",") if c.strip()]

    files, unsupported = collect_files(in_root, out_root)
    print(f"batch sanitize: {in_root}  →  {out_root}")
    print(f"  {len(files)} supported file(s)"
          + (f", {len(unsupported)} unsupported (skipped)" if unsupported else ""))

    records: list[dict] = []
    claimed: dict[Path, Path] = {}
    ok = skipped = failed = 0
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def summary(status: str) -> dict:
        """The record written after every file — `status` is running /
        interrupted / complete, so an aborted run is self-describing."""
        return {
            "input": str(in_root),
            "output_root": str(out_root),
            "status": status,
            "detector": args.detector,
            "fields": args.fields,
            "started": started_at,
            "duration_s": round(time.monotonic() - started, 1),
            "totals": {"sanitized": ok, "skipped": skipped, "failed": failed,
                       "unsupported": len(unsupported)},
            "files": records,
            "unsupported": [str(p.relative_to(in_root)) for p in unsupported],
        }

    # Resume: say up front how much of the batch is already done, so a rerun
    # after an interruption starts with an honest picture instead of a silent
    # stream of "skipped" lines.
    if not args.force:
        probe: dict[Path, Path] = {}
        done = sum(is_done(s, job_dir_for(s, in_root, out_root, probe)) for s in files)
        if done:
            print(f"  resuming: {done} already sanitized, {len(files) - done} to do "
                  f"(--force redoes everything)")

    in_flight: dict | None = None
    try:
        for i, src in enumerate(files, 1):
            job_dir = job_dir_for(src, in_root, out_root, claimed)
            kind = "in-place" if src.suffix.lower() in STRUCTURED_SUFFIXES else "mineru"
            label = f"[{i}/{len(files)}] {src.relative_to(in_root)}"
            if not args.force and is_done(src, job_dir):
                skipped += 1
                records.append({"source": str(src.relative_to(in_root)), "job_dir": str(job_dir),
                                "status": "skipped (already sanitized)"})
                print(f"{label:<58} skipped (already sanitized; --force to redo)")
                write_summary(out_root, summary("running"))
                continue
            t0 = time.monotonic()
            # say we started, with the size — a big file otherwise sits silent
            # for minutes and looks hung; flush so piped/tee'd output shows it
            print(
                f"{label:<58} processing [{kind}] ({src.stat().st_size / 1_048_576:.1f} MB)",
                flush=True,
            )
            in_flight = {
                "source": str(src.relative_to(in_root)),
                "job_dir": str(job_dir),
                "route": kind,
                "status": "interrupted",
            }
            try:
                result = run_one(src, job_dir, args)
            except Exception as exc:  # isolate: one bad file never kills the batch
                failed += 1
                in_flight = None
                records.append({"source": str(src.relative_to(in_root)), "job_dir": str(job_dir),
                                "status": "failed", "error": str(exc)[:300]})
                print(f"{label:<58} FAILED [{kind}] {exc}")
                write_summary(out_root, summary("running"))
                gc.collect()  # drop whatever the failed run left behind
                continue
            in_flight = None
            duration = time.monotonic() - t0
            ok += 1
            records.append({
                "source": str(src.relative_to(in_root)),
                "job_dir": str(job_dir),
                "status": "sanitized",
                "route": kind,
                "artifacts": artifact_of(result),
                "detections": len(result.detections),
                "fields": field_counts(result),
                "duration_s": round(duration, 2),
            })
            print(f"{label:<58} {len(result.detections):>4} hits  "
                  f"{artifact_of(result):<32} {duration:5.1f}s")
            write_summary(out_root, summary("running"))
            # A large run's result (every detection) is dead once summarized;
            # releasing it here keeps one file's peak from stacking on the next
            # file's. Python's allocator still retains arenas, so RSS settles
            # at the batch's largest file rather than growing monotonically.
            result = None
            gc.collect()
    except KeyboardInterrupt:
        # Ctrl-C mid-batch: keep what is already written and say exactly how
        # to continue. The in-flight file is the one to redo (it never got an
        # audit), so it is recorded as interrupted and retried on the next run.
        if in_flight is not None:
            records.append(in_flight)
        remaining = len(files) - ok - skipped - failed - (1 if in_flight else 0)
        summary_path = write_summary(out_root, summary("interrupted"))
        print(f"\ninterrupted — {ok} sanitized, {skipped} already done, "
              f"{failed} failed, {remaining} not started"
              + (" (the file in flight will be redone)" if in_flight else ""))
        print("resume by rerunning the same command: completed files are skipped")
        print(f"summary: {summary_path}")
        return 130

    elapsed = time.monotonic() - started
    summary_path = write_summary(out_root, summary("complete"))

    print(f"\n{ok} sanitized, {skipped} skipped, {failed} failed, "
          f"{len(unsupported)} unsupported  —  {elapsed:.1f}s total")
    print(f"summary: {summary_path}")
    if unsupported:
        print("unsupported files (no sanitizer for this type):")
        for p in unsupported[:10]:
            print(f"  - {p.relative_to(in_root)}")
        if len(unsupported) > 10:
            print(f"  … and {len(unsupported) - 10} more (see summary)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

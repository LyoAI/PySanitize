#!/usr/bin/env python
"""Batch-recover sanitized artifacts produced with ``--recoverable``.

Walks a folder (recursively — point it at the batch output root), finds every
recoverable artifact by its pipeline name — ``sanitized.md`` /
``redacted.pdf`` / ``redacted.xlsx`` / ``redacted.json`` — and restores the
originals. Each artifact's ``audit.json`` must sit beside it (the pipeline
always puts it there); the passphrase resolves per file exactly like the CLI:
``--recover-key`` > ``$PYSANITIZE_RECOVER_KEY`` > the job's ``.recover.key``.

Artifacts from plain (non-recoverable) runs are classified and skipped, not
failures — a mixed tree recovers only what can be recovered. One broken
artifact never aborts the batch; already-recovered outputs are skipped unless
``--force``. A ``batch_recover_summary.json`` lands beside the summary of the
run, and files with unresolved spans are flagged loudly (someone edited the
sanitized artifact after the run).

``--source-root`` points at the folder that was sanitized: the batch layout
mirrors it (``output/<rel>/<stem>/redacted.json`` ← ``<root>/<rel>/<stem>.json``),
so each redacted.json / redacted.xlsx is recovered together with the source
file, putting back line breaks that a pre-fix sanitize flattened (see
``recover_file(newline_source=...)``). A missing source just means that file
recovers without them.

Default writes ``<stem>_recovered<ext>`` beside each artifact; ``--out-dir``
mirrors the tree into a separate root instead.

Usage:
    uv run python scripts/batch_recover.py output
    uv run python scripts/batch_recover.py output --recover-key s3cret
    uv run python scripts/batch_recover.py output --source-root upload_materials
    uv run python scripts/batch_recover.py output --out-dir recovered --force
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from pysanitize.recover import recover_file

ARTIFACT_NAMES = ("sanitized.md", "redacted.pdf", "redacted.xlsx", "redacted.json")
AUDIT_NAME = "audit.json"
SUMMARY_NAME = "batch_recover_summary.json"
_DONE_MARKER = "_recovered"  # outputs carry it in their filename


# ---------------------------------------------------------------- collection


def collect_artifacts(in_root: Path, out_root: Path | None) -> list[Path]:
    """Recoverable artifacts under ``in_root`` (recursive, sorted). Exact
    pipeline names only — audit.json / summaries / recovered outputs never
    match. With ``--out-dir`` inside the scanned tree, its contents are skipped.
    """
    found: list[Path] = []
    for p in sorted(in_root.rglob("*")):
        if not p.is_file() or p.name not in ARTIFACT_NAMES:
            continue
        if out_root is not None and out_root in p.parents:
            continue
        if any(part.startswith((".", "__")) for part in p.relative_to(in_root).parts):
            continue
        found.append(p)
    return found


def classify(audit_path: Path) -> str | None:
    """Why an artifact cannot be recovered — None when it can.

    Reads just the audit's public shape: no audit beside it (plain run wrote
    nothing to recover against), or no enabled recovery block (the run was
    not ``--recoverable``). Everything else is recoverable or a real failure.
    """
    if not audit_path.is_file():
        return "no audit.json beside artifact (plain run)"
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "audit.json is not valid JSON"
    rec = audit.get("recovery")
    if not rec or not rec.get("enabled"):
        return "run was not --recoverable (no recovery block)"
    return None


def target_for(artifact: Path, in_root: Path, out_root: Path | None) -> Path:
    """``<stem>_recovered<ext>`` beside the artifact, or mirrored under out_root."""
    name = f"{artifact.stem}_recovered{artifact.suffix}"
    if out_root is None:
        return artifact.parent / name
    return out_root / artifact.parent.relative_to(in_root) / name


def source_for(artifact: Path, in_root: Path, source_root: Path) -> Path | None:
    """The original document behind a ``redacted.json`` / ``redacted.xlsx``.

    The batch mirrors the input tree (``output/a/report/redacted.json`` came
    from ``upload_materials/a/report.json``), so the job directory names the
    stem. A same-stem collision gets a suffix (``report-json/``), tried second
    so a genuine ``report-json.json`` still wins. Only exact-source recovery
    uses this; anything else is recovered as before.
    """
    ext = artifact.suffix.lower()
    if artifact.name not in ("redacted.json", "redacted.xlsx"):
        return None
    job = artifact.parent
    try:
        rel_parent = job.parent.relative_to(in_root)
    except ValueError:
        return None
    stems = [job.name]
    if job.name.lower().endswith(f"-{ext.lstrip('.')}"):
        stems.append(job.name[: -len(ext)])  # "report-json" → "report"
    for stem in stems:
        candidate = source_root / rel_parent / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
        # original suffix may differ in case (REPORT.XLSX)
        for p in (source_root / rel_parent).glob(f"{stem}.*"):
            if p.suffix.lower() == ext and p.is_file():
                return p
    return None


# ---------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-restore artifacts sanitized with --recoverable "
        "(scans for sanitized.md / redacted.pdf / redacted.xlsx / redacted.json).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_dir", help="folder to walk (e.g. the batch output root)")
    parser.add_argument("-o", "--out-dir",
                        help="write restored files here, mirroring the tree "
                        "(default: beside each artifact)")
    parser.add_argument("--recover-key", help="passphrase (else $PYSANITIZE_RECOVER_KEY, "
                        "else each job's .recover.key)")
    parser.add_argument("--source-root", metavar="DIR",
                        help="the folder that was sanitized: recover redacted.json / "
                        "redacted.xlsx together with its source file, restoring line "
                        "breaks a pre-fix sanitize flattened")
    parser.add_argument("--force", action="store_true",
                        help="re-recover files whose output already exists")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    in_root = Path(args.input_dir).resolve()
    out_root = Path(args.out_dir).resolve() if args.out_dir else None
    if not in_root.is_dir():
        print(f"error: not a folder: {in_root}", file=sys.stderr)
        return 2
    source_root = Path(args.source_root).resolve() if args.source_root else None
    if source_root is not None and not source_root.is_dir():
        print(f"error: not a folder: {source_root}", file=sys.stderr)
        return 2

    artifacts = collect_artifacts(in_root, out_root)
    print(f"batch recover: {in_root}"
          + (f"  →  {out_root}" if out_root else "  (restored files beside their artifacts)"))
    print(f"  {len(artifacts)} recoverable artifact(s)")

    records: list[dict] = []
    ok = skipped = norecover = failed = 0
    partial: list[str] = []
    started = time.monotonic()

    for i, artifact in enumerate(artifacts, 1):
        rel = artifact.relative_to(in_root)
        label = f"[{i}/{len(artifacts)}] {rel}"
        target = target_for(artifact, in_root, out_root)
        if not args.force and target.is_file():
            skipped += 1
            records.append({"artifact": str(rel), "output": str(target),
                            "status": "skipped (already recovered)"})
            print(f"{label:<62} skipped (output exists; --force to redo)")
            continue
        why = classify(artifact.parent / AUDIT_NAME)
        if why:
            norecover += 1
            records.append({"artifact": str(rel), "status": "not recoverable", "reason": why})
            print(f"{label:<62} not recoverable — {why}")
            continue
        t0 = time.monotonic()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)  # recover_file won't
            newline_src = source_for(artifact, in_root, source_root) if source_root else None
            result = recover_file(
                artifact, passphrase=args.recover_key, out_path=target,
                newline_source=newline_src,
            )
        except Exception as exc:  # wrong key, corrupt artifact, missing extra …
            failed += 1
            records.append({"artifact": str(rel), "status": "failed", "error": str(exc)[:300]})
            print(f"{label:<62} FAILED {exc}")
            continue
        duration = time.monotonic() - t0
        ok += 1
        records.append({
            "artifact": str(rel), "output": str(result.output), "status": "recovered",
            "kind": result.kind, "restored": result.restored,
            "unresolved": result.unresolved, "duration_s": round(duration, 2),
            **({"newline_source": str(newline_src)} if newline_src else {}),
        })
        mark = "" if result.unresolved == 0 else f"  ⚠ {result.unresolved} unresolved"
        if result.unresolved:
            partial.append(str(rel))
        print(f"{label:<62} {result.restored:>4} restored  {result.kind:<8} {duration:5.1f}s{mark}")

    elapsed = time.monotonic() - started
    summary_root = out_root or in_root
    summary = {
        "input": str(in_root),
        "output_root": str(out_root or "(beside artifacts)"),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "duration_s": round(elapsed, 1),
        "totals": {"recovered": ok, "skipped": skipped,
                   "not_recoverable": norecover, "failed": failed},
        "files_with_unresolved": partial,
        "files": records,
    }
    summary_root.mkdir(parents=True, exist_ok=True)
    summary_path = summary_root / SUMMARY_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{ok} recovered, {skipped} skipped, {norecover} not recoverable, "
          f"{failed} failed  —  {elapsed:.1f}s total")
    if partial:
        print(f"⚠ {len(partial)} file(s) restored with unresolved spans — the sanitized "
              f"artifact was edited after the run (see summary)")
    print(f"summary: {summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

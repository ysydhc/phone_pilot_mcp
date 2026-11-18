"""Safe display projection for structured business evidence (never executes artifacts)."""

from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack
from phone_pilot.core.safe_io import safe_directory
from pathlib import Path
from urllib.parse import quote


def evidence_projection(run_dir: Path, evidence: dict) -> dict:
    item = dict(evidence)
    item.update(url=None, preview="", available=False)
    try:
        relative = Path(item.get("path") or "")
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid package-relative evidence path")
        path = relative
        if path.suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".mp4",
            ".txt",
            ".log",
            ".json",
            ".xml",
            ".bin",
        }:
            raise ValueError("active or unsupported evidence format is not linked")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("evidence metadata must be a dictionary")
        span = item.get("log_range") or metadata.get("log_range") or {}
        start = max(1, int(span.get("start", 1))) if isinstance(span, dict) else 1
        end = (
            min(max(start, int(span.get("end", start + 39))), start + 199)
            if isinstance(span, dict)
            else 40
        )
        with ExitStack() as stack:
            folder = stack.enter_context(safe_directory(run_dir))
            for part in relative.parts[:-1]:
                folder = stack.enter_context(folder.subdir(part))
            stream = stack.enter_context(folder.open(path.name))
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            if not item.get("sha256") or digest.hexdigest() != item["sha256"]:
                raise ValueError("evidence checksum missing or mismatched")
            if path.suffix.lower() in (".txt", ".log", ".json", ".xml"):
                stream.seek(0)
                preview = []
                number = 1
                while number <= end:
                    line = stream.readline(20001)
                    if not line:
                        break
                    text = line[:20000].decode("utf-8", errors="replace").rstrip("\r\n")
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(20001)
                    if number >= start:
                        preview.append(f"{number}: {text}")
                    number += 1
                item["preview"] = "\n".join(preview)[:20000]
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("evidence changed during preview")
        item["available"] = True
        item["url"] = quote(relative.as_posix(), safe="/")
        item["is_image"] = path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        item["is_video"] = path.suffix.lower() == ".mp4"
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        item.update(available=False, url=None, preview="")
        item["error"] = str(exc)
    return item


def business_projection(run_dir: Path, report: dict) -> dict:
    """Build a display-only copy; leave stored observations and verdicts untouched."""
    result = dict(report)
    # Normalize the persisted flat-attempt schema into grouped display cards.
    if "business_steps" in report:
        grouped = []
        for case in report.get("cases", []):
            for declared in case.get("steps", []):
                step_id = declared.get("business_step_id")
                attempts = [
                    a
                    for a in report.get("business_steps", [])
                    if a.get("case_id") == case.get("case_id")
                    and a.get("business_step_id") == step_id
                ]
                first = attempts[0].get("status", "NOT_RUN") if attempts else "NOT_RUN"
                final = attempts[-1].get("status", "NOT_RUN") if attempts else "NOT_RUN"
                grouped.append(
                    dict(
                        declared,
                        case_id=case.get("case_id"),
                        attempts=attempts,
                        first_result=first,
                        final_result=final,
                        recovery_status="RECOVERED"
                        if first != "PASS" and final == "PASS"
                        else "",
                    )
                )
        result["steps"] = grouped
    evidence = [
        evidence_projection(run_dir, e)
        for e in report.get("evidence", [])
        if isinstance(e, dict)
    ]
    by_id = {e.get("evidence_id"): e for e in evidence}
    steps = []
    for number, original in enumerate(result.get("steps", []), 1):
        step = dict(original)
        step["anchor"] = f"business-step-{number}"
        attempts = []
        for original_attempt in step.get("attempts", []):
            attempt = dict(original_attempt)
            attempt["display_evidence"] = [
                by_id[e] for e in attempt.get("evidence_refs", []) if e in by_id
            ]
            attempts.append(attempt)
        step["attempts"] = attempts
        step["screens"] = [
            e
            for a in attempts[-1:]
            for e in a["display_evidence"]
            if e.get("is_image") and e.get("available")
        ]
        steps.append(step)
    # Tool children use explicit IDs, never action/query or timestamp inference.
    result.update(steps=steps, evidence=evidence)
    return result

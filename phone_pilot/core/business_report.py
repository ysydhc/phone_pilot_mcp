"""声明驱动的业务验收记录 / Declaration-driven business evidence contract.

Tool success and process exit are deliberately independent of business PASS.
All durations use monotonic time; wall timestamps include their UTC offset.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from contextlib import ExitStack
from phone_pilot.core.safe_io import safe_directory, normalize_trusted_aliases
from contextvars import ContextVar
from pathlib import Path
import time
import uuid
from typing import Any

DIMENSIONS = ("ui", "callback_log", "network")
ASSERTION_STATUSES = frozenset(
    {"PASS", "FAIL", "NOT_COLLECTED", "NOT_RUN", "NOT_APPLICABLE", "INCONCLUSIVE"}
)
STEP_STATUSES = ASSERTION_STATUSES | {"BLOCKED"}
SCHEMA_VERSION = "2"


def wall_time() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


class BusinessReport:
    """Internal recorder owned by a RunSession; IDs are never inferred from tools."""

    def __init__(self, run_dir: Path):
        self.run_dir = normalize_trusted_aliases(run_dir)
        self.run_id = self.run_dir.name
        self._validation = ContextVar(f"validation-{id(self)}", default=None)
        self.cases: list[dict] = []
        self.business_steps: list[dict] = []
        self.evidence: list[dict] = []
        self._active: ContextVar[dict | None] = ContextVar(
            f"business-{id(self)}", default=None
        )

    @property
    def active(self) -> dict | None:
        return self._active.get()

    def declare_cases(self, cases: list[dict]) -> None:
        """Declare the denominator before execution; declarations are immutable."""
        if self.cases or self.business_steps:
            raise ValueError("cases must be declared once before execution")
        self.cases = self._validated_cases(cases)
        self.save()

    @staticmethod
    def _validated_cases(cases: list[dict]) -> list[dict]:
        if not isinstance(cases, list):
            raise ValueError("cases must be a list")
        declarations = copy.deepcopy(cases)
        seen_cases: set[str] = set()
        for case in declarations:
            case_id = case["case_id"]
            if not isinstance(case_id, str) or not case_id or case_id in seen_cases:
                raise ValueError("case_id must be unique and nonempty")
            seen_cases.add(case_id)
            case.setdefault("title", case_id)
            if not case.get("steps"):
                raise ValueError("a declared case must contain steps")
            seen_steps: set[str] = set()
            for step in case["steps"]:
                sid = step["business_step_id"]
                if not isinstance(sid, str) or not sid or sid in seen_steps:
                    raise ValueError("business_step_id must be unique within a case")
                seen_steps.add(sid)
                step.setdefault("title", sid)
                step.setdefault("expected", "")
                step.setdefault("required_dimensions", list(DIMENSIONS))
                step.setdefault("not_applicable", {})
                step.setdefault("required_evidence", [])
                required = step["required_dimensions"]
                na = step["not_applicable"]
                if (
                    not isinstance(required, list)
                    or not isinstance(na, dict)
                    or not isinstance(step["required_evidence"], list)
                    or len(required) != len(set(required))
                    or any(
                        not isinstance(reason, str) or not reason.strip()
                        for reason in na.values()
                    )
                ):
                    raise ValueError("invalid dimension/evidence declaration")
                if (set(required) | set(na)) - set(DIMENSIONS):
                    raise ValueError("unknown assertion dimension")
                if set(required) & set(na) or any(
                    not str(reason).strip() for reason in na.values()
                ):
                    raise ValueError(
                        "N/A requires an advance reason and cannot be required"
                    )
                if set(required) | set(na) != set(DIMENSIONS):
                    raise ValueError("each dimension must be required or declared N/A")
        return declarations

    def _declaration(self, case_id: str, business_step_id: str) -> dict:
        for case in self.cases:
            if case["case_id"] == case_id:
                for step in case["steps"]:
                    if step["business_step_id"] == business_step_id:
                        return step
        raise ValueError("business step was not declared")

    def _require_active(self) -> dict:
        if self.active is None:
            raise RuntimeError("no active business step")
        return self.active

    def begin_business_step(
        self,
        case_id: str,
        business_step_id: str,
        *,
        attempt_id: str | None = None,
        preconditions: Any = "",
        action: str = "",
        environment_ref: str = "unknown",
        expected: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        if self.active is not None:
            raise RuntimeError("nested business steps are not allowed")
        declaration = self._declaration(case_id, business_step_id)
        prior = [
            s
            for s in self.business_steps
            if s["case_id"] == case_id and s["business_step_id"] == business_step_id
        ]
        attempt_id = attempt_id or f"a{len(prior) + 1}"
        if (
            not isinstance(attempt_id, str)
            or not attempt_id
            or any(s["attempt_id"] == attempt_id for s in prior)
        ):
            raise ValueError("attempt_id must be unique and nonempty within a step")
        if any(s["finished_at"] is None for s in prior):
            raise RuntimeError("previous attempt is still running")
        started = wall_time()
        step = {
            "run_id": self.run_id,
            "case_id": case_id,
            "business_step_id": business_step_id,
            "attempt_id": attempt_id,
            "title": declaration["title"],
            "preconditions": copy.deepcopy(preconditions),
            "action": action,
            "environment_ref": environment_ref,
            "expected": expected if expected is not None else declaration["expected"],
            "actual": "",
            "started_at": started,
            "finished_at": None,
            "timezone": dt.datetime.now().astimezone().tzname(),
            "monotonic_start_ns": time.monotonic_ns(),
            "monotonic_end_ns": None,
            "duration_ms": None,
            "status": "NOT_RUN",
            "assertions": [],
            "evidence_refs": [],
            "failure_class": None,
            "error_message": "",
            "metadata": copy.deepcopy(metadata or {}),
        }
        if environment_ref == "unknown":
            step["metadata"].setdefault(
                "environment_reason", "environment fingerprint not supplied by caller"
            )
        self.business_steps.append(step)
        self._active.set(step)
        try:
            self.save()
        except BaseException:
            self._active.set(None)
            self.business_steps.remove(step)
            raise
        return copy.deepcopy(step)

    def end_business_step(
        self,
        status: str = "PASS",
        actual: str = "",
        *,
        failure_class: str | None = None,
        error_message: str = "",
    ) -> dict:
        if status not in STEP_STATUSES:
            raise ValueError("invalid business status")
        step = self._require_active()
        step.update(
            finished_at=wall_time(),
            monotonic_end_ns=time.monotonic_ns(),
            actual=actual,
            requested_status=status,
            failure_class=failure_class,
            error_message=error_message,
        )
        step["duration_ms"] = (
            step["monotonic_end_ns"] - step["monotonic_start_ns"]
        ) / 1_000_000
        step["status"] = self._evaluate(step) if status == "PASS" else status
        self._active.set(None)
        self.save()
        return copy.deepcopy(step)

    def _write_evidence_data(
        self, folder_name: str, filename: str, payload: bytes
    ) -> Path:
        """Create through pinned, no-follow directory descriptors, not resolved links.

        Reject platforms without the required primitives instead of falling back to
        a check-then-open path write. The run directory already exists after begin.
        """
        try:
            with safe_directory(self.run_dir, writable=True) as root:
                with root.subdir(folder_name, create=True) as folder:
                    folder.write_bytes(filename, payload)
        except (ValueError, OSError) as exc:
            raise ValueError(
                "unsafe evidence directory or failed evidence write"
            ) from exc
        return self.run_dir / folder_name / filename

    def add_evidence(
        self,
        kind: str,
        *,
        path: str | Path | None = None,
        data: Any = None,
        source: str = "script",
        evidence_id: str | None = None,
        capture_at: str | None = None,
        capture_error: str = "",
        association: str = "explicit_context",
        confidence: str = "high",
        reused: bool = False,
        metadata: dict | None = None,
    ) -> dict:
        """Index immutable package-local evidence or record an explicit capture error.

        ``data`` may be bytes, text, or a JSON-compatible value. ``path`` must
        already be inside the run package; outside files must be imported as data.
        """
        step = self._require_active()
        eid = evidence_id or f"E-{uuid.uuid4().hex}"
        if (
            not isinstance(eid, str)
            or not eid
            or any(e["evidence_id"] == eid for e in self.evidence)
        ):
            raise ValueError("evidence_id must be unique and nonempty")
        indexed_at = wall_time()
        # Indexing an existing file is not a new capture. Only caller-supplied
        # capture provenance may attribute path evidence to the active attempt.
        stamp = capture_at if path is not None else capture_at or indexed_at
        if stamp is not None:
            parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("capture_at must include a timezone")
        if path is not None and data is not None:
            raise ValueError("supply path or data, not both")
        relative = None
        digest = None
        error = capture_error
        if path is not None and not capture_at:
            error = error or "path evidence requires explicit capture_at provenance"
        if data is not None:
            suffix = (
                ".png"
                if kind == "screenshot"
                else ".mp4"
                if kind == "video" and isinstance(data, bytes)
                else ".bin"
                if isinstance(data, bytes)
                else ".txt"
                if isinstance(data, str)
                else ".json"
            )
            folder_name = "screenshots" if kind == "screenshot" else "evidence"
            filename = f"{uuid.uuid4().hex}{suffix}"
            payload = (
                data
                if isinstance(data, bytes)
                else data.encode("utf-8")
                if isinstance(data, str)
                else json.dumps(data, ensure_ascii=False).encode("utf-8")
            )
            path = self._write_evidence_data(folder_name, filename, payload)
        if path is not None:
            target = Path(path)
            if not target.is_absolute():
                target = self.run_dir / target
            try:
                relative = target.relative_to(self.run_dir).as_posix()
                with self._evidence_directory(relative) as (folder, name):
                    digest = folder.hash_file(name)
            except FileNotFoundError:
                error = error or "evidence file missing"
            except (ValueError, OSError) as exc:
                # Descriptor traversal wraps OS errors; only ENOENT is a capture
                # failure. Symlinks, permissions and traversal still fail closed.
                cause = exc
                while cause.__cause__ is not None:
                    cause = cause.__cause__
                if isinstance(cause, FileNotFoundError):
                    error = error or "evidence file missing"
                else:
                    raise ValueError(
                        "evidence path must stay inside a safe run package"
                    ) from exc
        elif not error:
            error = "no evidence captured"
        if not self._capture_in_attempt(stamp, step):
            error = error or "capture_at outside attempt time window"
        if relative and any(e["path"] == relative for e in self.evidence):
            reused = True
            error = (
                error
                or "evidence path already indexed; reuse cannot prove a new capture"
            )
        entry = {
            "evidence_id": eid,
            "kind": kind,
            "path": relative,
            "sha256": digest,
            "capture_at": stamp,
            "indexed_at": indexed_at,
            "source": source,
            "status": "NOT_COLLECTED" if error else "PASS",
            "capture_error": error,
            "case_id": step["case_id"],
            "business_step_id": step["business_step_id"],
            "attempt_id": step["attempt_id"],
            "association": association,
            "confidence": confidence,
            "reused": reused,
            "metadata": copy.deepcopy(metadata or {}),
        }
        self.evidence.append(entry)
        step["evidence_refs"].append(eid)
        self.save()
        return copy.deepcopy(entry)

    def add_assertion(
        self,
        dimension: str,
        status: str,
        expected: str,
        actual: str,
        *,
        evidence_refs: list[str] | None = None,
        assertion_id: str | None = None,
        reason: str = "",
    ) -> dict:
        step = self._require_active()
        if dimension not in DIMENSIONS or status not in ASSERTION_STATUSES:
            raise ValueError("invalid assertion dimension or status")
        declaration = self._declaration(step["case_id"], step["business_step_id"])
        if (
            status == "NOT_APPLICABLE"
            and dimension not in declaration["not_applicable"]
        ):
            raise ValueError("N/A must be declared before execution")
        aid = assertion_id or f"A-{uuid.uuid4().hex}"
        if any(
            a["assertion_id"] == aid
            for s in self.business_steps
            for a in s["assertions"]
        ):
            raise ValueError("assertion_id must be unique")
        entry = {
            "assertion_id": aid,
            "dimension": dimension,
            "status": status,
            "expected": expected,
            "actual": actual,
            "evidence_refs": list(evidence_refs or []),
            "reason": reason,
        }
        step["assertions"].append(entry)
        self.save()
        return copy.deepcopy(entry)

    @staticmethod
    def _capture_in_attempt(stamp: str, step: dict) -> bool:
        """Wall-time attribution with a one-second sampling/serialization tolerance."""
        try:
            captured = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            start = dt.datetime.fromisoformat(step["started_at"])
            end = dt.datetime.fromisoformat(step["finished_at"] or wall_time())
            if any(
                value.tzinfo is None or value.utcoffset() is None
                for value in (captured, start, end)
            ):
                return False
            tolerance = dt.timedelta(seconds=1)
            return start - tolerance <= captured <= end + tolerance
        except (ValueError, TypeError, AttributeError):
            return False

    def _evidence_directory(self, relative):
        from contextlib import contextmanager

        @contextmanager
        def opened():
            path = Path(relative)
            if (
                not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in str(relative)
            ):
                raise ValueError("invalid package-relative evidence path")
            with ExitStack() as stack:
                folder = stack.enter_context(safe_directory(self.run_dir))
                for part in path.parts[:-1]:
                    folder = stack.enter_context(folder.subdir(part))
                yield folder, path.name

        return opened()

    def _valid_evidence(self, eid: str, step: dict) -> bool:
        scope = self._validation.get()
        key = (eid, step["case_id"], step["business_step_id"], step["attempt_id"])
        if scope is not None and key in scope["valid"]:
            return scope["valid"][key]
        valid = self._check_evidence(eid, step, scope)
        if scope is not None:
            scope["valid"][key] = valid
        return valid

    def _check_evidence(self, eid, step, scope):
        entry = (
            scope["entries"].get(eid)
            if scope is not None
            else next((e for e in self.evidence if e["evidence_id"] == eid), None)
        )
        if not entry or entry["status"] != "PASS" or not entry["path"]:
            return False
        if entry["reused"] or any(
            entry[k] != step[k] for k in ("case_id", "business_step_id", "attempt_id")
        ):
            return False
        if entry["association"] != "explicit_context" or entry["confidence"] != "high":
            return False
        if not self._capture_in_attempt(entry["capture_at"], step):
            return False
        if scope is not None:
            if scope["first_path"].get(entry["path"]) != eid:
                return False
        elif any(
            e.get("path") == entry["path"]
            for e in self.evidence[: self.evidence.index(entry)]
        ):
            return False
        try:
            with self._evidence_directory(entry["path"]) as (folder, name):
                if entry["kind"] == "screenshot":
                    import cv2
                    import numpy as np

                    raw = folder.read_bytes(name)
                    if (
                        not raw.startswith(b"\x89PNG\r\n\x1a\n")
                        or hashlib.sha256(raw).hexdigest() != entry["sha256"]
                    ):
                        return False
                    try:
                        image = cv2.imdecode(
                            np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED
                        )
                    except cv2.error:
                        return False
                    return image is not None and image.size > 0
                return (
                    folder.stat(name).st_size > 0
                    and folder.hash_file(name) == entry["sha256"]
                )
        except (ValueError, OSError, ImportError):
            return False

    def _dimension_evidence(self, assertion: dict, step: dict) -> bool:
        kinds = {
            "ui": {"screenshot", "ui_tree", "ui", "visual"},
            "callback_log": {"logcat", "callback_log", "log"},
            "network": {"network", "http", "mock_requests"},
        }
        scope = self._validation.get()
        entries = (
            scope["entries"] if scope else {e["evidence_id"]: e for e in self.evidence}
        )
        return any(
            entries.get(eid, {}).get("kind") in kinds[assertion["dimension"]]
            and self._valid_evidence(eid, step)
            for eid in assertion["evidence_refs"]
        )

    def _evidence_complete(self, step: dict) -> bool:
        declaration = self._declaration(step["case_id"], step["business_step_id"])
        if not declaration["required_dimensions"]:
            return False
        for dim in declaration["required_dimensions"]:
            checks = [a for a in step["assertions"] if a["dimension"] == dim]
            if not checks or any(
                a["status"] not in {"PASS", "FAIL"}
                or not str(a["expected"]).strip()
                or not str(a["actual"]).strip()
                or not a["evidence_refs"]
                or not self._dimension_evidence(a, step)
                or not all(
                    self._valid_evidence(eid, step) for eid in a["evidence_refs"]
                )
                for a in checks
            ):
                return False
        return all(
            any(
                e["kind"] == kind
                and e["evidence_id"] in step["evidence_refs"]
                and self._valid_evidence(e["evidence_id"], step)
                for e in self.evidence
            )
            for kind in declaration["required_evidence"]
        )

    def _evaluate(self, step: dict) -> str:
        declaration = self._declaration(step["case_id"], step["business_step_id"])
        assertions = step["assertions"]
        if not assertions:
            return "NOT_COLLECTED"
        if any(a["status"] == "FAIL" for a in assertions):
            return "FAIL"
        if not declaration["required_dimensions"]:
            return "NOT_APPLICABLE"
        for dim in declaration["required_dimensions"]:
            checks = [a for a in assertions if a["dimension"] == dim]
            if not checks:
                return "NOT_COLLECTED"
            if any(a["status"] != "PASS" for a in checks):
                return (
                    "INCONCLUSIVE"
                    if any(a["status"] == "INCONCLUSIVE" for a in checks)
                    else "NOT_COLLECTED"
                )
            if any(
                not isinstance(a["expected"], str)
                or not a["expected"].strip()
                or not isinstance(a["actual"], str)
                or not a["actual"].strip()
                or not a["evidence_refs"]
                or not self._dimension_evidence(a, step)
                or not all(
                    self._valid_evidence(eid, step) for eid in a["evidence_refs"]
                )
                for a in checks
            ):
                return "NOT_COLLECTED"
        for kind in declaration["required_evidence"]:
            if not any(
                e["kind"] == kind
                and e["evidence_id"] in step["evidence_refs"]
                and self._valid_evidence(e["evidence_id"], step)
                for e in self.evidence
            ):
                return "NOT_COLLECTED"
        return "PASS"

    def snapshot(self) -> dict:
        # A cache lives for exactly one operation, never across snapshots/loads.
        entries = {e["evidence_id"]: e for e in self.evidence}
        first_path = {}
        for e in self.evidence:
            first_path.setdefault(e.get("path"), e["evidence_id"])
        token = self._validation.set(
            dict(entries=entries, first_path=first_path, valid={})
        )
        try:
            return self._snapshot()
        finally:
            self._validation.reset(token)

    def _snapshot(self) -> dict:
        cases = copy.deepcopy(self.cases)
        attempts = copy.deepcopy(self.business_steps)
        by_step = {}
        for step in attempts:
            by_step.setdefault((step["case_id"], step["business_step_id"]), []).append(
                step
            )
        for step in attempts:
            if step.get("requested_status") == "PASS" or step["status"] == "PASS":
                step["status"] = (
                    self._evaluate(step) if step["finished_at"] else "NOT_RUN"
                )
        summary = dict(
            planned_cases=len(cases),
            planned_steps=0,
            executed_steps=0,
            first_passed=0,
            final_passed=0,
            recovered=0,
            failed=0,
            not_run=0,
            evidence_complete=0,
        )
        summary["step_results"] = []
        for case in cases:
            statuses = []
            for declaration in case["steps"]:
                prior = by_step.get(
                    (case["case_id"], declaration["business_step_id"]), []
                )
                first = prior[0]["status"] if prior else "NOT_RUN"
                final = prior[-1]["status"] if prior else "NOT_RUN"
                recovered = bool(prior and first != "PASS" and final == "PASS")
                declaration.update(
                    first_status=first,
                    final_status=final,
                    status="RECOVERED" if recovered else final,
                    attempts=len(prior),
                )
                summary["planned_steps"] += 1
                summary["executed_steps"] += bool(prior)
                summary["first_passed"] += first == "PASS"
                summary["final_passed"] += final == "PASS"
                summary["recovered"] += recovered
                summary["failed"] += final == "FAIL"
                summary["not_run"] += not prior
                summary["evidence_complete"] += bool(
                    prior
                    and prior[-1]["finished_at"]
                    and self._evidence_complete(prior[-1])
                )
                summary["step_results"].append(
                    {
                        "case_id": case["case_id"],
                        "business_step_id": declaration["business_step_id"],
                        "title": declaration["title"],
                        "first_result": first,
                        "final_result": final,
                        "recovery_status": "RECOVERED" if recovered else "NONE",
                    }
                )
                statuses.append(final)
            case["status"] = (
                "PASS"
                if all(s == "PASS" for s in statuses)
                else "FAIL"
                if "FAIL" in statuses
                else "INCONCLUSIVE"
            )
        count = summary["planned_steps"]
        for key, numerator in (
            ("first_pass_rate", "first_passed"),
            ("final_completion_rate", "final_passed"),
            ("evidence_complete_rate", "evidence_complete"),
        ):
            summary[key] = summary[numerator] / count if count else 0.0
        summary["status"] = (
            "PASS"
            if count and summary["final_passed"] == count
            else "FAIL"
            if summary["failed"]
            else "INCONCLUSIVE"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "cases": cases,
            "business_steps": attempts,
            "evidence": copy.deepcopy(self.evidence),
            "summary": summary,
        }

    def save(self) -> dict:
        report = self.snapshot()
        with safe_directory(self.run_dir, create=True, writable=True) as root:
            root.write_bytes(
                "business_report.json",
                (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                ),
                replace=True,
            )
        return report


def load_business_report(run_dir: str | Path) -> dict:
    """Load a v2 package and recompute PASS/coverage using current file hashes.

    Saved summary and declaration aggregate results are never trusted. Raises
    ValueError for unsupported/malformed contracts; renderers can display that
    as a package error rather than silently showing a green legacy fallback.
    """
    directory = Path(run_dir)
    with safe_directory(directory) as root:
        data = json.loads(root.read_bytes("business_report.json"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported business report schema")
    report = BusinessReport(directory)
    try:
        report.cases = report._validated_cases(data["cases"])
        report.business_steps = copy.deepcopy(data["business_steps"])
        report.evidence = copy.deepcopy(data["evidence"])
        if not all(
            isinstance(value, list)
            for value in (report.cases, report.business_steps, report.evidence)
        ):
            raise ValueError("business report collections must be lists")
        identities = set()
        assertion_ids = set()
        for step in report.business_steps:
            report._declaration(step["case_id"], step["business_step_id"])
            identity = (step["case_id"], step["business_step_id"], step["attempt_id"])
            if (
                not isinstance(step["attempt_id"], str)
                or not step["attempt_id"].strip()
                or identity in identities
            ):
                raise ValueError("invalid or duplicate attempt identity")
            identities.add(identity)
            if not isinstance(step.get("evidence_refs"), list) or not all(
                isinstance(eid, str) for eid in step["evidence_refs"]
            ):
                raise ValueError("invalid attempt evidence references")
            if step["status"] not in STEP_STATUSES or not isinstance(
                step["assertions"], list
            ):
                raise ValueError("invalid attempt status/assertions")
            for assertion in step["assertions"]:
                aid = assertion["assertion_id"]
                if not isinstance(aid, str) or not aid.strip() or aid in assertion_ids:
                    raise ValueError("invalid or duplicate assertion identity")
                assertion_ids.add(aid)
                if (
                    assertion["dimension"] not in DIMENSIONS
                    or assertion["status"] not in ASSERTION_STATUSES
                ):
                    raise ValueError("invalid assertion dimension/status")
                if not isinstance(assertion["evidence_refs"], list):
                    raise ValueError("invalid evidence references")
                if (
                    assertion["status"] == "NOT_APPLICABLE"
                    and assertion["dimension"]
                    not in report._declaration(
                        step["case_id"], step["business_step_id"]
                    )["not_applicable"]
                ):
                    raise ValueError("undeclared N/A assertion")
        evidence_ids = set()
        for evidence in report.evidence:
            if not isinstance(evidence.get("metadata", {}), dict):
                raise ValueError("evidence metadata must be a dictionary")
            eid = evidence["evidence_id"]
            if not isinstance(eid, str) or not eid.strip() or eid in evidence_ids:
                raise ValueError("invalid or duplicate evidence identity")
            evidence_ids.add(eid)
            if (
                evidence["case_id"],
                evidence["business_step_id"],
                evidence["attempt_id"],
            ) not in identities:
                raise ValueError("evidence refers to undeclared attempt")
        return report.snapshot()
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("malformed business report") from exc


__all__ = [
    "BusinessReport",
    "load_business_report",
    "DIMENSIONS",
    "ASSERTION_STATUSES",
    "SCHEMA_VERSION",
]

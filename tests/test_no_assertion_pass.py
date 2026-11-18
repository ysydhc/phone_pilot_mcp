"""N/A does not prove PASS and empty business state is a legacy tool run."""

from phone_pilot.core.business_report import BusinessReport, load_business_report
from phone_pilot.core.run_store import RunSession
from phone_pilot.core.html_report import _load_run_data


def test_all_na_attempt_never_inflates_pass_or_coverage(tmp_path):
    report = BusinessReport(tmp_path / "run")
    report.declare_cases(
        [
            {
                "case_id": "c",
                "steps": [
                    {
                        "business_step_id": "s",
                        "required_dimensions": [],
                        "not_applicable": {
                            "ui": "offline",
                            "callback_log": "offline",
                            "network": "offline",
                        },
                    }
                ],
            }
        ]
    )
    report.begin_business_step("c", "s")
    report.add_assertion("ui", "NOT_APPLICABLE", "", "")
    assert report.end_business_step()["status"] == "NOT_APPLICABLE"
    summary = load_business_report(report.run_dir)["summary"]
    assert summary["status"] != "PASS"
    assert summary["final_passed"] == summary["evidence_complete"] == 0


def test_empty_business_state_keeps_legacy_retry_view(tmp_path):
    session = RunSession(tmp_path / "run")
    session.add_step(
        step_num=1, action="find_text", detail="'Ready' not found", status="fail"
    )
    session.add_step(
        step_num=2, action="find_text", detail="'Ready' @ (1, 2)", status="ok"
    )
    session.finalize()
    view = _load_run_data(session.run_dir)
    assert not view["business"]
    assert len(view["steps"]) == 1 and view["steps_pass_rate"] == 100


def test_initial_save_failure_unwinds_attempt(tmp_path, monkeypatch):
    import pytest

    report = BusinessReport(tmp_path / "run")
    report.declare_cases([{"case_id": "c", "steps": [{"business_step_id": "s"}]}])
    original_save = report.save
    monkeypatch.setattr(
        report, "save", lambda: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError):
        report.begin_business_step("c", "s")
    assert report.active is None and report.business_steps == []
    monkeypatch.setattr(report, "save", original_save)
    report.begin_business_step("c", "s")
    report.end_business_step("FAIL")


def test_null_attempt_evidence_refs_rejected_with_package_warning(tmp_path):
    import json
    import pytest
    from phone_pilot.core.html_report import render_html_report

    report = BusinessReport(tmp_path / "run")
    report.declare_cases([{"case_id": "c", "steps": [{"business_step_id": "s"}]}])
    report.begin_business_step("c", "s")
    report.end_business_step("FAIL")
    path = report.run_dir / "business_report.json"
    data = json.loads(path.read_text())
    data["business_steps"][0]["evidence_refs"] = None
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="evidence references"):
        load_business_report(report.run_dir)
    assert "invalid attempt evidence references" in render_html_report(report.run_dir)

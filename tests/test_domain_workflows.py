from __future__ import annotations

from io import BytesIO

import pytest

from jarvis.background_tasks import BackgroundTaskEngine
from jarvis.reconciliation import _index, _report
from jarvis.tools.regulatory_tools import compare_documents
from jarvis.tools.uat_tools import trace_uat


def test_workbook_preserves_all_breaks_and_signed_waterfall():
    openpyxl = pytest.importorskip("openpyxl")
    before = _index([("ID", "RWA"), ("=1+1", -100), ("B", 50)], ["ID"])
    after = _index([("ID", "RWA"), ("=1+1", 120), ("C", 75)], ["ID"])
    workbook = openpyxl.Workbook(write_only=True)
    result = _report(before, after, sample_limit=1, workbook=workbook, keys=["ID"], metric="RWA")
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    report = openpyxl.load_workbook(output)
    try:
        assert len(result["sample"]) == 1
        assert report["Breaks"].max_row == 4
        assert report["Breaks"]["D2"].data_type == "s"
        assert report["Cell differences"]["F2"].value == -100
        assert result["waterfall"] == {
            "opening": "-50", "removed": "-50", "changed": "220", "added": "75", "closing": "195",
        }
        assert len(report["Variance waterfall"]._charts) == 1
    finally:
        report.close()


def test_workbooks_require_completed_run_and_are_removed_with_run():
    engine = BackgroundTaskEngine(":memory:")
    try:
        task = engine.create("Compare")
        engine.save_workbook(task, b"workbook")
        assert engine.get_workbook(task) is None
        engine.set_status(task, "done")
        assert engine.get_workbook(task) == b"workbook"
        engine.cleanup(max_done=0)
        assert engine.get_workbook(task) is None
    finally:
        engine.close()


def test_regulatory_register_and_uat_trace(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    mapping = tmp_path / "controls.csv"
    old.write_text("# 1.1 Previous wording\n# 2 Removed clause\n", encoding="utf-8")
    new.write_text("# 1.1 Updated wording\n# 3 New clause\n", encoding="utf-8")
    mapping.write_text("clause,control,owner\n1.1,RWA policy,Risk team\n", encoding="utf-8")
    report = compare_documents(str(old), str(new), str(mapping), 0, 1)
    assert report["counts"] == {"changed": 1, "added": 1, "removed": 1, "unchanged": 0}
    assert report["changes"][0]["mapped_controls"][0]["owner"] == "Risk team"
    assert report["changes"][0]["after"]["line"] == 1
    assert report["next_offset"] == 1

    requirements = tmp_path / "requirements.csv"
    previous = tmp_path / "previous.csv"
    tests = tmp_path / "tests.csv"
    requirements.write_text("requirement_id,requirement\nR1,Revised\nR2,New\n", encoding="utf-8")
    previous.write_text("requirement_id,requirement\nR1,Original\nR3,Removed\n", encoding="utf-8")
    tests.write_text(
        "test_id,requirement_id,result,evidence\nT1,R1,pass,Run 4\nT2,R3,fail,Run 4\n", encoding="utf-8",
    )
    report = trace_uat(str(requirements), str(tests), str(previous), 0, 10)
    assert report["coverage_totals"] == {"verified": 1, "uncovered": 1}
    assert report["requirements"][0]["retest_due"]
    assert report["removed_requirement_sample"] == ["R3"]
    assert report["orphan_test_count"] == 1


def test_case_files_keep_deadline_order_and_reject_stale_edits():
    from jarvis.store import Store

    store = Store(":memory:")
    try:
        first = store.save_case({"title": "Refund", "deadline": None, "status": "open"})
        second = store.save_case({"title": "Meter repair", "deadline": "2026-12-02", "status": "waiting"})
        assert [case["id"] for case in store.list_cases(None, 10)] == [second["id"], first["id"]]
        changed = {"title": "Deposit refund", "status": "resolved", "evidence": ["Receipt.pdf"]}
        store.save_case(changed, first["id"], first["revision"])
        with pytest.raises(ValueError, match="case changed"):
            store.save_case(changed, first["id"], first["revision"])
        assert store.get_case(first["id"])["revision"] == 2
        assert store.list_cases("resolved", 10)[0]["title"] == "Deposit refund"
    finally:
        store.close()


def test_intake_flags_ambiguous_missing_and_duplicate_fields(tmp_path):
    from jarvis.tools.intake_tools import DocumentIntakeArgs, review_intake

    first = tmp_path / "bill-one.txt"
    second = tmp_path / "bill-two.txt"
    first.write_text("Invoice number: INV-42\nTotal: 100\nAmount due: 120\n", encoding="utf-8")
    second.write_text("Invoice number: INV-42\n", encoding="utf-8")
    args = DocumentIntakeArgs(
        paths=[str(first), str(second)],
        fields=[{"name": "Invoice number"}, {"name": "Total", "aliases": ["Amount due"]}],
        unique_field="Invoice number",
    )
    report = review_intake(args)
    assert report["needs_review"] == 2
    assert [item["issue"] for item in report["documents"][0]["exceptions"]] == [
        "ambiguous", "duplicate across documents",
    ]
    assert [item["issue"] for item in report["documents"][1]["exceptions"]] == [
        "missing", "duplicate across documents",
    ]
    assert report["documents"][0]["fields"][1]["candidates"][0]["line"] == 2

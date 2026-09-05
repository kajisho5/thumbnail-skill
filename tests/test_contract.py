"""Contract / doctor shape tests, and drift checks: the contract must actually match what the
skill implements (only implemented tools, only registered fonts, only real error codes)."""
from __future__ import annotations

from thumbnail_skill.contract import skill_contract
from thumbnail_skill.doctor import doctor_report
from thumbnail_skill.errors import ERROR_CODES
from thumbnail_skill.executor import TOOLS
from thumbnail_skill.fonts import font_ids
from thumbnail_skill.model import ELEMENT_TYPES, OUTPUT_FORMATS


def test_contract_is_json_serialisable():
    import json
    json.dumps(skill_contract())


def test_contract_tool_ids_match_executor():
    c = skill_contract()
    assert {t["tool_id"] for t in c["tools"]} == set(TOOLS)


def test_contract_lists_no_unimplemented_operation():
    c = skill_contract()
    forbidden_terms = ("select_best_frame", "auto_layout", "generate_title", "rank_frames", "face_detect", "click_through")
    text = str(c).lower()
    for term in forbidden_terms:
        assert term not in text


def test_contract_error_codes_match_errors_module():
    c = skill_contract()
    assert set(c["errors"]["codes"]) == set(ERROR_CODES)


def test_contract_element_types_match_model():
    c = skill_contract()
    assert set(c["document"]["elements"]["types"]) == set(ELEMENT_TYPES)


def test_contract_output_formats_match_model():
    c = skill_contract()
    assert set(c["output_formats"]) == set(OUTPUT_FORMATS)


def test_contract_font_ids_match_registry():
    c = skill_contract()
    assert set(c["fonts"]["font_ids"]) == set(font_ids())


def test_contract_declares_no_shell_or_arbitrary_execution():
    c = skill_contract()
    ex = c["execution"]
    assert ex["shell"] is False
    assert ex["arbitrary_executables"] is False
    assert ex["arbitrary_filters"] is False
    assert ex["network"] is False


def test_doctor_report_is_json_serialisable():
    import json
    json.dumps(doctor_report())


def test_doctor_never_reports_unimplemented_font_as_available():
    rep = doctor_report()
    for fid, status in rep["checks"]["fonts"].items():
        assert fid in font_ids()
        assert status["status"] in ("available", "unavailable")
        if status["status"] == "available":
            assert "path" in status and "sha256" in status

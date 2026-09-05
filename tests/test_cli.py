"""CLI-level regression tests: these exercise `cli.main()` directly against real files on disk,
which is a different code path from calling `skill.run_tool()`/`run_request()` in-process (as
test_integration.py and test_security.py do). Two real bugs were only reachable through this path:
a non-dict top-level JSON document crashing with a raw Python TypeError instead of a structured
error, and `thumbnail run -` always exiting 0 because it checked the process-boundary transport's
outer "dispatched" flag instead of the wrapped tool response's own "ok"."""
from __future__ import annotations

import io
import json

import pytest

from thumbnail_skill.cli import main
from thumbnail_skill.errors import EXIT_CODES


def _write(path, obj_or_text):
    path.write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text), encoding="utf-8")


@pytest.mark.parametrize("cmd", ["validate", "render", "extract-frame"])
@pytest.mark.parametrize("top_level", ["5", "true", '"a string"', "null"])
def test_non_dict_top_level_json_never_crashes(tmp_path, capsys, cmd, top_level):
    """A request file whose top-level JSON value isn't an object must fail cleanly (INVALID_REQUEST,
    one JSON document on stdout), never raise an uncaught TypeError out of argparse's main()."""
    req = tmp_path / "req.json"
    _write(req, top_level)
    args = [cmd, str(req), "--json"] if cmd == "validate" else [cmd, str(req), "--workspace", str(tmp_path), "--json"]
    code = main(args)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "INVALID_REQUEST"
    assert code == EXIT_CODES["INVALID_REQUEST"]


def test_render_with_non_dict_options_never_crashes(tmp_path, capsys):
    req = tmp_path / "req.json"
    _write(req, {"document": {"document_id": "d", "canvas": {"width": 32, "height": 32}, "elements": []}, "options": "not-an-object"})
    code = main(["render", str(req), "--workspace", str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "INVALID_REQUEST"
    assert code == EXIT_CODES["INVALID_REQUEST"]


def test_run_transport_exit_code_reflects_tool_failure(tmp_path, monkeypatch, capsys):
    """`thumbnail run -`'s outer envelope is {"ok": true, "tool", "result"}: "ok" there only means
    the request was well-formed and dispatched, not that the render succeeded. The process exit code
    must come from the nested `result`, not the outer envelope."""
    request = {"tool": "thumbnail/render", "params": {
        "document": {"document_id": "d", "canvas": {"width": 10, "height": 10}, "elements": []},   # width < MIN_CANVAS: INVALID_REQUEST
        "output": {"path": str(tmp_path / "out.png"), "format": "png"}}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    code = main(["run", "-", "--workspace", str(tmp_path), "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True                      # dispatch itself succeeded
    assert printed["result"]["ok"] is False            # the render did not
    assert printed["result"]["error"]["code"] == "INVALID_REQUEST"
    assert code == EXIT_CODES["INVALID_REQUEST"]       # not 0


def test_run_transport_exit_code_zero_on_success(tmp_path, monkeypatch, capsys):
    request = {"tool": "thumbnail/validate", "params": {"document": {
        "document_id": "d", "canvas": {"width": 64, "height": 64},
        "elements": [{"element_id": "e", "type": "text", "text": {"text": "hi", "font_id": "sans", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}}]}}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    code = main(["run", "-", "--workspace", str(tmp_path), "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert printed["result"]["ok"] is True
    assert code == 0


def test_run_accepts_explicit_json_flag(tmp_path, monkeypatch, capsys):
    """The `run` subcommand's own help text shows `run - --json`; the flag must actually be accepted
    (it is a no-op: `run` always prints JSON), not rejected by argparse."""
    request = {"tool": "thumbnail/validate", "params": {"document": {
        "document_id": "d", "canvas": {"width": 64, "height": 64},
        "elements": [{"element_id": "e", "type": "text", "text": {"text": "hi", "font_id": "sans", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}}]}}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    code = main(["run", "-", "--workspace", str(tmp_path), "--json"])
    assert code == 0


def test_render_direct_command_exit_code_matches_error_table(tmp_path, capsys):
    req = tmp_path / "req.json"
    _write(req, {"document": {"document_id": "d", "canvas": {"width": 64, "height": 64},
                              "elements": [{"element_id": "e", "type": "text", "text": {"text": "hi", "font_id": "no-such-font", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}}]},
                 "output": {"path": str(tmp_path / "out.png"), "format": "png"}})
    code = main(["render", str(req), "--workspace", str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "MISSING_INPUT"
    assert code == EXIT_CODES["MISSING_INPUT"]

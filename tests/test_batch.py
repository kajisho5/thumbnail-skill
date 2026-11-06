"""`thumbnail/render_batch` (ADR-14, roadmap step 5): a 10-row CSV and two platform targets write 20 files at the
platform sizes, each passing the same output check as a single render; names that do not fit are shrunk to a
stated minimum and never cut; every row is checked before anything is written. Real Pillow renders, no mocking."""
from __future__ import annotations

import json

import pytest
from PIL import Image

from thumbnail_skill.batch import fit_text
from thumbnail_skill.cli import main as cli_main
from thumbnail_skill.contract import skill_contract
from thumbnail_skill.errors import ThumbnailError
from thumbnail_skill.fonts import resolve_font
from thumbnail_skill.model import PLATFORM_PRESETS, Position, TextContent, preset_violations
from thumbnail_skill.renderer import text_box
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool

from .fixtures.generate import SESSIONS, build_batch_fixture


def landscape(min_title=24):
    return {"preset": "youtube",
            "document": {"document_id": "talk-{{session}}", "canvas": {"background": "#14213D"},
                         "assets": [{"asset_id": "photo", "kind": "image", "path": "{{photo}}"}],
                         "elements": [
                             {"element_id": "photo_el", "type": "image", "image": {"asset_id": "photo", "position": {"x": 840, "y": 140}, "size": {"width": 400, "height": 400}}},
                             {"element_id": "title", "type": "text", "z_index": 2,
                              "text": {"text": "{{title}}", "font_id": "sans-bold", "font_size": 72, "color": "#FFFFFF", "position": {"x": 40, "y": 600}}},
                             {"element_id": "speaker", "type": "text", "z_index": 2,
                              "text": {"text": "{{speaker}} | {{session}}", "font_id": "sans", "font_size": 40, "color": "#FCA311", "position": {"x": 40, "y": 520}}}]},
            "text_fit": {"title": {"max_width": 1200, "max_height": 110, "min_font_size": min_title}}}


def vertical():
    return {"preset": "youtube-shorts",
            "document": {"document_id": "talk-{{session}}-v", "canvas": {"background": "#14213D"},
                         "assets": [{"asset_id": "photo", "kind": "image", "path": "{{photo}}"}],
                         "elements": [
                             {"element_id": "photo_el", "type": "image", "image": {"asset_id": "photo", "position": {"x": 140, "y": 300}, "size": {"width": 800, "height": 800}}},
                             {"element_id": "title", "type": "text", "z_index": 2,
                              "text": {"text": "{{title}}", "font_id": "sans-bold", "font_size": 80, "color": "#FFFFFF", "position": {"x": 540, "y": 1300},
                                       "align": {"horizontal": "center", "vertical": "top"}}}]},
            "text_fit": {"title": {"max_width": 1040, "min_font_size": 20}}}


def batch(params, workspace):
    return run_tool("thumbnail/render_batch", params, PathPolicy(str(workspace)))


@pytest.fixture()
def fixture(workspace):
    return build_batch_fixture(workspace / "sessions")


def test_ten_rows_two_targets_write_twenty_files_at_platform_sizes(fixture, workspace):
    res = batch({"csv": {"path": str(fixture["csv"])}, "targets": [landscape(), vertical()],
                 "output": {"directory": "out", "format": "png"}}, workspace)
    assert res["ok"] is True, res
    assert res["count"] == 20 and res["rows"] == 10 and res["targets"] == ["youtube", "youtube-shorts"]
    files = sorted((workspace / "out").iterdir())
    assert len(files) == 20
    for item in res["items"]:
        p = PLATFORM_PRESETS[item["preset"]]
        with Image.open(item["output"]) as img:
            assert img.format == "PNG" and img.size == (p["width"], p["height"])
        # the existing output check: the preset's verdict, and the same limits re-checked on the file itself
        assert item["preset_check"] == {"name": item["preset"], "platform": "YouTube", "checked": ["dimensions", "aspect_ratio", "format", "file_size"], "ok": True}
        size = (workspace / "out" / f"{item['row']}-{item['preset']}.png").stat().st_size
        assert preset_violations(item["preset"], img.size[0], img.size[1], "png", size) == []
    assert {(i["row"], i["preset"]) for i in res["items"]} == {(r, p) for r in range(1, 11) for p in ("youtube", "youtube-shorts")}


def test_long_title_is_shrunk_to_fit_and_never_cut(fixture, workspace):
    res = batch({"csv": {"path": str(fixture["csv"])}, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}, workspace)
    assert res["ok"] is True, res
    fitted = {i["row"]: i["fitted"]["title"] for i in res["items"]}
    long_row = 1 + next(i for i, s in enumerate(SESSIONS) if len(s[0]) > 60)
    assert fitted[long_row]["shrunk"] is True and 24 <= fitted[long_row]["font_size"] < 72
    assert fitted[1]["shrunk"] is False and fitted[1]["font_size"] == 72
    for f in fitted.values():
        x, y, w, h = f["box"]
        assert w <= 1200 and h <= 110 and x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720
    # the shrunk size is the largest that fits: one point larger does not
    title = SESSIONS[long_row - 1][0]
    c = TextContent(title, "sans-bold", fitted[long_row]["font_size"] + 1, "#FFFFFF", Position(40, 600))
    x0, y0, x1, y1 = text_box(c, resolve_font("sans-bold"))
    assert x1 - x0 > 1200 or y1 - y0 > 110
    # the whole text was drawn: ink exists right up to the measured box's right edge region, not clipped at max_width
    with Image.open(workspace / "out" / f"{long_row}-youtube.png") as img:
        x, y, w, h = fitted[long_row]["box"]
        strip = img.convert("RGB").crop((x + w - 40, y, x + w, y + h))
        assert (255, 255, 255) in {strip.getpixel((i, j)) for i in range(strip.width) for j in range(strip.height)}


def test_title_that_does_not_fit_at_the_minimum_is_refused_before_anything_is_written(fixture, workspace):
    t = landscape(min_title=60)
    res = batch({"csv": {"path": str(fixture["csv"])}, "targets": [t], "output": {"directory": "out", "format": "png"}}, workspace)
    assert res["ok"] is False
    err = res["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["details"]["reason"] == "text_does_not_fit"
    assert err["details"]["row"] == 4 and err["details"]["min_font_size"] == 60
    assert not (workspace / "out").exists()


def test_fit_text_never_enlarges_and_reports_outside_canvas():
    c = TextContent("Hi", "sans-bold", 30, "#FFFFFF", Position(10, 10))
    r = fit_text(c, {"max_width": 5000, "max_height": None, "min_font_size": 50}, (1280, 720), "t")
    assert r["font_size"] == 30 and r["shrunk"] is False
    c = TextContent("Hi", "sans-bold", 30, "#FFFFFF", Position(1270, 10))
    with pytest.raises(ThumbnailError) as e:
        fit_text(c, {"max_width": 5000, "max_height": None, "min_font_size": 10}, (1280, 720), "t")
    assert e.value.details["reason"] == "text_outside_canvas"


def test_inline_rows_jpeg_and_name_template(fixture, workspace):
    rows = [{"title": "One", "speaker": "A", "photo": str(fixture["dir"] / "speaker_01.png"), "session": "S1"},
            {"title": "Two", "speaker": "B", "photo": str(fixture["dir"] / "speaker_02.png"), "session": "S2"}]
    res = batch({"rows": rows, "targets": [landscape()], "output": {"directory": "jpg", "name": "{{session}}_yt", "format": "jpeg", "jpeg_quality": 85}}, workspace)
    assert res["ok"] is True, res
    assert sorted(p.name for p in (workspace / "jpg").iterdir()) == ["S1_yt.jpg", "S2_yt.jpg"]
    with Image.open(workspace / "jpg" / "S1_yt.jpg") as img:
        assert img.format == "JPEG"


def test_relative_photo_path_resolves_against_the_workspace(fixture, workspace):
    rows = [{"title": "One", "speaker": "A", "photo": "sessions/speaker_01.png", "session": "S1"}]
    res = batch({"rows": rows, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}, workspace)
    assert res["ok"] is True, res


def test_cp932_csv_is_opt_in(workspace):
    d = build_batch_fixture(workspace / "s")
    text = "title,speaker,photo,session\n基調講演,山田 花子," + str(d["dir"] / "speaker_01.png").replace("\\", "/") + ",K1\n"
    (workspace / "ja.csv").write_bytes(text.encode("cp932"))
    params = {"csv": {"path": str(workspace / "ja.csv")}, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}
    res = batch(params, workspace)   # the default is UTF-8: a Shift_JIS file is refused, never guessed
    assert res["ok"] is False and res["error"]["code"] == "INVALID_INPUT" and res["error"]["details"]["reason"] == "csv_encoding"
    params["csv"]["encoding"] = "cp932"
    res = batch(params, workspace)
    assert res["ok"] is True, res
    assert res["provenance"]["source"]["encoding"] == "cp932"
    assert res["items"][0]["fitted"]["title"]["font_size"] == 72


def test_utf8_bom_is_accepted(fixture, workspace):
    raw = fixture["csv"].read_bytes()
    (workspace / "bom.csv").write_bytes(b"\xef\xbb\xbf" + raw)
    res = batch({"csv": {"path": str(workspace / "bom.csv")}, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}, workspace)
    assert res["ok"] is True and res["provenance"]["source"]["columns"] == ["title", "speaker", "photo", "session"]


@pytest.mark.parametrize("mutate, code, reason", [
    (lambda p: p["targets"][0]["document"]["elements"][1]["text"].update(text="{{subtitle}}"), "INVALID_REQUEST", "unknown_placeholder"),
    (lambda p: p["output"].update(name="{{title}}"), "INVALID_REQUEST", "unsafe_name"),
    (lambda p: p["output"].update(name="same"), "INVALID_REQUEST", "duplicate_name"),
    (lambda p: p["targets"][0]["text_fit"].update(photo_el={"max_width": 10, "min_font_size": 8}), "INVALID_REQUEST", None),
])
def test_refusals_happen_before_any_file_is_written(fixture, workspace, mutate, code, reason):
    params = {"csv": {"path": str(fixture["csv"])}, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}
    mutate(params)
    res = batch(params, workspace)
    assert res["ok"] is False and res["error"]["code"] == code, res
    if reason:
        assert res["error"]["details"]["reason"] == reason
    assert not (workspace / "out").exists()


def test_missing_photo_is_refused_before_anything_is_written(fixture, workspace):
    rows = [{"title": "One", "speaker": "A", "photo": str(fixture["dir"] / "speaker_01.png"), "session": "S1"},
            {"title": "Two", "speaker": "B", "photo": str(fixture["dir"] / "nope.png"), "session": "S2"}]
    res = batch({"rows": rows, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_INPUT" and res["error"]["details"]["row"] == 2
    assert not (workspace / "out").exists()


def test_existing_output_needs_overwrite(fixture, workspace):
    params = {"csv": {"path": str(fixture["csv"])}, "targets": [landscape()], "output": {"directory": "out", "format": "png"}}
    assert batch(params, workspace)["ok"] is True
    res = batch(params, workspace)
    assert res["ok"] is False and res["error"]["code"] == "OUTPUT_ERROR"
    params["output"]["overwrite"] = True
    res = batch(params, workspace)
    assert res["ok"] is True and all(i["reused"] for i in res["items"])


@pytest.mark.parametrize("body", [
    {"targets": [], "output": {"directory": "o", "format": "png"}, "rows": []},
    {"csv": {"path": "x.csv"}, "rows": [], "targets": [{"preset": "youtube", "document": {}}], "output": {"directory": "o", "format": "png"}},
    {"rows": [{"a": 1}], "targets": [{"preset": "youtube", "document": {}}], "output": {"directory": "o", "format": "png"}},
    {"rows": [{"a": "1"}], "targets": [{"preset": "nope", "document": {}}], "output": {"directory": "o", "format": "png"}},
    {"rows": [{"row": "1"}], "targets": [{"preset": "youtube", "document": {}}], "output": {"directory": "o", "format": "png"}},
    {"rows": [{"a": "1"}], "targets": [{"preset": "youtube", "document": {}, "shell": "x"}], "output": {"directory": "o", "format": "png"}},
])
def test_request_validation(workspace, body):
    res = batch(body, workspace)
    assert res["ok"] is False and res["error"]["code"] in ("INVALID_REQUEST", "INVALID_INPUT")


def test_cli_render_batch(fixture, workspace, capsys):
    req = workspace / "batch.json"
    req.write_text(json.dumps({"csv": {"path": str(fixture["csv"])}, "targets": [landscape()], "output": {"directory": "cli", "format": "png"}}), encoding="utf-8")
    code = cli_main(["render-batch", str(req), "--workspace", str(workspace), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert code == 0 and doc["ok"] is True and doc["count"] == 10


def test_contract_declares_render_batch():
    c = skill_contract()
    tool = next(t for t in c["tools"] if t["tool_id"] == "thumbnail/render_batch")
    assert tool["deterministic"] is True and tool["produces_output"] is True
    assert {"id": "thumbnail.render_batch", "lifecycle": "EXPERIMENTAL", "tool_id": "thumbnail/render_batch"} in c["provides"]
    assert c["batch"]["csv"]["default_encoding"] == "utf-8" and "cp932" in c["batch"]["csv"]["encodings"]
    assert c["execution"]["ai"] is False and c["deterministic"] is True

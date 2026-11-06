"""`thumbnail/render_variants` / `thumbnail render --variants N` (ADR-16, roadmap step 7): N documented variants of one
thumbnail, each checked at the size it is seen on a phone. Three variants are written; the one whose text is 10 px
high at 320 px wide is reported (flagged), not dropped; contrast comes from qc-skill's text legibility."""
from __future__ import annotations

import json

import pytest
from PIL import Image

from thumbnail_skill.cli import main as cli_main
from thumbnail_skill.contract import skill_contract
from thumbnail_skill.fonts import resolve_font
from thumbnail_skill.model import Position, TextContent
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool
from thumbnail_skill.variants import ink_height

TITLE = "PHONE SIZE"


def size_for_phone_height(target: float, canvas_width: int = 1280, phone: int = 320) -> int:
    """The font size whose glyph ink is closest to `target` px high at phone width (font files differ per OS)."""
    best = None
    for size in range(20, 120):
        ink = ink_height(TextContent(TITLE, "sans-bold", size, "#FFFFFF", Position(40, 40)), resolve_font("sans-bold"), (canvas_width, 720))
        err = abs(ink * phone / canvas_width - target)
        if best is None or err < best[0]:
            best = (err, size)
    assert best is not None and best[0] <= 0.25
    return best[1]


def document(media, background="#202A44", title_color="#FFFFFF"):
    return {"document_id": "ab", "canvas": {"width": 1280, "height": 720, "background": background},
            "assets": [{"asset_id": "frame", "kind": "image", "path": str(media["bg_jpg"])}],
            "elements": [
                {"element_id": "frame_el", "type": "image", "image": {"asset_id": "frame", "position": {"x": 640, "y": 0}, "size": {"width": 640, "height": 720}}},
                {"element_id": "title", "type": "text", "z_index": 2,
                 "text": {"text": TITLE, "font_id": "sans-bold", "font_size": 110, "color": title_color, "position": {"x": 40, "y": 560}}}]}


def variants(params, workspace):
    return run_tool("thumbnail/render_variants", params, PathPolicy(str(workspace)))


def test_three_variants_are_written_and_the_10px_one_is_reported_not_dropped(media, workspace):
    small = size_for_phone_height(10.0)
    params = {"document": document(media), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"},
              "variants": {"count": 3, "layouts": [{"name": "small-title", "position": {"x": 40, "y": 40}, "font_size": small}]}}
    res = variants(params, workspace)
    assert res["ok"] is True, res
    assert res["count"] == 3 and res["axes"]["layouts"] == ["as-given", "small-title"]
    for k in (1, 2, 3):
        assert (workspace / f"thumb-v{k}.png").is_file()   # every variant is written, the flagged one included
    v = {x["variant"]: x for x in res["variants"]}
    assert v[1]["choices"] == {"layout": "as-given", "palette": "as-given", "frame": "as-given"}   # variant 1 is the original
    assert v[2]["choices"]["layout"] == "small-title" and v[2]["choices"]["palette"] == "white-on-dark-band"
    assert v[3]["choices"] == {"layout": "as-given", "palette": "black-on-yellow-band", "frame": "as-given"}
    title2 = v[2]["small_size"]["texts"][0]
    assert title2["element_id"] == "title" and abs(title2["text_height_px_at_phone"] - 10.0) <= 0.25
    assert "text_too_small" in [f["code"] for f in title2["findings"]]
    assert v[2]["small_size"]["status"] == "fail" and v[2]["small_size"]["ok"] is False
    assert res["flagged"] == [2]
    for k in (1, 3):
        t = v[k]["small_size"]["texts"][0]
        assert t["text_height_px_at_phone"] > 12 and "text_too_small" not in [f["code"] for f in t["findings"]]
        assert v[k]["preset_check"]["ok"] is True
    assert v[1]["small_size"]["scale"] == 0.25


def test_ink_height_follows_the_rendered_glyphs(media, workspace):
    # the measured ink height is the height of the drawn white glyph rows on a black canvas
    doc = {"document_id": "ink", "canvas": {"width": 1280, "height": 720, "background": "#000000"},
           "elements": [{"element_id": "t", "type": "text", "text": {"text": TITLE, "font_id": "sans-bold", "font_size": 80, "color": "#FFFFFF", "position": {"x": 40, "y": 40}}}]}
    out = workspace / "ink.png"
    assert run_tool("thumbnail/render", {"document": doc, "output": {"path": str(out), "format": "png"}}, PathPolicy(str(workspace)))["ok"]
    alpha = Image.open(out).convert("L").point(lambda p: 255 if p >= 128 else 0)
    rows = [y for y in range(alpha.height) if alpha.crop((0, y, alpha.width, y + 1)).getbbox()]
    assert ink_height(TextContent(TITLE, "sans-bold", 80, "#FFFFFF", Position(40, 40)), resolve_font("sans-bold"), (1280, 720)) == rows[-1] - rows[0] + 1


def test_contrast_comes_from_qc_skill(media, workspace, qc_skill_dir):
    # white title on a near-white canvas (as given) vs the same title on a dark band (variant 2)
    doc = document(media, background="#F4F4F4")
    doc["elements"] = [e for e in doc["elements"] if e["element_id"] == "title"]   # nothing but the canvas behind the title
    doc["assets"] = []
    params = {"document": doc, "output": {"path": "c.png", "format": "png"},
              "variants": {"count": 2, "qc_skill": qc_skill_dir, "layouts": [{"name": "same-place", "position": {"x": 40, "y": 560}}]}}
    res = variants(params, workspace)
    assert res["ok"] is True, res
    v1, v2 = res["variants"]
    t1, t2 = v1["small_size"]["texts"][0], v2["small_size"]["texts"][0]
    assert t1["contrast_source"] == "qc-skill video.text_regions" and t2["contrast_source"] == "qc-skill video.text_regions"
    assert t1["contrast_ratio"] < 4.5 and "low_contrast" in [f["code"] for f in t1["findings"]]
    assert t2["contrast_ratio"] >= 4.5 and t2["findings"] == []
    assert v1["small_size"]["status"] == "fail" and v2["small_size"]["status"] == "pass"
    assert res["flagged"] == [1]
    assert v2["small_size"]["qc_skill"]["measurement"] == "video.text_regions"


def test_without_qc_skill_contrast_is_unmeasured_and_said_so(media, workspace):
    params = {"document": document(media), "output": {"path": "n.png", "format": "png"},
              "variants": {"count": 2, "qc_skill": str(workspace / "no-qc-skill-here")}}
    res = variants(params, workspace)
    assert res["ok"] is True, res
    for v in res["variants"]:
        t = v["small_size"]["texts"][0]
        assert t["contrast_ratio"] is None and [f["code"] for f in t["findings"]] == ["contrast_unmeasured"]
        assert v["small_size"]["status"] == "unknown"
    assert res["flagged"] == [] and any("contrast not measured" in w for w in res["warnings"])


def test_frame_axis_swaps_the_frame_asset(media, workspace):
    params = {"document": document(media), "output": {"path": "f.png", "format": "png"},
              "variants": {"count": 2, "frame_element": "frame_el", "frames": [{"name": "red", "asset": {"kind": "image", "path": str(media["logo"])}}],
                           "qc_skill": str(workspace / "none")}}
    res = variants(params, workspace)
    assert res["ok"] is True, res
    assert res["variants"][1]["choices"]["frame"] == "red"
    assert Image.open(workspace / "f-v1.png").convert("RGB").getpixel((960, 360)) != (220, 30, 30)
    assert Image.open(workspace / "f-v2.png").convert("RGB").getpixel((960, 360)) == (220, 30, 30)


def test_existing_variant_output_is_refused_before_anything_is_written(media, workspace):
    (workspace / "e-v2.png").write_bytes(b"x")
    res = variants({"document": document(media), "output": {"path": "e.png", "format": "png"}, "variants": {"count": 3}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "OUTPUT_ERROR" and res["error"]["details"]["variant"] == 2
    assert not (workspace / "e-v1.png").exists()


@pytest.mark.parametrize("v", [{}, {"count": 0}, {"count": 13}, {"count": 2, "text_element": "frame_el"}, {"count": 2, "frames": []},
                               {"count": 2, "layouts": [{"name": "as-given", "position": {"x": 0, "y": 0}}]}, {"count": 2, "shell": "x"}])
def test_variant_request_validation(media, workspace, v):
    res = variants({"document": document(media), "output": {"path": "x.png", "format": "png"}, "variants": v}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_REQUEST"


def test_cli_render_with_variants(media, workspace, capsys):
    req = workspace / "req.json"
    req.write_text(json.dumps({"document": document(media), "output": {"path": "cli.png", "format": "png"}}), encoding="utf-8")
    code = cli_main(["render", str(req), "--variants", "3", "--workspace", str(workspace), "--qc-skill", str(workspace / "none"), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert code == 0 and doc["count"] == 3
    assert sorted(p.name for p in workspace.glob("cli-v*.png")) == ["cli-v1.png", "cli-v2.png", "cli-v3.png"]
    # without --variants the same request file is an ordinary render
    assert cli_main(["render", str(req), "--workspace", str(workspace), "--json"]) == 0
    assert (workspace / "cli.png").is_file()


def test_contract_declares_render_variants():
    c = skill_contract()
    t = next(t for t in c["tools"] if t["tool_id"] == "thumbnail/render_variants")
    assert t["deterministic"] is True and "qc-skill/run" in t["delegates_to"]
    assert c["variants"]["small_size_check"]["phone_width"] == 320
    assert "picking a winner" in c["variants"]["not_provided"]
    assert {"id": "thumbnail.render_variants", "lifecycle": "EXPERIMENTAL", "tool_id": "thumbnail/render_variants"} in c["provides"]

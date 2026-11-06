"""Layered output (ADR-17, roadmap step 8): every composed layer (background, photo, text, logo) is also written as
a PNG with alpha, with a manifest (position, size, order). The layers composited by the manifest equal the
thumbnail pixel for pixel. The tests rebuild the image themselves from the files on disk instead of trusting the
tool's own check."""
from __future__ import annotations

import json

import pytest
from PIL import Image

from thumbnail_skill.cli import main as cli_main
from thumbnail_skill.contract import skill_contract
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool


def rich_document(media):
    return {"document_id": "layers", "canvas": {"width": 1280, "height": 720, "background": "#14213DF0"},
            "assets": [{"asset_id": "photo", "kind": "image", "path": str(media["bg_jpg"])}, {"asset_id": "logo", "kind": "image", "path": str(media["logo"])},
                       {"asset_id": "tall", "kind": "image", "path": str(media["tall"])}],
            "elements": [
                {"element_id": "photo", "type": "image", "z_index": 0, "image": {"asset_id": "photo", "position": {"x": -40, "y": -20}, "size": {"width": 900, "height": 760}, "fit": "cover", "opacity": 0.85}},
                {"element_id": "side", "type": "image", "z_index": 1, "image": {"asset_id": "tall", "position": {"x": 1100, "y": 300}, "size": {"width": 300, "height": 500}, "fit": "contain", "rotation": 90}},
                {"element_id": "offscreen", "type": "image", "z_index": 1, "image": {"asset_id": "logo", "position": {"x": 2000, "y": 2000}, "size": {"width": 50, "height": 50}}},
                {"element_id": "logo", "type": "image", "z_index": 5, "image": {"asset_id": "logo", "position": {"x": 1060, "y": 20}, "size": {"width": 200, "height": 200}, "fit": "contain", "opacity": 0.6}},
                {"element_id": "title", "type": "text", "z_index": 10,
                 "text": {"text": "Layered\nThumbnail", "font_id": "sans-bold", "font_size": 72, "color": "#FFFFFF", "position": {"x": 60, "y": 680},
                          "align": {"horizontal": "left", "vertical": "bottom"}, "background": {"color": "#000000AA", "padding": 18},
                          "stroke": {"color": "#E63946", "width": 3}, "shadow": {"color": "#00000088", "offset_x": 5, "offset_y": 5}, "opacity": 0.95}},
                {"element_id": "speaker", "type": "text", "z_index": 10, "text": {"text": "Ada Lovelace", "font_id": "sans", "font_size": 40, "color": "#FCA311", "position": {"x": 60, "y": 60}}}]}


def render_layers(document, output, layers, workspace):
    return run_tool("thumbnail/render_layers", {"document": document, "output": output, "layers": layers}, PathPolicy(str(workspace)))


def rebuild(directory):
    m = json.loads((directory / "layers.json").read_text(encoding="utf-8"))
    files = [e for e in sorted(m["layers"], key=lambda e: e["order"]) if e["file"]]
    base = Image.open(directory / files[0]["file"]).convert("RGBA")
    assert base.size == (m["canvas"]["width"], m["canvas"]["height"])
    for e in files[1:]:
        layer = Image.open(directory / e["file"])
        assert layer.mode == "RGBA" and layer.size == (e["size"]["width"], e["size"]["height"])
        base.alpha_composite(layer, dest=(e["position"]["x"], e["position"]["y"]))
    return m, base


def test_layers_composited_by_the_manifest_equal_the_thumbnail_pixel_for_pixel(media, workspace):
    res = render_layers(rich_document(media), {"path": "thumb.png", "format": "png"}, {"directory": "layers"}, workspace)
    assert res["ok"] is True, res
    m, rebuilt = rebuild(workspace / "layers")
    thumb = Image.open(workspace / "thumb.png")
    assert thumb.mode == "RGBA"
    assert rebuilt.tobytes() == thumb.tobytes()
    roles = [(e["role"], e["element_id"]) for e in m["layers"]]
    assert roles == [("background", None), ("image", "photo"), ("image", "side"), ("image", "offscreen"), ("image", "logo"),
                     ("text_background", "title"), ("text", "title"), ("text", "speaker")]
    off = next(e for e in m["layers"] if e["element_id"] == "offscreen")
    assert off["file"] is None and "omitted" in off
    logo = next(e for e in m["layers"] if e["element_id"] == "logo")
    assert logo["position"] == {"x": 1060, "y": 20} and logo["size"] == {"width": 200, "height": 200}
    assert m["verified"]["pixel_equal"] is True and res["layers"]["count"] == 7


def test_each_layer_holds_only_its_own_pixels(media, workspace):
    res = render_layers(rich_document(media), {"path": "t.png", "format": "png"}, {"directory": "l"}, workspace)
    assert res["ok"] is True
    m = json.loads((workspace / "l" / "layers.json").read_text(encoding="utf-8"))
    title = next(e for e in m["layers"] if e["role"] == "text" and e["element_id"] == "title")
    img = Image.open(workspace / "l" / title["file"])
    alpha = img.getchannel("A")
    assert alpha.getbbox() == (0, 0, img.width, img.height)          # cropped to its visible pixels
    assert alpha.getextrema()[0] == 0                                 # transparent between the glyphs: a designer can move it
    # the speaker layer sits where it was drawn
    speaker = next(e for e in m["layers"] if e["element_id"] == "speaker")
    assert 55 <= speaker["position"]["x"] <= 65 and 55 <= speaker["position"]["y"] <= 75


def test_jpeg_thumbnail_layers_equal_the_lossless_composite(media, workspace):
    res = render_layers(rich_document(media), {"path": "t.jpg", "format": "jpeg"}, {"directory": "lj"}, workspace)
    assert res["ok"] is True, res
    m, rebuilt = rebuild(workspace / "lj")
    assert "lossless composite" in m["verified"]["against"]
    png = render_layers(rich_document(media), {"path": "same.png", "format": "png"}, {"directory": "lp"}, workspace)
    assert png["ok"] is True
    assert rebuilt.tobytes() == Image.open(workspace / "same.png").tobytes()


def test_reused_render_still_writes_matching_layers(media, workspace):
    assert render_layers(rich_document(media), {"path": "a.png", "format": "png"}, {"directory": "la"}, workspace)["ok"]
    res = render_layers(rich_document(media), {"path": "b.png", "format": "png"}, {"directory": "lb"}, workspace)
    assert res["ok"] is True and res["reused"] is True
    _, rebuilt = rebuild(workspace / "lb")
    assert rebuilt.tobytes() == Image.open(workspace / "b.png").tobytes()


def test_video_frame_layers(media, workspace, ffmpeg_skill_dir, has_ffmpeg):
    if not has_ffmpeg or "video" not in media:
        pytest.skip("needs ffmpeg")
    doc = {"document_id": "vf", "canvas": {"width": 640, "height": 360},
           "assets": [{"asset_id": "f", "kind": "video_frame", "path": str(media["video"]), "timestamp": 1.0}],
           "elements": [{"element_id": "frame", "type": "image", "image": {"asset_id": "f", "position": {"x": 0, "y": 0}, "size": {"width": 640, "height": 360}}},
                        {"element_id": "t", "type": "text", "text": {"text": "Frame", "font_id": "sans-bold", "font_size": 48, "color": "#FFFFFF", "position": {"x": 20, "y": 20}}}]}
    res = run_tool("thumbnail/render_layers", {"document": doc, "output": {"path": "v.png", "format": "png"}, "layers": {"directory": "lv"}}, PathPolicy(str(workspace)), ffmpeg_skill_dir)
    assert res["ok"] is True, res
    _, rebuilt = rebuild(workspace / "lv")
    assert rebuilt.tobytes() == Image.open(workspace / "v.png").tobytes()


def test_non_empty_layer_directory_needs_overwrite(media, workspace):
    assert render_layers(rich_document(media), {"path": "o.png", "format": "png"}, {"directory": "lo"}, workspace)["ok"]
    res = render_layers(rich_document(media), {"path": "o2.png", "format": "png"}, {"directory": "lo"}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "OUTPUT_ERROR"
    assert not (workspace / "o2.png").exists()   # refused before the thumbnail is written
    doc = rich_document(media)
    doc["elements"] = doc["elements"][:2]
    res = render_layers(doc, {"path": "o2.png", "format": "png"}, {"directory": "lo", "overwrite": True}, workspace)
    assert res["ok"] is True
    names = sorted(p.name for p in (workspace / "lo").iterdir())
    assert names == ["00-background.png", "01-photo.png", "02-side.png", "layers.json"]   # the old layers are gone


@pytest.mark.parametrize("layers", [None, {}, {"directory": ""}, {"directory": "../x"}, {"directory": "x", "shell": "y"}])
def test_layers_request_validation(media, workspace, layers):
    params = {"document": rich_document(media), "output": {"path": "x.png", "format": "png"}}
    if layers is not None:
        params["layers"] = layers
    res = run_tool("thumbnail/render_layers", params, PathPolicy(str(workspace)))
    assert res["ok"] is False and res["error"]["code"] in ("INVALID_REQUEST", "PATH_NOT_ALLOWED")


def test_cli_render_with_layers(media, workspace, capsys):
    req = workspace / "req.json"
    req.write_text(json.dumps({"document": rich_document(media), "output": {"path": "cli.png", "format": "png"}}), encoding="utf-8")
    assert cli_main(["render", str(req), "--layers", "cli-layers", "--workspace", str(workspace), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["layers"]["verified"]["pixel_equal"] is True
    _, rebuilt = rebuild(workspace / "cli-layers")
    assert rebuilt.tobytes() == Image.open(workspace / "cli.png").tobytes()
    assert cli_main(["render", str(req), "--layers", "x", "--variants", "2", "--workspace", str(workspace), "--json"]) != 0
    capsys.readouterr()


def test_contract_declares_render_layers():
    c = skill_contract()
    t = next(t for t in c["tools"] if t["tool_id"] == "thumbnail/render_layers")
    assert t["deterministic"] is True and t["produces_output"] is True
    assert c["layered_output"]["manifest"]["file"] == "layers.json"
    assert {"id": "thumbnail.render_layers", "lifecycle": "EXPERIMENTAL", "tool_id": "thumbnail/render_layers"} in c["provides"]

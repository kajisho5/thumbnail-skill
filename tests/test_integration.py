"""Real-media end-to-end tests: actual Pillow rendering against real image fixtures, and (when an
ffmpeg-skill checkout is available) an actual video frame extraction. No mocking of the renderer or
the adapter's subprocess calls: these exercise the real pipeline."""
from __future__ import annotations

import json

from PIL import Image

from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool


def render(document, output, workspace, options=None, ffmpeg_skill_dir=None):
    policy = PathPolicy(str(workspace))
    params = {"document": document, "output": output, "options": options or {}}
    return run_tool("thumbnail/render", params, policy, ffmpeg_skill_dir)


def basic_document(media, canvas=(1280, 720)):
    return {"document_id": "e2e", "canvas": {"width": canvas[0], "height": canvas[1], "background": "#101010"},
            "assets": [{"asset_id": "bg", "kind": "image", "path": str(media["bg_jpg"])}, {"asset_id": "logo", "kind": "image", "path": str(media["logo"])}],
            "elements": [
                {"element_id": "bg_el", "type": "image", "z_index": 0, "image": {"asset_id": "bg", "position": {"x": 0, "y": 0}, "size": {"width": canvas[0], "height": canvas[1]}, "fit": "cover"}},
                {"element_id": "logo_el", "type": "image", "z_index": 5, "image": {"asset_id": "logo", "position": {"x": canvas[0] - 220, "y": 20}, "size": {"width": 200, "height": 200}, "fit": "contain"}},
                {"element_id": "title", "type": "text", "z_index": 10,
                 "text": {"text": "Line One\nLine Two", "font_id": "sans-bold", "font_size": 48, "color": "#FFFFFF", "position": {"x": 40, "y": canvas[1] - 140},
                          "background": {"color": "#000000AA", "padding": 12}, "stroke": {"color": "#000000", "width": 2}}},
            ]}


def test_render_still_image_thumbnail_png(media, workspace):
    out = workspace / "out.png"
    res = render(basic_document(media), {"path": str(out), "format": "png"}, workspace)
    assert res["ok"] is True and res["status"] == "ok"
    body = res
    assert body["width"] == 1280 and body["height"] == 720
    assert body["format"] == "png"
    assert out.is_file()
    img = Image.open(out)
    assert img.size == (1280, 720)
    assert img.format == "PNG"


def test_render_jpeg_output(media, workspace):
    out = workspace / "out.jpg"
    res = render(basic_document(media), {"path": str(out), "format": "jpeg", "jpeg_quality": 85}, workspace)
    assert res["ok"] is True
    img = Image.open(out)
    assert img.format == "JPEG"
    assert img.mode == "RGB"   # JPEG never carries the alpha channel


def test_deterministic_render_same_bytes(media, workspace):
    doc = basic_document(media)
    out1 = workspace / "a.png"
    out2 = workspace / "b.png"
    r1 = render(doc, {"path": str(out1), "format": "png"}, workspace)
    r2 = render(doc, {"path": str(out2), "format": "png"}, workspace)
    assert r1["sha256"] == r2["sha256"]
    assert out1.read_bytes() == out2.read_bytes()


def test_reuse_hits_cache_on_second_render(media, workspace):
    doc = basic_document(media)
    out = workspace / "out.png"
    r1 = render(doc, {"path": str(out), "format": "png"}, workspace)
    assert r1["reused"] is False
    out.unlink()
    r2 = render(doc, {"path": str(out), "format": "png"}, workspace)
    assert r2["reused"] is True
    assert r2["sha256"] == r1["sha256"]


def test_reuse_disabled_still_produces_identical_output(media, workspace):
    doc = basic_document(media)
    out = workspace / "out.png"
    r1 = render(doc, {"path": str(out), "format": "png"}, workspace, options={"reuse": False})
    out.unlink()
    r2 = render(doc, {"path": str(out), "format": "png"}, workspace, options={"reuse": False})
    assert r2["reused"] is False
    assert r1["sha256"] == r2["sha256"]


def test_reuse_rebuilds_when_cache_entry_is_corrupted(media, workspace):
    doc = basic_document(media)
    out = workspace / "out.png"
    render(doc, {"path": str(out), "format": "png"}, workspace)
    cache_dir = workspace / ".thumbnail-skill" / "cache"
    cache_files = list(cache_dir.glob("*.png"))
    assert cache_files
    cache_files[0].write_bytes(b"not a png anymore")
    out.unlink()
    res = render(doc, {"path": str(out), "format": "png"}, workspace)
    assert res["ok"] is True
    assert res["reused"] is False   # a broken cache entry is rebuilt, never returned as reused
    assert Image.open(out).size == (1280, 720)


def test_z_order_stacking(media, workspace):
    """A red square drawn above a green square (higher z_index) must be visible on top."""
    doc = {"document_id": "z", "canvas": {"width": 100, "height": 100, "background": "#000000"},
           "assets": [{"asset_id": "green", "kind": "image", "path": str(media["tall"])}, {"asset_id": "logo", "kind": "image", "path": str(media["logo"])}],
           "elements": [
               {"element_id": "top_red", "type": "image", "z_index": 10, "image": {"asset_id": "logo", "position": {"x": 0, "y": 0}, "size": {"width": 100, "height": 100}, "fit": "fill"}},
               {"element_id": "under_green", "type": "image", "z_index": 1, "image": {"asset_id": "green", "position": {"x": 0, "y": 0}, "size": {"width": 100, "height": 100}, "fit": "fill"}},
           ]}
    out = workspace / "z.png"
    render(doc, {"path": str(out), "format": "png"}, workspace)
    px = Image.open(out).convert("RGB").getpixel((50, 50))
    assert px[0] > px[1]   # red (logo, z_index 10) wins over green (z_index 1) regardless of document order


def test_image_fit_modes_produce_expected_dimensions(media, workspace):
    for fit in ("cover", "contain", "fill", "none"):
        doc = {"document_id": f"fit-{fit}", "canvas": {"width": 300, "height": 300},
               "assets": [{"asset_id": "tall", "kind": "image", "path": str(media["tall"])}],
               "elements": [{"element_id": "e", "type": "image", "image": {"asset_id": "tall", "position": {"x": 0, "y": 0}, "size": {"width": 150, "height": 150}, "fit": fit}}]}
        out = workspace / f"fit_{fit}.png"
        res = render(doc, {"path": str(out), "format": "png"}, workspace)
        assert res["ok"] is True
        assert Image.open(out).size == (300, 300)   # canvas size is always exactly what was declared


def test_multiline_unicode_text_renders(media, workspace):
    doc = {"document_id": "text", "canvas": {"width": 800, "height": 200, "background": "#000000"}, "assets": [],
           "elements": [{"element_id": "t", "type": "text", "text": {"text": "第38回学会\n特別講演のお知らせ 🎤", "font_id": "cjk", "font_size": 32,
                        "color": "#ffffff", "position": {"x": 10, "y": 10}}}]}
    out = workspace / "text.png"
    res = render(doc, {"path": str(out), "format": "png"}, workspace)
    assert res["ok"] is True
    img = Image.open(out).convert("RGB")
    # some pixel became non-background (text actually drew something), without asserting exact glyph shapes
    assert any(img.getpixel((x, y)) != (0, 0, 0) for x in range(0, 800, 4) for y in range(0, 200, 4))


def test_provenance_fields_present(media, workspace):
    out = workspace / "out.png"
    res = render(basic_document(media), {"path": str(out), "format": "png"}, workspace)
    prov = res["provenance"]
    for key in ("skill", "skill_version", "operation", "engine", "engine_version", "identity", "reused", "assets", "fonts", "output_hash"):
        assert key in prov
    assert prov["output_hash"] == res["sha256"]
    assert len(prov["assets"]) == 2
    assert all("sha256" in a for a in prov["assets"])
    assert prov["fonts"][0]["font_id"] == "sans-bold"
    assert prov["fonts"][0]["font_file_hash"].startswith("sha256:")


def test_missing_asset_file_is_invalid_input(media, workspace):
    doc = basic_document(media)
    doc["assets"][0]["path"] = str(workspace / "does_not_exist.png")
    res = render(doc, {"path": str(workspace / "out.png"), "format": "png"}, workspace)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"


def test_not_an_image_file_is_invalid_input(media, workspace):
    doc = basic_document(media)
    doc["assets"][0]["path"] = str(media["not_image"])
    res = render(doc, {"path": str(workspace / "out.png"), "format": "png"}, workspace)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"


def test_malformed_run_request_never_crashes():
    from thumbnail_skill.skill import run_request
    for bad in ["not a dict", 5, None]:
        try:
            run_request(bad)
            assert False, "expected ThumbnailError"
        except Exception as e:
            assert type(e).__name__ == "ThumbnailError"


def test_cli_run_transport_round_trip(media, workspace, capsys, monkeypatch):
    """`thumbnail run -` reads one JSON request from stdin and prints exactly one JSON document."""
    import io
    import sys

    from thumbnail_skill.cli import main

    out_path = workspace / "cli_out.png"
    request = {"tool": "thumbnail/render", "params": {"document": basic_document(media), "output": {"path": str(out_path), "format": "png"}}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    code = main(["run", "-", "--workspace", str(workspace)])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert printed["result"]["output"] == str(out_path)
    assert out_path.is_file()


# ---- video_frame path (requires a real ffmpeg-skill checkout; skipped otherwise)

def test_video_frame_extraction_uses_exact_timestamp(media, workspace, ffmpeg_skill_dir, has_ffmpeg):
    if "video" not in media or not has_ffmpeg:
        import pytest
        pytest.skip("ffmpeg not available to build the video fixture")
    doc = {"document_id": "vf", "canvas": {"width": 320, "height": 180, "background": "#000000"},
           "assets": [{"asset_id": "frame", "kind": "video_frame", "path": str(media["video"]), "timestamp": 1.5}],
           "elements": [{"element_id": "e", "type": "image", "image": {"asset_id": "frame", "position": {"x": 0, "y": 0}, "size": {"width": 320, "height": 180}, "fit": "cover"}}]}
    out = workspace / "frame_thumb.png"
    res = render(doc, {"path": str(out), "format": "png"}, workspace, ffmpeg_skill_dir=ffmpeg_skill_dir)
    assert res["ok"] is True, res
    assert Image.open(out).size == (320, 180)
    assert res["provenance"]["assets"][0]["timestamp"] == 1.5


def test_video_frame_timestamp_beyond_duration_rejected(media, workspace, ffmpeg_skill_dir, has_ffmpeg):
    if "video" not in media or not has_ffmpeg:
        import pytest
        pytest.skip("ffmpeg not available to build the video fixture")
    doc = {"document_id": "vf2", "canvas": {"width": 320, "height": 180},
           "assets": [{"asset_id": "frame", "kind": "video_frame", "path": str(media["video"]), "timestamp": 999.0}],
           "elements": [{"element_id": "e", "type": "image", "image": {"asset_id": "frame", "position": {"x": 0, "y": 0}, "size": {"width": 320, "height": 180}}}]}
    res = render(doc, {"path": str(workspace / "out.png"), "format": "png"}, workspace, ffmpeg_skill_dir=ffmpeg_skill_dir)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_TIME_RANGE"


def test_extract_frame_tool_matches_timestamp(media, workspace, ffmpeg_skill_dir, has_ffmpeg):
    if "video" not in media or not has_ffmpeg:
        import pytest
        pytest.skip("ffmpeg not available to build the video fixture")
    policy = PathPolicy(str(workspace))
    params = {"source": {"path": str(media["video"]), "timestamp": 0.5}, "output": {"path": str(workspace / "frame.png"), "format": "png"}}
    res = run_tool("thumbnail/extract_frame", params, policy, ffmpeg_skill_dir)
    assert res["ok"] is True, res
    assert (workspace / "frame.png").is_file()
    assert res["provenance"]["source"]["timestamp"] == 0.5


def test_no_automatic_best_frame_selection_is_not_a_supported_operation():
    """Guard against scope creep: nothing in the public tool surface performs frame scoring/selection."""
    from thumbnail_skill.executor import TOOLS
    for name in ("thumbnail/select_best_frame", "thumbnail/rank_frames", "thumbnail/auto_layout", "thumbnail/generate_title"):
        assert name not in TOOLS

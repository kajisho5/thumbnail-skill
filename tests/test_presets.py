"""Platform size presets (`output.preset`, ADR-9): every preset renders at its own size and passes its
own check; explicit canvas width/height win over the preset's size; a written artifact that exceeds a
preset (dimensions, aspect ratio, format, file size) fails VALIDATION_ERROR and never reaches the
output path. Real Pillow renders against real fixtures, no mocking."""
from __future__ import annotations

import random

import pytest
from PIL import Image

from thumbnail_skill.contract import skill_contract
from thumbnail_skill.errors import ThumbnailError
from thumbnail_skill.model import MAX_CANVAS, MIN_CANVAS, PLATFORM_PRESETS, check_preset, parse_output, parse_render_request, preset_violations
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool

PRESETS = sorted(PLATFORM_PRESETS)


def render(document, output, workspace, options=None, ffmpeg_skill_dir=None):
    return run_tool("thumbnail/render", {"document": document, "output": output, "options": options or {}}, PathPolicy(str(workspace)), ffmpeg_skill_dir)


def preset_document(media, canvas=None):
    """A document whose canvas carries no size of its own unless `canvas` is given: the preset decides."""
    c = {"background": "#202020", **(canvas or {})}
    return {"document_id": "preset", "canvas": c,
            "assets": [{"asset_id": "bg", "kind": "image", "path": str(media["bg_jpg"])}],
            "elements": [{"element_id": "bg_el", "type": "image", "image": {"asset_id": "bg", "position": {"x": 0, "y": 0}, "size": {"width": 64, "height": 64}, "fit": "cover"}}]}


def noise_image(path, size):
    """Incompressible pixels: a PNG of this is far larger than any 2 MB thumbnail limit."""
    rng = random.Random(0)
    Image.frombytes("RGB", size, rng.randbytes(size[0] * size[1] * 3)).save(path, format="PNG")
    return path


# ---- the table itself

@pytest.mark.parametrize("name", PRESETS)
def test_preset_own_size_passes_its_own_limits(name):
    p = PLATFORM_PRESETS[name]
    assert MIN_CANVAS <= p["width"] <= MAX_CANVAS and MIN_CANVAS <= p["height"] <= MAX_CANVAS
    assert preset_violations(name, p["width"], p["height"], p["formats"][0], p["max_bytes"]) == []
    assert p["sources"], "every preset cites the platform's own documentation"
    assert set(p["unverified"]) <= set(p) - {"platform", "surface", "unverified", "sources"}


@pytest.mark.parametrize("name", PRESETS)
def test_preset_one_byte_over_limit_fails(name):
    p = PLATFORM_PRESETS[name]
    with pytest.raises(ThumbnailError) as e:
        check_preset(name, p["width"], p["height"], p["formats"][0], p["max_bytes"] + 1)
    assert e.value.code == "VALIDATION_ERROR"
    assert e.value.details["reason"] == "preset_limit_exceeded" and e.value.details["preset"] == name
    assert [v["check"] for v in e.value.details["violations"]] == ["file_size"]


@pytest.mark.parametrize("name", PRESETS)
def test_preset_wrong_shape_fails_aspect_ratio(name):
    p = PLATFORM_PRESETS[name]
    wrong = (p["height"], p["width"]) if p["width"] != p["height"] else (p["width"], p["height"] // 2)   # rotated, or 2:1 for a square
    checks = {v["check"] for v in preset_violations(name, wrong[0], wrong[1], p["formats"][0], 1)}
    assert "aspect_ratio" in checks


def test_named_sizes():
    assert (PLATFORM_PRESETS["youtube"]["width"], PLATFORM_PRESETS["youtube"]["height"]) == (1280, 720)
    for vertical in ("youtube-shorts", "tiktok", "instagram-reels"):
        assert (PLATFORM_PRESETS[vertical]["width"], PLATFORM_PRESETS[vertical]["height"]) == (1080, 1920)
    assert (PLATFORM_PRESETS["instagram-feed"]["width"], PLATFORM_PRESETS["instagram-feed"]["height"]) == (1080, 1080)
    assert (PLATFORM_PRESETS["x"]["width"], PLATFORM_PRESETS["x"]["height"]) == (1600, 900)


def test_without_a_preset_nothing_is_checked():
    assert check_preset(None, 1, 1, "png", 10 ** 12) is None


# ---- request parsing / precedence

def test_unknown_preset_rejected():
    with pytest.raises(ThumbnailError) as e:
        parse_output({"path": "o.png", "format": "png", "preset": "myspace"})
    assert e.value.code == "INVALID_REQUEST"


def test_preset_supplies_canvas_size_when_omitted(media):
    req = parse_render_request({"document": preset_document(media), "output": {"path": "o.jpg", "format": "jpeg", "preset": "x"}})
    assert (req.document.canvas.width, req.document.canvas.height) == (1600, 900)


def test_explicit_canvas_size_wins_over_preset(media):
    req = parse_render_request({"document": preset_document(media, {"width": 1920, "height": 1080}), "output": {"path": "o.jpg", "format": "jpeg", "preset": "youtube"}})
    assert (req.document.canvas.width, req.document.canvas.height) == (1920, 1080)


def test_partial_canvas_size_with_preset_rejected(media):
    with pytest.raises(ThumbnailError) as e:
        parse_render_request({"document": preset_document(media, {"width": 1920}), "output": {"path": "o.jpg", "format": "jpeg", "preset": "youtube"}})
    assert e.value.code == "INVALID_REQUEST" and e.value.details["reason"] == "partial_canvas_size"


def test_canvas_size_still_required_without_preset(media):
    with pytest.raises(ThumbnailError) as e:
        parse_render_request({"document": preset_document(media), "output": {"path": "o.jpg", "format": "jpeg"}})
    assert e.value.code == "INVALID_REQUEST"


# ---- real renders

@pytest.mark.parametrize("name", PRESETS)
def test_render_each_preset(name, media, workspace):
    p = PLATFORM_PRESETS[name]
    out = workspace / f"{name}.jpg"
    res = render(preset_document(media), {"path": str(out), "format": "jpeg", "preset": name}, workspace)
    assert res["ok"] is True, res
    assert (res["width"], res["height"]) == (p["width"], p["height"])
    assert res["preset"] == {"name": name, "platform": p["platform"], "checked": ["dimensions", "aspect_ratio", "format", "file_size"], "ok": True}
    assert res["provenance"]["preset"] == name
    img = Image.open(out)
    assert img.size == (p["width"], p["height"]) and img.format == "JPEG"
    assert out.stat().st_size <= p["max_bytes"]


def test_response_has_no_preset_key_without_a_preset(media, workspace):
    res = render(preset_document(media, {"width": 320, "height": 180}), {"path": str(workspace / "o.png"), "format": "png"}, workspace)
    assert res["ok"] is True and "preset" not in res and "preset" not in res["provenance"]


def test_explicit_size_within_preset_limits_renders(media, workspace):
    out = workspace / "yt1080.jpg"
    res = render(preset_document(media, {"width": 1920, "height": 1080}), {"path": str(out), "format": "jpeg", "preset": "youtube"}, workspace)
    assert res["ok"] is True, res
    assert Image.open(out).size == (1920, 1080)


def test_explicit_size_outside_preset_fails_and_writes_nothing(media, workspace):
    out = workspace / "square_for_youtube.jpg"
    res = render(preset_document(media, {"width": 1000, "height": 1000}), {"path": str(out), "format": "jpeg", "preset": "youtube"}, workspace)
    assert res["ok"] is False
    err = res["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["retryable"] is False
    assert err["details"]["reason"] == "preset_limit_exceeded"
    assert [v["check"] for v in err["details"]["violations"]] == ["aspect_ratio"]
    assert not out.exists()


def test_format_not_accepted_by_preset_fails(media, workspace):
    out = workspace / "tiktok.png"
    res = render(preset_document(media), {"path": str(out), "format": "png", "preset": "tiktok"}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "VALIDATION_ERROR"
    assert [v["check"] for v in res["error"]["details"]["violations"]] == ["format"]
    assert not out.exists()


def noise_document(path):
    return {"document_id": "noise", "canvas": {"background": "#000000"}, "assets": [{"asset_id": "n", "kind": "image", "path": str(path)}],
            "elements": [{"element_id": "n", "type": "image", "image": {"asset_id": "n", "position": {"x": 0, "y": 0}, "size": {"width": 1280, "height": 720}, "fit": "fill"}}]}


def test_over_file_size_limit_fails_and_is_neither_placed_nor_cached(workspace):
    doc = noise_document(noise_image(workspace / "noise.png", (1280, 720)))
    out = workspace / "too_big.png"
    res = render(doc, {"path": str(out), "format": "png", "preset": "youtube"}, workspace)
    assert res["ok"] is False, res
    err = res["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["preset"] == "youtube"
    (violation,) = err["details"]["violations"]
    assert violation["check"] == "file_size" and violation["expected"] == 2_000_000 and violation["got"] > 2_000_000
    assert not out.exists()
    assert not list((workspace / ".thumbnail-skill" / "tmp").glob("*.png"))   # the rejected render is discarded
    # nothing was cached: the same document without a preset renders afresh
    doc["canvas"].update({"width": 1280, "height": 720})
    again = render(doc, {"path": str(workspace / "ok.png"), "format": "png"}, workspace)
    assert again["ok"] is True and again["reused"] is False


def test_cache_hit_is_checked_against_the_preset_too(workspace):
    doc = noise_document(noise_image(workspace / "noise.png", (1280, 720)))
    doc["canvas"].update({"width": 1280, "height": 720})
    first = render(doc, {"path": str(workspace / "a.png"), "format": "png"}, workspace)
    assert first["ok"] is True
    out = workspace / "b.png"
    res = render(doc, {"path": str(out), "format": "png", "preset": "youtube"}, workspace)   # same identity -> cache hit
    assert res["ok"] is False and res["error"]["details"]["violations"][0]["check"] == "file_size"
    assert not out.exists()
    # the same cached artifact (1280x720, 16:9, between 2 MB and 5 MB) does fit X's 5 MB limit
    ok = render(doc, {"path": str(workspace / "c.png"), "format": "png", "preset": "x"}, workspace)
    assert ok["ok"] is True and ok["reused"] is True and ok["preset"]["name"] == "x"


def test_validate_tool_reports_the_preset_size(media):
    res = run_tool("thumbnail/validate", {"document": preset_document(media), "output": {"path": "o.jpg", "format": "jpeg", "preset": "instagram-feed"}})
    assert res["ok"] is True
    assert res["validation"]["canvas"]["width"] == 1080 and res["validation"]["canvas"]["height"] == 1080


def test_extract_frame_checks_the_preset(media, workspace, ffmpeg_skill_dir, has_ffmpeg):
    if "video" not in media or not has_ffmpeg:
        pytest.skip("ffmpeg not available to build the video fixture")
    policy = PathPolicy(str(workspace))
    src = {"path": str(media["video"]), "timestamp": 0.5}
    ok = run_tool("thumbnail/extract_frame", {"source": src, "output": {"path": str(workspace / "f.jpg"), "format": "jpeg", "preset": "youtube"}}, policy, ffmpeg_skill_dir)
    assert ok["ok"] is True, ok
    assert ok["preset"]["name"] == "youtube"   # a 16:9 source frame fits the 16:9 preset
    bad = run_tool("thumbnail/extract_frame", {"source": src, "output": {"path": str(workspace / "g.jpg"), "format": "jpeg", "preset": "tiktok"}}, policy, ffmpeg_skill_dir)
    assert bad["ok"] is False and bad["error"]["code"] == "VALIDATION_ERROR"
    assert "aspect_ratio" in {v["check"] for v in bad["error"]["details"]["violations"]}
    assert not (workspace / "g.jpg").exists()


# ---- contract

def test_contract_declares_every_preset_from_the_same_table():
    c = skill_contract()["platform_presets"]
    assert c["field"] == "output.preset"
    assert set(c["presets"]) == set(PLATFORM_PRESETS)
    for name, p in c["presets"].items():
        src = PLATFORM_PRESETS[name]
        assert (p["width"], p["height"], p["max_bytes"], p["formats"]) == (src["width"], src["height"], src["max_bytes"], list(src["formats"]))
        assert p["sources"] and all(u.startswith("https://") for u in p["sources"])
        assert set(p["formats"]) <= set(skill_contract()["output_formats"])

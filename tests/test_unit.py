"""Unit tests: model validation, fonts, canonical hashing. No file system, no ffmpeg-skill."""
from __future__ import annotations

import pytest

from thumbnail_skill import fonts
from thumbnail_skill.canonical import canonical_json, stable_hash
from thumbnail_skill.errors import ERROR_CODES, ThumbnailError
from thumbnail_skill.model import parse_asset, parse_document, parse_options, parse_output, parse_render_request


def doc(**overrides):
    base = {"document_id": "doc1", "canvas": {"width": 640, "height": 360},
            "elements": [{"element_id": "e1", "type": "text", "text": {"text": "hi", "font_id": "sans", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}}]}
    base.update(overrides)
    return base


def test_minimal_document_parses():
    d = parse_document(doc())
    assert d.document_id == "doc1"
    assert d.canvas.width == 640 and d.canvas.height == 360
    assert d.canvas.background == "#000000"
    assert len(d.elements) == 1


def test_canvas_bounds():
    with pytest.raises(ThumbnailError) as e:
        parse_document(doc(canvas={"width": 4, "height": 360}))
    assert e.value.code == "INVALID_REQUEST"
    with pytest.raises(ThumbnailError):
        parse_document(doc(canvas={"width": 640, "height": 100000}))


def test_unknown_field_rejected():
    d = doc()
    d["canvas"]["nonsense"] = 1
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "INVALID_REQUEST"
    assert "unknown field" in e.value.message


@pytest.mark.parametrize("key", ["command", "argv", "shell", "filter", "filter_complex", "env", "executable", "html", "css"])
def test_forbidden_fields_rejected_anywhere(key):
    d = doc()
    d["elements"][0][key] = "x"
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "INVALID_REQUEST"
    assert e.value.details.get("reason") == "forbidden_field"


def test_duplicate_element_id_rejected():
    d = doc(elements=[
        {"element_id": "e1", "type": "text", "text": {"text": "a", "font_id": "sans", "font_size": 20, "color": "#fff000", "position": {"x": 0, "y": 0}}},
        {"element_id": "e1", "type": "text", "text": {"text": "b", "font_id": "sans", "font_size": 20, "color": "#fff000", "position": {"x": 0, "y": 0}}},
    ])
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "DEPENDENCY_ERROR"


def test_image_element_requires_declared_asset():
    d = doc(elements=[{"element_id": "e1", "type": "image", "image": {"asset_id": "missing", "position": {"x": 0, "y": 0}, "size": {"width": 10, "height": 10}}}])
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "MISSING_INPUT"


def test_image_element_cannot_carry_text_field_and_vice_versa():
    d = doc(elements=[{"element_id": "e1", "type": "text", "text": {"text": "a", "font_id": "sans", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}, "image": {}}])
    with pytest.raises(ThumbnailError):
        parse_document(d)


@pytest.mark.parametrize("kind,extra", [("image", {}), ("video_frame", {"timestamp": 1.0})])
def test_asset_kinds(kind, extra):
    a = parse_asset({"asset_id": "a1", "kind": kind, "path": "x.png", **extra}, "asset")
    assert a.kind == kind


def test_video_frame_requires_timestamp():
    with pytest.raises(ThumbnailError) as e:
        parse_asset({"asset_id": "a1", "kind": "video_frame", "path": "x.mp4"}, "asset")
    assert e.value.code == "INVALID_REQUEST"


def test_image_asset_forbids_timestamp():
    with pytest.raises(ThumbnailError) as e:
        parse_asset({"asset_id": "a1", "kind": "image", "path": "x.png", "timestamp": 1.0}, "asset")
    assert e.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("ts", [-1.0, -0.001])
def test_negative_timestamp_rejected(ts):
    with pytest.raises(ThumbnailError) as e:
        parse_asset({"asset_id": "a1", "kind": "video_frame", "path": "x.mp4", "timestamp": ts}, "asset")
    assert e.value.code == "INVALID_TIME_RANGE"


def test_timestamp_beyond_max_rejected():
    with pytest.raises(ThumbnailError) as e:
        parse_asset({"asset_id": "a1", "kind": "video_frame", "path": "x.mp4", "timestamp": 10 ** 9}, "asset")
    assert e.value.code == "INVALID_TIME_RANGE"


def test_nan_and_inf_timestamp_rejected():
    with pytest.raises(ThumbnailError):
        parse_asset({"asset_id": "a1", "kind": "video_frame", "path": "x.mp4", "timestamp": float("nan")}, "asset")
    with pytest.raises(ThumbnailError):
        parse_asset({"asset_id": "a1", "kind": "video_frame", "path": "x.mp4", "timestamp": float("inf")}, "asset")


def test_unsupported_rotation_rejected():
    d = doc(assets=[{"asset_id": "a", "kind": "image", "path": "x.png"}],
            elements=[{"element_id": "e1", "type": "image", "image": {"asset_id": "a", "position": {"x": 0, "y": 0}, "size": {"width": 10, "height": 10}, "rotation": 45}}])
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "UNSUPPORTED_OPERATION"


def test_unsupported_output_format_rejected():
    with pytest.raises(ThumbnailError) as e:
        parse_output({"path": "out.gif", "format": "gif"})
    assert e.value.code == "UNSUPPORTED_FORMAT"


def test_output_extension_must_match_format():
    with pytest.raises(ThumbnailError) as e:
        parse_output({"path": "out.jpg", "format": "png"})
    assert e.value.code == "UNSUPPORTED_FORMAT"


def test_jpeg_quality_only_valid_for_jpeg():
    with pytest.raises(ThumbnailError):
        parse_output({"path": "out.png", "format": "png", "jpeg_quality": 80})
    out = parse_output({"path": "out.jpg", "format": "jpeg", "jpeg_quality": 80})
    assert out.jpeg_quality == 80


@pytest.mark.parametrize("text", ["", "x" * 3000, "line\x01break"])
def test_bad_text_strings_rejected(text):
    d = doc(elements=[{"element_id": "e1", "type": "text", "text": {"text": text, "font_id": "sans", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}}])
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "INVALID_REQUEST"


def test_multiline_and_unicode_text_accepted():
    d = doc(elements=[{"element_id": "e1", "type": "text",
                       "text": {"text": "第38回学会\n特別講演\n😀", "font_id": "cjk", "font_size": 20, "color": "#ffffff", "position": {"x": 0, "y": 0}}}])
    parsed = parse_document(d)
    assert parsed.elements[0].text.text.count("\n") == 2


@pytest.mark.parametrize("bad_color", ["red", "#fff", "#gggggg", "#12345", 123])
def test_bad_colors_rejected(bad_color):
    d = doc(elements=[{"element_id": "e1", "type": "text", "text": {"text": "hi", "font_id": "sans", "font_size": 20, "color": bad_color, "position": {"x": 0, "y": 0}}}])
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "INVALID_REQUEST"


def test_options_defaults_and_bounds():
    opts = parse_options(None)
    assert opts["reuse"] is True and opts["timeout"] == 120.0
    with pytest.raises(ThumbnailError):
        parse_options({"timeout": 0})
    with pytest.raises(ThumbnailError):
        parse_options({"unknown": 1})


def test_render_request_requires_document():
    with pytest.raises(ThumbnailError):
        parse_render_request({})


def test_render_request_wrong_schema_rejected():
    with pytest.raises(ThumbnailError):
        parse_render_request({"schema": "not-a-schema", "document": doc()})


def test_deeply_nested_metadata_rejected_cleanly_not_a_recursion_error():
    """`_reject_forbidden` walks the raw document (including free-form `metadata`) before any other
    check, including the metadata byte-size cap. Without its own depth bound, a small (a few KB)
    but deeply nested payload drives Python's recursion limit into an uncaught RecursionError before
    MAX_METADATA_BYTES ever gets a chance to apply."""
    nested = {}
    cur = nested
    for _ in range(500):
        cur["k"] = {}
        cur = cur["k"]
    d = doc(metadata=nested)
    with pytest.raises(ThumbnailError) as e:
        parse_document(d)
    assert e.value.code == "INVALID_REQUEST"
    assert e.value.details.get("reason") == "too_deeply_nested"


# ---- fonts

def test_font_ids_registered():
    assert "sans" in fonts.font_ids()


def test_unknown_font_id_raises_missing_input():
    with pytest.raises(ThumbnailError) as e:
        fonts.resolve_font("this-font-does-not-exist")
    assert e.value.code == "MISSING_INPUT"


def test_font_id_type_checked():
    with pytest.raises(ThumbnailError):
        fonts.resolve_font(123)


# ---- canonical

def test_canonical_json_is_key_order_independent():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_stable_hash_changes_with_content():
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def test_error_table_matches_task_error_codes():
    expected = {"INVALID_REQUEST", "INVALID_INPUT", "UNSUPPORTED_OPERATION", "UNSUPPORTED_FORMAT", "INVALID_TIME_RANGE",
                "DEPENDENCY_ERROR", "PATH_NOT_ALLOWED", "MISSING_INPUT", "OUTPUT_ERROR", "VALIDATION_ERROR", "TOOL_ERROR", "CANCELLED", "INTERNAL_ERROR"}
    assert set(ERROR_CODES) == expected

"""Frame choice and face-aware layout (ADR-15, roadmap step 6). `thumbnail/choose_frame` ranks a detection-skill
face document's frames by measured facts: a blinking frame and a sharp frame rank the sharp one first, and a
closed-eyes frame never ranks above an open one. `thumbnail/place_text` never puts the title box over a face. That
is checked geometrically and on rendered pixels: the face region of the render is byte-identical with and without
the title."""
from __future__ import annotations

import copy
import json

import pytest
from PIL import Image

from thumbnail_skill.canonical import sha256_file
from thumbnail_skill.cli import main as cli_main
from thumbnail_skill.contract import skill_contract
from thumbnail_skill.errors import ThumbnailError
from thumbnail_skill.frames import WEIGHTS, source_rect_to_canvas
from thumbnail_skill.model import Crop, ImageContent, Position, Size
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool

from .fixtures.generate import face_detections

FACE = [500, 160, 220, 260]


def choose(params, workspace):
    return run_tool("thumbnail/choose_frame", params, PathPolicy(str(workspace)))


def place(params, workspace):
    return run_tool("thumbnail/place_text", params, PathPolicy(str(workspace)))


def blink_and_sharp():
    return face_detections([
        (1.0, [(FACE, 0.22, 260.0)]),   # blinking (eyes closed), sharp enough
        (2.0, [(FACE, 0.86, 300.0)]),   # eyes open, sharp
        (3.0, [(FACE, 0.84, 25.0)]),    # eyes open, blurred
        (4.0, []),                      # no face
    ])


def test_blinking_and_sharp_frames_rank_the_sharp_open_one_first(workspace):
    res = choose({"detections": blink_and_sharp()}, workspace)
    assert res["ok"] is True, res
    order = [(e["t"], e["rank"]) for e in res["ranking"]]
    assert order == [(2.0, 1), (3.0, 2), (1.0, 3), (4.0, None)]
    assert res["chosen"]["t"] == 2.0 and res["chosen"]["eyes_closed"] is False
    blink = next(e for e in res["ranking"] if e["t"] == 1.0)
    assert blink["eyes_closed"] is True
    # the score is the stated weighted sum of its parts, and exposure (not measured) is listed as missing
    for e in res["ranking"][:3]:
        assert e["missing"] == ["exposure"]
        assert abs(e["score"] - sum(WEIGHTS[k] * (v or 0) for k, v in e["parts"].items())) < 1e-3
    assert res["ranking"][3]["score"] is None and res["ranking"][3]["faces"] == []


def test_closed_eyes_rank_last_even_when_sharper(workspace):
    doc = face_detections([(1.0, [(FACE, 0.3, 5000.0)]), (2.0, [(FACE, 0.6, 20.0)])])
    res = choose({"detections": doc}, workspace)
    assert [e["t"] for e in res["ranking"]] == [2.0, 1.0]
    raw = sum(WEIGHTS[k] * (v or 0) for k, v in res["ranking"][1]["parts"].items())
    assert raw > res["ranking"][0]["score"]   # higher score, still after: eyes closed is ordered first


def test_frame_measures_supply_sharpness_and_exposure(workspace):
    doc = face_detections([(1.0, [(FACE, 0.9, 300.0)]), (2.0, [(FACE, 0.9, 300.0)])])
    measures = [{"t": 1.0, "sharpness": 20.0, "mean_luma": 0.95}, {"t": 2.0, "sharpness": 400.0, "mean_luma": 0.5}]
    res = choose({"detections": doc, "measures": measures}, workspace)
    first = res["ranking"][0]
    assert first["t"] == 2.0 and first["missing"] == [] and first["parts"]["exposure"] == 1.0
    assert first["sources"]["sharpness"] == "caller measures" and first["sources"]["exposure"] == "caller measures"
    assert res["ranking"][1]["parts"]["exposure"] == pytest.approx(0.1)


def test_main_face_is_the_largest_and_weights_can_be_given(workspace):
    doc = face_detections([(1.0, [((10, 10, 40, 40), 0.1, 300.0), (FACE, 0.9, 300.0)]), (2.0, [(FACE, 0.7, 300.0)])])
    res = choose({"detections": doc, "weights": {"eyes_open": 1, "sharpness": 0, "exposure": 0, "frontal": 0, "size": 0}}, workspace)
    assert res["ranking"][0]["t"] == 1.0 and res["ranking"][0]["main_face"] == 1
    assert res["method"]["weights"]["eyes_open"] == 1.0
    assert res["ranking"][0]["score"] == pytest.approx(0.9)


def test_video_fingerprint_is_checked_and_the_chosen_frame_becomes_an_asset(media, workspace):
    video = media["bg_jpg"]   # any file: only its sha256 is compared
    doc = blink_and_sharp()
    res = choose({"detections": doc, "video": str(video)}, workspace)
    assert res["ok"] is False and res["error"]["details"]["reason"] == "detections_source_mismatch"
    doc["source"]["fingerprint"] = f"sha256:{sha256_file(str(video))}"
    res = choose({"detections": doc, "video": str(video)}, workspace)
    assert res["ok"] is True and res["asset"] == {"kind": "video_frame", "path": str(video.resolve()), "timestamp": 2.0}


def test_detections_from_a_file(workspace):
    p = workspace / "faces.json"
    p.write_text(json.dumps(blink_and_sharp()), encoding="utf-8")
    res = choose({"detections_path": str(p)}, workspace)
    assert res["ok"] is True and res["chosen"]["t"] == 2.0


def test_every_frame_closed_is_warned(workspace):
    res = choose({"detections": face_detections([(1.0, [(FACE, 0.2, 300.0)])])}, workspace)
    assert res["ok"] is True and res["chosen"]["eyes_closed"] is True and res["warnings"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema="detection-skill/tracks@1"),
    lambda d: d.update(kind="text"),
    lambda d: [det.pop("attributes") for fr in d["frames"] for det in fr["detections"]],
    lambda d: d["frames"][0]["detections"][0].update(box=[0, 0, -1, 5]),
    lambda d: d.update(frames=[]),
])
def test_invalid_detections_are_refused(workspace, mutate):
    doc = blink_and_sharp()
    mutate(doc)
    res = choose({"detections": doc}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_REQUEST"


# ---- placement

def frame_document(media, fit="cover", title="Scaling Live Captions"):
    return {"document_id": "face", "canvas": {"width": 1280, "height": 720, "background": "#000000"},
            "assets": [{"asset_id": "frame", "kind": "image", "path": str(media["bg_png"])}],
            "elements": [
                {"element_id": "frame_el", "type": "image", "image": {"asset_id": "frame", "position": {"x": 0, "y": 0}, "size": {"width": 1280, "height": 720}, "fit": fit}},
                {"element_id": "title", "type": "text", "z_index": 2,
                 "text": {"text": title, "font_id": "sans-bold", "font_size": 64, "color": "#FFFFFF", "position": {"x": 0, "y": 0},
                          "background": {"color": "#000000CC", "padding": 14}, "stroke": {"color": "#000000", "width": 3}, "shadow": {"color": "#00000088", "offset_x": 4, "offset_y": 4}}}]}


def _render(document, workspace, name):
    out = workspace / name
    res = run_tool("thumbnail/render", {"document": document, "output": {"path": str(out), "format": "png"}}, PathPolicy(str(workspace)))
    assert res["ok"] is True, res
    return Image.open(out).convert("RGBA")


@pytest.mark.parametrize("fx, fy", [(fx, fy) for fx in (0, 250, 500, 800, 1060) for fy in (0, 230, 460)])
def test_title_box_never_overlaps_the_face_box(media, workspace, fx, fy):
    # faces are in detection-skill frame pixels (1280x720); the frame image is 640x360 and the element covers the canvas
    face = [fx, fy, 220, 260]
    res = place({"document": frame_document(media), "text_element": "title", "image_element": "frame_el", "faces": [face],
                 "frame_size": {"width": 1280, "height": 720}, "margin": 0}, workspace)
    if not res["ok"]:
        assert res["error"]["details"]["reason"] == "no_position_clear_of_faces"
        return
    box = res["placement"]["text_box"]
    fb = res["placement"]["face_boxes"][0]
    assert fb == [float(fx), float(fy), float(fx + 220), float(fy + 260)]
    assert not (box[0] < fb[2] and fb[0] < box[2] and box[1] < fb[3] and fb[1] < box[3])
    assert box[0] >= 0 and box[1] >= 0 and box[2] <= 1280 and box[3] <= 720
    # on rendered pixels: the face region is byte-identical with and without the title
    with_title = _render(res["document"], workspace, "with.png")
    without = copy.deepcopy(res["document"])
    without["elements"] = [e for e in without["elements"] if e["element_id"] != "title"]
    no_title = _render(without, workspace, "without.png")
    region = (fx, fy, fx + 220, fy + 260)
    assert with_title.crop(region).tobytes() == no_title.crop(region).tobytes()
    assert with_title.tobytes() != no_title.tobytes()   # the title was drawn somewhere


def test_a_face_covering_every_candidate_is_refused(media, workspace):
    res = place({"document": frame_document(media), "text_element": "title", "image_element": "frame_el", "faces": [[0, 0, 1280, 720]],
                 "frame_size": {"width": 1280, "height": 720}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "VALIDATION_ERROR"
    assert res["error"]["details"]["reason"] == "no_position_clear_of_faces" and len(res["error"]["details"]["tried"]) == 8


def test_caller_candidates_are_tried_in_order(media, workspace):
    cands = [{"name": "over-face", "position": {"x": 600, "y": 300}}, {"name": "low", "position": {"x": 40, "y": 600}}]
    res = place({"document": frame_document(media), "text_element": "title", "image_element": "frame_el", "faces": [FACE],
                 "frame_size": {"width": 1280, "height": 720}, "candidates": cands}, workspace)
    assert res["ok"] is True and res["placement"]["chosen"] == "low"
    assert [t["clear"] for t in res["placement"]["tried"]] == [False, True]
    title = next(e for e in res["document"]["elements"] if e["element_id"] == "title")
    assert title["text"]["position"] == {"x": 40, "y": 600}


def test_video_frame_asset_needs_frame_size(media, workspace):
    doc = frame_document(media)
    doc["assets"][0] = {"asset_id": "frame", "kind": "video_frame", "path": str(media["bg_png"]), "timestamp": 1.0}
    res = place({"document": doc, "text_element": "title", "image_element": "frame_el", "faces": [FACE]}, workspace)
    assert res["ok"] is False and res["error"]["details"]["field"] == "frame_size"
    res = place({"document": doc, "text_element": "title", "image_element": "frame_el", "faces": [FACE], "frame_size": {"width": 1280, "height": 720}}, workspace)
    assert res["ok"] is True


def test_rotated_frame_is_unsupported(media, workspace):
    doc = frame_document(media)
    doc["elements"][0]["image"]["rotation"] = 90
    res = place({"document": doc, "text_element": "title", "image_element": "frame_el", "faces": [FACE], "frame_size": {"width": 1280, "height": 720}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "UNSUPPORTED_OPERATION"


def _content(fit, crop=None, pos=(10, 20), size=(400, 300)):
    return ImageContent("a", Position(*pos), Size(*size), fit, crop)


def test_source_rect_mapping_matches_the_renderer_arithmetic():
    # fill: independent x/y scale
    assert source_rect_to_canvas(_content("fill"), (200, 100), (50, 50, 20, 10)) == (110.0, 170.0, 150.0, 200.0)
    # cover: 200x100 into 400x300 -> scale 3 (600x300), centre-cropped by 100 px on the left
    assert source_rect_to_canvas(_content("cover"), (200, 100), (50, 0, 10, 10)) == (60.0, 20.0, 90.0, 50.0)
    assert source_rect_to_canvas(_content("cover"), (200, 100), (0, 0, 20, 10)) is None   # cropped away
    # contain: scale 2 (400x200), placed at the element's position
    assert source_rect_to_canvas(_content("contain"), (200, 100), (50, 50, 20, 10)) == (110.0, 120.0, 150.0, 140.0)
    # none: native size, centred in the box
    assert source_rect_to_canvas(_content("none"), (200, 100), (0, 0, 10, 10)) == (110.0, 120.0, 120.0, 130.0)
    # crop first, then fit
    assert source_rect_to_canvas(_content("fill", Crop(100, 0, 100, 100)), (200, 100), (150, 50, 10, 10)) == (210.0, 170.0, 250.0, 200.0)
    with pytest.raises(ThumbnailError):
        source_rect_to_canvas(ImageContent("a", Position(0, 0), Size(10, 10), "fill", None, 1.0, 90), (10, 10), (0, 0, 1, 1))


def test_cli_choose_frame_and_place_text(media, workspace, capsys):
    req = workspace / "choose.json"
    req.write_text(json.dumps({"detections": blink_and_sharp()}), encoding="utf-8")
    assert cli_main(["choose-frame", str(req), "--workspace", str(workspace), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["chosen"]["t"] == 2.0
    req = workspace / "place.json"
    req.write_text(json.dumps({"document": frame_document(media), "text_element": "title", "image_element": "frame_el", "faces": [FACE],
                               "frame_size": {"width": 1280, "height": 720}}), encoding="utf-8")
    assert cli_main(["place-text", str(req), "--workspace", str(workspace), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["placement"]["chosen"] == "bottom-center"


def test_contract_declares_frame_choice_without_detecting():
    c = skill_contract()
    tools = {t["tool_id"]: t for t in c["tools"]}
    for tid in ("thumbnail/choose_frame", "thumbnail/place_text"):
        assert tools[tid]["produces_output"] is False and tools[tid]["deterministic"] is True and tools[tid]["writes_media"] is False
    assert c["frame_choice"]["detects"] is False and c["frame_choice"]["reads_pixels"] is False
    assert "face/object detection" in c["not_provided"]
    assert c["execution"]["ai"] is False

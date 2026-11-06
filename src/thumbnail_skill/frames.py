"""Frame choice and face-aware text placement (ECOSYSTEM_ROADMAP.md step 6, ADR-15).

This module detects nothing and measures no pixels. Faces, their boxes and their attributes come from
detection-skill (`detection faces --attributes` -> `detection-skill/detections@1`). Frame-level sharpness and
exposure come from the caller, and media-analysis-skill does not publish them yet (its ADR-029 lists them as a gap).
What it adds is arithmetic over those facts:

- `thumbnail/choose_frame` ranks the sampled frames of one detections document by a stated, weighted formula.
  Every part and every missing part is listed, and a frame whose main face has its eyes closed is ranked after
  every frame where they are open. It suggests; the caller decides.
- `thumbnail/place_text` maps the face boxes onto the canvas through the image element that shows the frame
  (same fit/crop arithmetic as the renderer). It then moves one text element to the first candidate position
  whose painted box (`renderer.text_box`) clears every face by a margin and stays on the canvas. If no candidate
  does, the request is refused; the text is never placed over a face."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .canonical import sha256_file
from .errors import ThumbnailError
from .fonts import resolve_font
from .model import H_ALIGN, V_ALIGN, ImageContent, ThumbnailDocument, _enum, _id, _number, _obj, _reject_forbidden, parse_document
from .renderer import text_box

DETECTIONS_SCHEMA = "detection-skill/detections@1"
# Part weights (sum 1). detection-skill's face-rank@1 uses eyes 0.35 / sharpness 0.25 / frontal 0.20 / size 0.20;
# exposure is added here and the other four are scaled down to make room for it.
WEIGHTS: Dict[str, float] = {"eyes_open": 0.30, "sharpness": 0.25, "exposure": 0.15, "frontal": 0.15, "size": 0.15}
SHARPNESS_HALF = 100.0   # sharpness / (sharpness + 100), as detection-skill's rank: a Laplacian variance of 100 scores 0.5
SIZE_FULL = 0.4          # min(1, face height / frame height / 0.4), as detection-skill's rank
EYES_CLOSED_BELOW = 0.5  # detection-skill's eyes_open is "about 0.8 open, 0.2 closed" (its ADR-007); halfway between
MAX_FRAMES = 2000
MAX_FACES = 64
DEFAULT_MARGIN = 16
MAX_CANDIDATES = 32


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _box(value: Any, where: str) -> Tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4 or not all(_num(v) for v in value) or value[2] <= 0 or value[3] <= 0:
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be [x, y, w, h] with positive w and h", {"field": where})
    return float(value[0]), float(value[1]), float(value[2]), float(value[3])


# ---------------------------------------------------------------- choose_frame
def _faces_document(doc: Any) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise ThumbnailError("INVALID_REQUEST", "detections must be a detection-skill/detections@1 document (an object)", {"field": "detections"})
    if doc.get("schema") != DETECTIONS_SCHEMA:
        raise ThumbnailError("INVALID_REQUEST", f"detections.schema must be {DETECTIONS_SCHEMA!r}, got {doc.get('schema')!r}", {"field": "detections.schema"})
    if doc.get("kind") != "face":
        raise ThumbnailError("INVALID_REQUEST", "detections must be face detections (detection faces --attributes)", {"field": "detections.kind"})
    fs = doc.get("frame_size")
    if not isinstance(fs, dict) or not all(isinstance(fs.get(k), int) and fs[k] > 0 for k in ("width", "height")):
        raise ThumbnailError("INVALID_REQUEST", "detections.frame_size must be {width, height} in pixels", {"field": "detections.frame_size"})
    frames = doc.get("frames")
    if not isinstance(frames, list) or not frames or len(frames) > MAX_FRAMES:
        raise ThumbnailError("INVALID_REQUEST", f"detections.frames must be a non-empty array of at most {MAX_FRAMES} frames", {"field": "detections.frames"})
    for i, fr in enumerate(frames):
        if not isinstance(fr, dict) or not isinstance(fr.get("detections"), list) or len(fr["detections"]) > MAX_FACES:
            raise ThumbnailError("INVALID_REQUEST", f"detections.frames[{i}] must carry a detections array (at most {MAX_FACES})", {"field": f"detections.frames[{i}]"})
        if fr.get("t") is not None and (not _num(fr["t"]) or fr["t"] < 0):
            raise ThumbnailError("INVALID_REQUEST", f"detections.frames[{i}].t must be a time in seconds or null", {"field": f"detections.frames[{i}].t"})
        for j, d in enumerate(fr["detections"]):
            if not isinstance(d, dict):
                raise ThumbnailError("INVALID_REQUEST", f"detections.frames[{i}].detections[{j}] must be an object", {"field": f"detections.frames[{i}].detections[{j}]"})
            _box(d.get("box"), f"detections.frames[{i}].detections[{j}].box")
            if d.get("attributes") is not None and not isinstance(d["attributes"], dict):
                raise ThumbnailError("INVALID_REQUEST", f"detections.frames[{i}].detections[{j}].attributes must be an object", {"field": f"detections.frames[{i}].detections[{j}].attributes"})
    if not any(isinstance(d.get("attributes"), dict) for fr in frames for d in fr["detections"]) and any(fr["detections"] for fr in frames):
        raise ThumbnailError("INVALID_REQUEST", "the faces carry no attributes: run detection faces --attributes", {"field": "detections", "reason": "no_attributes"})
    return doc


def _measures(value: Any) -> Dict[float, Dict[str, float]]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ThumbnailError("INVALID_REQUEST", "measures must be an array of {t, sharpness?, mean_luma?}", {"field": "measures"})
    out: Dict[float, Dict[str, float]] = {}
    for i, m in enumerate(value):
        d = _obj(m, f"measures[{i}]", ("t", "sharpness", "mean_luma"), ("t",))
        t = round(_number(d["t"], f"measures[{i}].t", 0), 3)
        out[t] = {}
        if "sharpness" in d:
            out[t]["sharpness"] = _number(d["sharpness"], f"measures[{i}].sharpness", 0)
        if "mean_luma" in d:
            out[t]["mean_luma"] = _number(d["mean_luma"], f"measures[{i}].mean_luma", 0, 1)
    return out


def _frame_parts(face: Dict[str, Any], frame_measures: Dict[str, float]) -> Tuple[Dict[str, Optional[float]], List[str], Dict[str, str]]:
    a = face.get("attributes") or {}
    sharp_src = "measures" if "sharpness" in frame_measures else ("face" if _num(a.get("sharpness")) else None)
    sharp = frame_measures.get("sharpness", a.get("sharpness") if _num(a.get("sharpness")) else None)
    luma = frame_measures.get("mean_luma")
    parts: Dict[str, Optional[float]] = {
        "eyes_open": min(1.0, max(0.0, float(a["eyes_open"]))) if _num(a.get("eyes_open")) else None,
        "sharpness": sharp / (sharp + SHARPNESS_HALF) if sharp is not None else None,
        "exposure": 1.0 - 2.0 * abs(luma - 0.5) if luma is not None else None,
        "frontal": min(1.0, max(0.0, float(a["frontal"]))) if _num(a.get("frontal")) else None,
        "size": min(1.0, float(a["size"]) / SIZE_FULL) if _num(a.get("size")) else None,
    }
    sources = {"eyes_open": "detection-skill", "frontal": "detection-skill", "size": "detection-skill",
               "sharpness": {"measures": "caller measures", "face": "detection-skill (face)"}.get(sharp_src or "", "none"),
               "exposure": "caller measures" if luma is not None else "none"}
    return parts, [k for k, v in parts.items() if v is None], sources


def choose_frame(params: Any, resolve_input: Callable[[str, str], Path]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ThumbnailError("INVALID_REQUEST", "params must be a JSON object")
    _reject_forbidden({k: v for k, v in params.items() if k != "detections"}, "params")   # the detections document is data, read by field
    d = _obj(params, "params", ("detections", "detections_path", "video", "measures", "weights", "eyes_closed_below", "options"), ())
    if ("detections" in d) == ("detections_path" in d):
        raise ThumbnailError("INVALID_REQUEST", "give exactly one of 'detections' (the document) or 'detections_path' (its file)", {"field": "detections"})
    if "detections_path" in d:
        path = resolve_input(d["detections_path"], "detections_path")
        import json
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as e:
            raise ThumbnailError("INVALID_INPUT", f"detections_path is not a readable JSON document: {e}", {"reason": "unreadable_detections"})
    else:
        raw = d["detections"]
    doc = _faces_document(raw)
    measures = _measures(d.get("measures"))
    weights = dict(WEIGHTS)
    if "weights" in d:
        w = _obj(d["weights"], "weights", tuple(WEIGHTS), ())
        weights.update({k: _number(v, f"weights.{k}", 0, 1) for k, v in w.items()})
        if sum(weights.values()) <= 0:
            raise ThumbnailError("INVALID_REQUEST", "weights must not all be 0", {"field": "weights"})
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}
    closed_below = _number(d.get("eyes_closed_below", EYES_CLOSED_BELOW), "eyes_closed_below", 0, 1)

    video = None
    if "video" in d:
        vpath = resolve_input(d["video"], "video")
        fp = f"sha256:{sha256_file(str(vpath))}"
        want = (doc.get("source") or {}).get("fingerprint")
        if want and want != fp:
            raise ThumbnailError("INVALID_INPUT", "video is not the file these detections were made from (fingerprint differs)",
                                 {"reason": "detections_source_mismatch", "expected": want, "got": fp})
        video = {"path": str(vpath), "fingerprint": fp}

    ranked: List[Dict[str, Any]] = []
    without_face: List[Dict[str, Any]] = []
    for pos, fr in enumerate(doc["frames"]):
        t = fr.get("t")
        boxes = [list(_box(f["box"], "box")) for f in fr["detections"]]
        entry: Dict[str, Any] = {"index": fr.get("index", pos), "t": t, "faces": boxes}
        if not fr["detections"]:
            without_face.append({**entry, "rank": None, "score": None, "parts": None, "missing": [], "eyes_closed": None, "main_face": None})
            continue
        # the main face is the largest box (the subject); a frame is judged by it
        main = max(range(len(boxes)), key=lambda j: (boxes[j][2] * boxes[j][3], -j))
        fm = measures.get(round(t, 3), {}) if t is not None else {}
        parts, missing, sources = _frame_parts(fr["detections"][main], fm)
        score = sum(weights[k] * (v or 0.0) for k, v in parts.items())
        eyes = parts["eyes_open"]
        ranked.append({**entry, "score": round(score, 4), "parts": {k: (round(v, 4) if v is not None else None) for k, v in parts.items()},
                       "missing": missing, "sources": sources, "eyes_closed": eyes is not None and eyes < closed_below, "main_face": main,
                       "main_face_box": boxes[main], "_order": pos})
    ranked.sort(key=lambda e: (e["eyes_closed"], -e["score"], e["_order"]))
    for n, e in enumerate(ranked, start=1):
        e["rank"] = n
        del e["_order"]
    frames = ranked + without_face
    chosen = ranked[0] if ranked else None
    body: Dict[str, Any] = {
        "ranking": frames, "chosen": chosen, "frame_size": dict(doc["frame_size"]),
        "method": {"weights": {k: round(v, 4) for k, v in weights.items()}, "sharpness": f"s / (s + {SHARPNESS_HALF:g}), s a Laplacian variance",
                   "exposure": "1 - 2 * |mean_luma - 0.5|", "size": f"min(1, face height / frame height / {SIZE_FULL})",
                   "eyes_closed_below": closed_below, "order": "eyes open before eyes closed, then score (high first), then input order",
                   "main_face": "the largest face box of the frame", "missing_part": "counts 0 and is listed in missing", "heuristic": True},
        "source": {"detections": DETECTIONS_SCHEMA, "fingerprint": (doc.get("source") or {}).get("fingerprint"),
                   "detection_skill_version": doc.get("skill_version"), "frames": len(doc["frames"])},
    }
    if chosen is not None and video is not None and chosen["t"] is not None:
        body["asset"] = {"kind": "video_frame", "path": video["path"], "timestamp": chosen["t"]}
        body["video"] = video
    if chosen is not None and all(e["eyes_closed"] for e in ranked):
        body["warnings"] = ["every ranked frame's main face has its eyes closed (eyes_open below eyes_closed_below)"]
    return body


# ---------------------------------------------------------------- place_text
def source_rect_to_canvas(content: ImageContent, source_size: Tuple[int, int], rect: Tuple[float, float, float, float]) -> Optional[Tuple[float, float, float, float]]:
    """A rectangle in the image asset's own pixels -> (x0, y0, x1, y1) on the canvas, through the element's crop, fit
    and position exactly as renderer.draw_image_element places the pixels. None when the rectangle is cropped away.
    Axis-aligned elements only (rotation 0)."""
    if content.rotation != 0:
        raise ThumbnailError("UNSUPPORTED_OPERATION", "face-aware placement needs an image element with rotation 0", {"field": "image.rotation"})
    sw, sh = source_size
    x0, y0, x1, y1 = rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]
    if content.crop is not None:
        c = content.crop
        x0, y0, x1, y1 = max(x0, c.x) - c.x, max(y0, c.y) - c.y, min(x1, c.x + c.width) - c.x, min(y1, c.y + c.height) - c.y
        sw, sh = c.width, c.height
        if x1 <= x0 or y1 <= y0:
            return None
    tw, th = content.size.width, content.size.height
    px, py = round(content.position.x), round(content.position.y)
    if content.fit == "fill":
        sx, sy, ox, oy, clip = tw / sw, th / sh, 0.0, 0.0, None
    elif content.fit == "none":
        sx = sy = 1.0
        ox, oy, clip = float(max(0, (tw - sw) // 2)), float(max(0, (th - sh) // 2)), None
    else:
        scale = max(tw / sw, th / sh) if content.fit == "cover" else min(tw / sw, th / sh)
        nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
        sx, sy = nw / sw, nh / sh
        if content.fit == "cover":
            left, top = max(0, (nw - tw) // 2), max(0, (nh - th) // 2)
            ox, oy, clip = -float(left), -float(top), (0.0, 0.0, float(tw), float(th))
        else:
            ox, oy, clip = 0.0, 0.0, None
    bx0, by0, bx1, by1 = x0 * sx + ox, y0 * sy + oy, x1 * sx + ox, y1 * sy + oy
    if clip is not None:
        bx0, by0, bx1, by1 = max(bx0, clip[0]), max(by0, clip[1]), min(bx1, clip[2]), min(by1, clip[3])
        if bx1 <= bx0 or by1 <= by0:
            return None
    return bx0 + px, by0 + py, bx1 + px, by1 + py


def default_candidates(width: int, height: int) -> List[Dict[str, Any]]:
    m = round(0.05 * min(width, height))
    spots = [("bottom-center", width / 2, height - m, "center", "bottom"), ("top-center", width / 2, m, "center", "top"),
             ("bottom-left", m, height - m, "left", "bottom"), ("bottom-right", width - m, height - m, "right", "bottom"),
             ("top-left", m, m, "left", "top"), ("top-right", width - m, m, "right", "top"),
             ("middle-left", m, height / 2, "left", "middle"), ("middle-right", width - m, height / 2, "right", "middle")]
    return [{"name": n, "position": {"x": x, "y": y}, "align": {"horizontal": h, "vertical": v}} for n, x, y, h, v in spots]


def _candidates(value: Any, width: int, height: int) -> List[Dict[str, Any]]:
    if value is None:
        return default_candidates(width, height)
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CANDIDATES:
        raise ThumbnailError("INVALID_REQUEST", f"candidates must be an array of 1..{MAX_CANDIDATES} positions", {"field": "candidates"})
    out = []
    for i, c in enumerate(value):
        d = _obj(c, f"candidates[{i}]", ("name", "position", "align"), ("position",))
        p = _obj(d["position"], f"candidates[{i}].position", ("x", "y"), ("x", "y"))
        a = _obj(d.get("align", {}), f"candidates[{i}].align", ("horizontal", "vertical"), ())
        out.append({"name": str(d.get("name", f"candidate-{i + 1}"))[:64],
                    "position": {"x": _number(p["x"], f"candidates[{i}].position.x", -100000, 100000), "y": _number(p["y"], f"candidates[{i}].position.y", -100000, 100000)},
                    "align": {"horizontal": _enum(a.get("horizontal", "left"), f"candidates[{i}].align.horizontal", H_ALIGN),
                              "vertical": _enum(a.get("vertical", "top"), f"candidates[{i}].align.vertical", V_ALIGN)}})
    return out


def _overlaps(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place_text(params: Any, open_size: Callable[[str], Tuple[int, int]]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ThumbnailError("INVALID_REQUEST", "params must be a JSON object")
    _reject_forbidden(params, "params")
    d = _obj(params, "params", ("document", "text_element", "image_element", "faces", "frame_size", "candidates", "margin", "options"),
             ("document", "text_element", "image_element", "faces"))
    document: ThumbnailDocument = parse_document(d["document"])
    text_id, image_id = _id(d["text_element"], "text_element"), _id(d["image_element"], "image_element")
    text_el = next((e for e in document.elements if e.element_id == text_id), None)
    image_el = next((e for e in document.elements if e.element_id == image_id), None)
    if text_el is None or text_el.text is None:
        raise ThumbnailError("INVALID_REQUEST", f"text_element {text_id!r} is not a text element of the document", {"field": "text_element"})
    if image_el is None or image_el.image is None:
        raise ThumbnailError("INVALID_REQUEST", f"image_element {image_id!r} is not an image element of the document", {"field": "image_element"})
    faces_raw = d["faces"]
    if not isinstance(faces_raw, list) or len(faces_raw) > MAX_FACES:
        raise ThumbnailError("INVALID_REQUEST", f"faces must be an array of at most {MAX_FACES} [x, y, w, h] boxes", {"field": "faces"})
    faces = [_box(f, f"faces[{i}]") for i, f in enumerate(faces_raw)]
    margin = int(_number(d.get("margin", DEFAULT_MARGIN), "margin", 0, 1000, integer=True))

    asset = document.asset(image_el.image.asset_id)
    frame_size = None
    if "frame_size" in d:
        fs = _obj(d["frame_size"], "frame_size", ("width", "height"), ("width", "height"))
        frame_size = (int(_number(fs["width"], "frame_size.width", 1, 100000, integer=True)), int(_number(fs["height"], "frame_size.height", 1, 100000, integer=True)))
    if asset.kind == "image":
        source_size = open_size(asset.path)
    elif frame_size is not None:
        source_size = frame_size   # ffmpeg-skill/look decodes the frame at the source's display size, detection-skill's frame_size
    else:
        raise ThumbnailError("INVALID_REQUEST", "frame_size is required when the image element shows a video_frame asset (the pixel space of the face boxes)",
                             {"field": "frame_size"})
    fs = frame_size or source_size
    scale_x, scale_y = source_size[0] / fs[0], source_size[1] / fs[1]
    face_canvas = []
    for f in faces:
        r = source_rect_to_canvas(image_el.image, source_size, (f[0] * scale_x, f[1] * scale_y, f[2] * scale_x, f[3] * scale_y))
        if r is not None:
            face_canvas.append((r[0] - margin, r[1] - margin, r[2] + margin, r[3] + margin))

    W, H = document.canvas.width, document.canvas.height
    font = resolve_font(text_el.text.font_id)
    content = text_el.text
    tried = []
    chosen = None
    for c in _candidates(d.get("candidates"), W, H):
        content.position.x, content.position.y = c["position"]["x"], c["position"]["y"]
        content.align.horizontal, content.align.vertical = c["align"]["horizontal"], c["align"]["vertical"]
        box = text_box(content, font)
        inside = box[0] >= 0 and box[1] >= 0 and box[2] <= W and box[3] <= H
        hits = [i for i, fb in enumerate(face_canvas) if _overlaps(box, fb)]
        ok = inside and not hits
        tried.append({"name": c["name"], "text_box": list(box), "inside_canvas": inside, "overlaps_faces": hits, "clear": ok})
        if ok:
            chosen = c
            break
    if chosen is None:
        raise ThumbnailError("VALIDATION_ERROR", f"no candidate position keeps text element {text_id!r} on the canvas and clear of every face",
                             {"reason": "no_position_clear_of_faces", "tried": tried, "face_boxes": [list(map(_r2, f)) for f in face_canvas]})
    return {"document": document.to_dict(),
            "placement": {"text_element": text_id, "image_element": image_id, "chosen": chosen["name"], "position": chosen["position"], "align": chosen["align"],
                          "text_box": tried[-1]["text_box"], "face_boxes": [list(map(_r2, f)) for f in face_canvas], "margin": margin, "tried": tried,
                          "rule": "the first candidate, in order, whose painted text box (layout, background, ink with stroke and shadow) is on the canvas "
                                  "and overlaps no face box grown by margin"}}


def _r2(v: float) -> float:
    return round(v, 2)


__all__ = ["choose_frame", "place_text", "source_rect_to_canvas", "default_candidates", "WEIGHTS", "DETECTIONS_SCHEMA", "EYES_CLOSED_BELOW"]

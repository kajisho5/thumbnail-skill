"""`thumbnail/render_variants` (`thumbnail render --variants N`): N documented variants of one thumbnail for a
platform A/B test, each checked at the size it is seen on a phone (ECOSYSTEM_ROADMAP.md step 7, ADR-16).

Variant k (1-based) takes entry (k-1) mod n of each axis: the text element's layout (position, alignment,
optionally font size), its palette (text colour, background band, stroke), and the frame shown by one image
element. Entry 0 of every axis is the document as given, so variant 1 is always the original. Every choice is
written into the response, so the variants are documented rather than opaque. Each variant is an ordinary
`thumbnail/render`.

The small-size check measures, for every text element of a written variant:
- text height at `phone_width` px wide: the tallest run of pixel rows containing the element's own glyph ink
  (fill only; alpha >= 128), drawn alone with the renderer's layout, times phone_width / canvas width. This is the
  quantity qc-skill's text legibility calls ink height (its ADR-018). Measured here because the element's own
  ink is known exactly, without separating it from the picture behind it.
- contrast ratio: qc-skill's `video.text_regions` on the written file, at the element's painted box (WCAG 2.x).
A variant below either minimum is reported (`small_size.ok: false`, its findings, `flagged`), never dropped and
never re-laid out."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .errors import ThumbnailError
from .fonts import ResolvedFont
from .frames import default_candidates
from .model import H_ALIGN, V_ALIGN, TextContent, ThumbnailDocument, _color, _enum, _id, _number, _obj
from .renderer import _layout, load_font, text_box

MAX_VARIANTS = 12
MAX_AXIS = 12
PHONE_WIDTH = 320          # the roadmap's phone width
MIN_TEXT_HEIGHT_PX = 12    # this skill's default floor at PHONE_WIDTH; a heuristic, not a platform figure (ADR-16)
MIN_CONTRAST_RATIO = 4.5   # WCAG 2.x AA (SC 1.4.3), qc-skill's default
INK_ALPHA = 128

DEFAULT_PALETTES: List[Dict[str, Any]] = [
    {"name": "white-on-dark-band", "color": "#FFFFFF", "background": {"color": "#000000CC", "padding": 16}, "stroke": None},
    {"name": "black-on-yellow-band", "color": "#111111", "background": {"color": "#FFD400", "padding": 16}, "stroke": None},
    {"name": "white-with-outline", "color": "#FFFFFF", "background": None, "stroke": {"color": "#000000", "width": 4}},
]


def _layout_entry(value: Any, where: str) -> Dict[str, Any]:
    d = _obj(value, where, ("name", "position", "align", "font_size"), ("name", "position"))
    p = _obj(d["position"], f"{where}.position", ("x", "y"), ("x", "y"))
    a = _obj(d.get("align", {}), f"{where}.align", ("horizontal", "vertical"), ())
    out = {"name": _id(d["name"], f"{where}.name"), "position": {"x": _number(p["x"], f"{where}.position.x", -100000, 100000), "y": _number(p["y"], f"{where}.position.y", -100000, 100000)},
           "align": {"horizontal": _enum(a.get("horizontal", "left"), f"{where}.align.horizontal", H_ALIGN), "vertical": _enum(a.get("vertical", "top"), f"{where}.align.vertical", V_ALIGN)}}
    if "font_size" in d:
        out["font_size"] = int(_number(d["font_size"], f"{where}.font_size", 6, 400, integer=True))
    return out


def _palette_entry(value: Any, where: str) -> Dict[str, Any]:
    d = _obj(value, where, ("name", "color", "background", "stroke"), ("name", "color"))
    bg = st = None
    if d.get("background") is not None:
        b = _obj(d["background"], f"{where}.background", ("color", "padding"), ("color",))
        bg = {"color": _color(b["color"], f"{where}.background.color"), "padding": int(_number(b.get("padding", 0), f"{where}.background.padding", 0, 200, integer=True))}
    if d.get("stroke") is not None:
        s = _obj(d["stroke"], f"{where}.stroke", ("color", "width"), ("color",))
        st = {"color": _color(s["color"], f"{where}.stroke.color"), "width": int(_number(s.get("width", 1), f"{where}.stroke.width", 0, 40, integer=True))}
    return {"name": _id(d["name"], f"{where}.name"), "color": _color(d["color"], f"{where}.color"), "background": bg, "stroke": st}


def _frame_entry(value: Any, where: str) -> Dict[str, Any]:
    d = _obj(value, where, ("name", "asset"), ("name", "asset"))
    a = _obj(d["asset"], f"{where}.asset", ("kind", "path", "timestamp"), ("kind", "path"))
    return {"name": _id(d["name"], f"{where}.name"), "asset": dict(a)}


def _axis(value: Any, where: str, parse: Any, default: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if value is None:
        return default
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_AXIS:
        raise ThumbnailError("INVALID_REQUEST", f"{where} must be an array of 1..{MAX_AXIS} entries", {"field": where})
    out = [parse(v, f"{where}[{i}]") for i, v in enumerate(value)]
    if len({e["name"] for e in out}) != len(out):
        raise ThumbnailError("INVALID_REQUEST", f"{where}: names must be unique", {"field": where})
    return out


def parse_variants(value: Any, document: ThumbnailDocument, count: Optional[int]) -> Dict[str, Any]:
    d = _obj(value if value is not None else {}, "variants",
             ("count", "text_element", "layouts", "palettes", "frame_element", "frames", "phone_width", "min_text_height_px", "min_contrast_ratio", "qc_skill"), ())
    n = count if count is not None else d.get("count")
    if n is None:
        raise ThumbnailError("INVALID_REQUEST", "variants.count (or --variants N) is required", {"field": "variants.count"})
    n = int(_number(n, "variants.count", 1, MAX_VARIANTS, integer=True))
    texts = [e for e in document.elements if e.text is not None]
    if not texts:
        raise ThumbnailError("INVALID_REQUEST", "the document has no text element to vary", {"field": "document.elements"})
    text_id = _id(d["text_element"], "variants.text_element") if "text_element" in d else texts[0].element_id
    text_el = next((e for e in texts if e.element_id == text_id), None)
    if text_el is None:
        raise ThumbnailError("INVALID_REQUEST", f"variants.text_element {text_id!r} is not a text element of the document", {"field": "variants.text_element"})
    W, H = document.canvas.width, document.canvas.height
    c = text_el.text
    assert c is not None
    as_given_layout = {"name": "as-given", "position": c.position.to_dict(), "align": c.align.to_dict()}
    as_given_palette = {"name": "as-given", "color": c.color, "background": c.background.to_dict() if c.background else None,
                        "stroke": c.stroke.to_dict() if c.stroke else None}
    layouts = [as_given_layout] + _axis(d.get("layouts"), "variants.layouts", _layout_entry, default_candidates(W, H))
    palettes = [as_given_palette] + _axis(d.get("palettes"), "variants.palettes", _palette_entry, [dict(p) for p in DEFAULT_PALETTES])
    frames: List[Dict[str, Any]] = [{"name": "as-given", "asset": None}]
    frame_id = None
    if "frames" in d:
        if "frame_element" not in d:
            raise ThumbnailError("INVALID_REQUEST", "variants.frames needs variants.frame_element (the image element whose asset changes)", {"field": "variants.frame_element"})
        frame_id = _id(d["frame_element"], "variants.frame_element")
        fe = next((e for e in document.elements if e.element_id == frame_id and e.image is not None), None)
        if fe is None:
            raise ThumbnailError("INVALID_REQUEST", f"variants.frame_element {frame_id!r} is not an image element of the document", {"field": "variants.frame_element"})
        frames += _axis(d["frames"], "variants.frames", _frame_entry, [])
    for axis in (layouts, palettes, frames):
        names = [e["name"] for e in axis]
        if len(set(names)) != len(names):
            raise ThumbnailError("INVALID_REQUEST", "'as-given' is reserved for the document's own choice", {"field": "variants"})
    qc_dir = d.get("qc_skill")
    if qc_dir is not None and (not isinstance(qc_dir, str) or not qc_dir):
        raise ThumbnailError("INVALID_REQUEST", "variants.qc_skill must be a directory path", {"field": "variants.qc_skill"})
    return {"count": n, "text_element": text_id, "frame_element": frame_id, "layouts": layouts, "palettes": palettes, "frames": frames,
            "phone_width": int(_number(d.get("phone_width", PHONE_WIDTH), "variants.phone_width", 100, 4000, integer=True)),
            "min_text_height_px": _number(d.get("min_text_height_px", MIN_TEXT_HEIGHT_PX), "variants.min_text_height_px", 0, 400),
            "min_contrast_ratio": _number(d.get("min_contrast_ratio", MIN_CONTRAST_RATIO), "variants.min_contrast_ratio", 1, 21), "qc_skill": qc_dir}


def build_variant(raw_document: Dict[str, Any], spec: Dict[str, Any], k: int) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Variant k (1-based) of the raw document, and the names of the choices it took."""
    layout = spec["layouts"][(k - 1) % len(spec["layouts"])]
    palette = spec["palettes"][(k - 1) % len(spec["palettes"])]
    frame = spec["frames"][(k - 1) % len(spec["frames"])]
    doc = copy.deepcopy(raw_document)
    for e in doc["elements"]:
        if e.get("element_id") == spec["text_element"]:
            t = e["text"]
            if layout["name"] != "as-given":
                t["position"], t["align"] = dict(layout["position"]), dict(layout["align"])
                if "font_size" in layout:
                    t["font_size"] = layout["font_size"]
            if palette["name"] != "as-given":
                t["color"] = palette["color"]
                for key in ("background", "stroke"):
                    if palette[key] is None:
                        t.pop(key, None)
                    else:
                        t[key] = dict(palette[key])
        if frame["asset"] is not None and e.get("element_id") == spec["frame_element"]:
            new_id = f"variant-frame-{k}"
            doc["assets"] = list(doc.get("assets") or []) + [{"asset_id": new_id, **frame["asset"]}]
            e["image"]["asset_id"] = new_id
    choices = {"layout": layout["name"], "palette": palette["name"], "frame": frame["name"]}
    return doc, choices


def ink_height(content: TextContent, font_file: ResolvedFont, canvas: Tuple[int, int]) -> int:
    """Tallest run of rows containing the element's own glyph ink (fill only, alpha >= 128), drawn alone on a
    transparent canvas with the renderer's layout."""
    bare = copy.deepcopy(content)
    bare.background = bare.stroke = bare.shadow = None
    bare.opacity = 1.0
    bare.color = "#FFFFFF"
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = load_font(bare, font_file)
    lines, _, _, _, _, _, _, origins = _layout(draw, bare, font)
    for ln, (lx, ly) in zip(lines, origins):
        draw.text((lx, ly), ln, font=font, fill=(255, 255, 255, 255))
    alpha = layer.getchannel("A").point(lambda a: 255 if a >= INK_ALPHA else 0)
    bbox = alpha.getbbox()
    if bbox is None:
        return 0
    x0, y0, x1, y1 = bbox
    best = run = 0
    for y in range(y0, y1):
        if alpha.crop((x0, y, x1, y + 1)).getbbox() is not None:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def clipped_box(content: TextContent, font_file: ResolvedFont, canvas: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    """The element's painted box clipped to the canvas, as [x, y, w, h]; None when it is off the canvas."""
    x0, y0, x1, y1 = text_box(content, font_file)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(canvas[0], x1), min(canvas[1], y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1 - x0, y1 - y0


__all__ = ["parse_variants", "build_variant", "ink_height", "clipped_box", "PHONE_WIDTH", "MIN_TEXT_HEIGHT_PX", "MIN_CONTRAST_RATIO", "MAX_VARIANTS",
           "DEFAULT_PALETTES"]

"""Compositing engine: canvas + resolved image/text elements -> one Pillow Image.

This is where the actual picture is drawn, and it never touches ffmpeg: it is a bounded, typed
raster pipeline over Pillow (canvas fill, paste, crop, resize, ImageDraw text). There is no filter
string, no shell, no arbitrary code path; every operation here corresponds to exactly one validated
field of a ThumbnailElement (model.py). Elements are drawn in z_index order (ties broken by their
position in the document, stable sort) so the caller's stacking is the only stacking that happens."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .errors import ThumbnailError
from .fonts import ResolvedFont
from .model import Crop, ImageContent, TextContent, ThumbnailDocument, ThumbnailElement


def _hex_color(value: str) -> tuple:
    v = value.lstrip("#")
    if len(v) == 6:
        r, g, b = (int(v[i:i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, 255)
    r, g, b, a = (int(v[i:i + 2], 16) for i in (0, 2, 4, 6))
    return (r, g, b, a)


def _apply_opacity(img: "Image.Image", opacity: float) -> "Image.Image":
    if opacity >= 1.0:
        return img
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    a = a.point(lambda p: int(p * opacity))
    return Image.merge("RGBA", (r, g, b, a))


def _apply_crop(img: "Image.Image", crop: Crop, where: str) -> "Image.Image":
    w, h = img.size
    if crop.x + crop.width > w or crop.y + crop.height > h:
        raise ThumbnailError("INVALID_REQUEST", f"{where}.crop [{crop.x},{crop.y},{crop.width}x{crop.height}] exceeds the source image size ({w}x{h})",
                             {"field": f"{where}.crop", "reason": "crop_out_of_bounds", "image_size": {"width": w, "height": h}})
    return img.crop((crop.x, crop.y, crop.x + crop.width, crop.y + crop.height))


def _apply_fit(img: "Image.Image", target_w: int, target_h: int, fit: str) -> "Image.Image":
    """Resize `img` for placement in a target_w x target_h box.
    - fill:    stretch to exactly the box (aspect not preserved)
    - cover:   scale to fully cover the box, cropping the overflow, centred
    - contain: scale to fit entirely inside the box, no cropping (result may be smaller than the box)
    - none:    no scaling; the source is placed at its native size (cropped by the box on paste)
    """
    sw, sh = img.size
    if fit == "fill":
        return img.resize((target_w, target_h), Image.LANCZOS)
    if fit == "none":
        return img
    scale_cover = max(target_w / sw, target_h / sh)
    scale_contain = min(target_w / sw, target_h / sh)
    scale = scale_cover if fit == "cover" else scale_contain
    new_w, new_h = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    if fit == "contain":
        return resized
    # cover: centre-crop the overflow so the result is exactly target_w x target_h
    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _rotate(img: "Image.Image", degrees: int) -> "Image.Image":
    if degrees == 0:
        return img
    return img.rotate(-degrees, expand=True)   # PIL rotates counter-clockwise; this skill's rotation is clockwise


class LayerRecorder:
    """Collects every composite the renderer makes, in order, as (image, dest, meta): exactly the operations that
    built the canvas, so replaying them over the background reproduces it pixel for pixel (ADR-17)."""

    def __init__(self) -> None:
        self.ops: List[Tuple["Image.Image", Tuple[int, int], Dict[str, Any]]] = []
        self.meta: Dict[str, Any] = {}
        self.base: Optional["Image.Image"] = None   # the canvas fill, before any element

    def composite(self, canvas: "Image.Image", src: "Image.Image", dest: Tuple[int, int], role: str) -> None:
        canvas.alpha_composite(src, dest=dest)
        self.ops.append((src, dest, {**self.meta, "role": role}))


def _composite(canvas: "Image.Image", src: "Image.Image", dest: Tuple[int, int], recorder: Optional[LayerRecorder], role: str) -> None:
    if recorder is None:
        canvas.alpha_composite(src, dest=dest)
    else:
        recorder.composite(canvas, src, dest, role)


def draw_image_element(canvas: "Image.Image", content: ImageContent, source: "Image.Image", where: str, recorder: Optional[LayerRecorder] = None) -> None:
    img = source.convert("RGBA")
    if content.crop is not None:
        img = _apply_crop(img, content.crop, where)
    img = _apply_fit(img, content.size.width, content.size.height, content.fit)
    img = _rotate(img, content.rotation)
    img = _apply_opacity(img, content.opacity)
    x, y = round(content.position.x), round(content.position.y)
    if content.fit == "none":
        # centre the native-size source within the declared box, matching contain/cover's centring
        x += max(0, (content.size.width - img.width) // 2)
        y += max(0, (content.size.height - img.height) // 2)
    _composite(canvas, img, (x, y), recorder, "image")


def _measure_lines(draw: "ImageDraw.ImageDraw", lines: List[str], font: "ImageFont.FreeTypeFont", line_spacing: float) -> Any:
    ascent, descent = font.getmetrics()
    line_h = int(round((ascent + descent) * line_spacing))
    widths = []
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln if ln else " ", font=font)
        widths.append(bbox[2] - bbox[0])
    return widths, line_h, ascent, descent


def load_font(content: TextContent, font_file: ResolvedFont) -> "ImageFont.FreeTypeFont":
    return ImageFont.truetype(font_file.path, content.font_size, index=font_file.index)


def _layout(draw: "ImageDraw.ImageDraw", content: TextContent, font: "ImageFont.FreeTypeFont") -> Tuple[List[str], List[int], int, float, float, int, int, List[Tuple[float, float]]]:
    """Where every line of a text element goes: the one layout both drawing and measuring use, so a
    measured box is exactly the box that is drawn."""
    lines = content.text.split("\n")
    widths, line_h, _, _ = _measure_lines(draw, lines, font, content.line_spacing)
    block_w = max(widths) if widths else 0
    block_h = line_h * len(lines)
    ax, ay = content.position.x, content.position.y
    if content.align.horizontal == "center":
        ax -= block_w / 2
    elif content.align.horizontal == "right":
        ax -= block_w
    if content.align.vertical == "middle":
        ay -= block_h / 2
    elif content.align.vertical == "bottom":
        ay -= block_h
    origins: List[Tuple[float, float]] = []
    for i in range(len(lines)):
        lx = ax
        if content.align.horizontal == "center":
            lx = ax + (block_w - widths[i]) / 2
        elif content.align.horizontal == "right":
            lx = ax + (block_w - widths[i])
        origins.append((lx, ay + i * line_h))
    return lines, widths, line_h, ax, ay, block_w, block_h, origins


def text_box(content: TextContent, font_file: ResolvedFont) -> Tuple[int, int, int, int]:
    """The canvas rectangle (x0, y0, x1, y1; x1/y1 exclusive) a text element can paint: the union of its
    layout block, its background box, and every glyph's ink including stroke and shadow, rounded
    outward to whole pixels. Measured with the same layout and font as `draw_text_element`, so nothing
    the element draws falls outside it."""
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = load_font(content, font_file)
    lines, _, _, ax, ay, block_w, block_h, origins = _layout(draw, content, font)
    boxes = [(ax, ay, ax + block_w, ay + block_h)]
    if content.background is not None:
        pad = content.background.padding
        boxes.append((round(ax - pad), round(ay - pad), round(ax + block_w + pad) + 1, round(ay + block_h + pad) + 1))
    stroke = content.stroke.width if content.stroke is not None else 0
    for ln, (lx, ly) in zip(lines, origins):
        if not ln:
            continue
        boxes.append(draw.textbbox((lx, ly), ln, font=font, stroke_width=stroke))
        if content.shadow is not None:
            boxes.append(draw.textbbox((lx + content.shadow.offset_x, ly + content.shadow.offset_y), ln, font=font))
    return (math.floor(min(b[0] for b in boxes)), math.floor(min(b[1] for b in boxes)),
            math.ceil(max(b[2] for b in boxes)), math.ceil(max(b[3] for b in boxes)))


def draw_text_element(canvas: "Image.Image", content: TextContent, font_file: ResolvedFont, recorder: Optional[LayerRecorder] = None) -> None:
    draw = ImageDraw.Draw(canvas)
    font = load_font(content, font_file)
    lines, _, _, ax, ay, block_w, block_h, origins = _layout(draw, content, font)

    if content.background is not None:
        pad = content.background.padding
        box = (round(ax - pad), round(ay - pad), round(ax + block_w + pad), round(ay + block_h + pad))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(box, fill=_hex_color(content.background.color))
        _composite(canvas, _apply_opacity(overlay, content.opacity), (0, 0), recorder, "text_background")

    text_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(text_layer)
    fill = _hex_color(content.color)
    for ln, (lx, ly) in zip(lines, origins):
        if content.shadow is not None:
            tdraw.text((lx + content.shadow.offset_x, ly + content.shadow.offset_y), ln, font=font, fill=_hex_color(content.shadow.color))
        if content.stroke is not None and content.stroke.width > 0:
            tdraw.text((lx, ly), ln, font=font, fill=fill, stroke_width=content.stroke.width, stroke_fill=_hex_color(content.stroke.color))
        else:
            tdraw.text((lx, ly), ln, font=font, fill=fill)
    _composite(canvas, _apply_opacity(text_layer, content.opacity), (0, 0), recorder, "text")


def render_document(document: ThumbnailDocument, images: Dict[str, "Image.Image"], fonts_used: Dict[str, ResolvedFont],
                    recorder: Optional[LayerRecorder] = None) -> "Image.Image":
    """`images` maps asset_id -> an already-opened, already-validated Pillow Image (the still image
    itself, or the decoded video frame). `fonts_used` maps font_id -> ResolvedFont. Pure function:
    no file I/O, no ffmpeg, no randomness; the same inputs always draw the same pixels."""
    canvas = Image.new("RGBA", (document.canvas.width, document.canvas.height), _hex_color(document.canvas.background))
    if recorder is not None:
        recorder.base = canvas.copy()
    ordered: List[ThumbnailElement] = sorted(enumerate(document.elements), key=lambda pair: (pair[1].z_index, pair[0]))
    for _, element in ordered:
        if recorder is not None:
            recorder.meta = {"element_id": element.element_id, "type": element.type, "z_index": element.z_index}
        if element.type == "image":
            content = element.image
            assert content is not None
            source = images[content.asset_id]
            draw_image_element(canvas, content, source, f"elements[{element.element_id}].image", recorder)
        else:
            content = element.text
            assert content is not None
            draw_text_element(canvas, content, fonts_used[content.font_id], recorder)
    return canvas


__all__ = ["LayerRecorder", "render_document", "draw_image_element", "draw_text_element", "text_box", "load_font"]

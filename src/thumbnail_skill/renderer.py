"""Compositing engine: canvas + resolved image/text elements -> one Pillow Image.

This is where the actual picture is drawn, and it never touches ffmpeg: it is a bounded, typed
raster pipeline over Pillow (canvas fill, paste, crop, resize, ImageDraw text). There is no filter
string, no shell, no arbitrary code path; every operation here corresponds to exactly one validated
field of a ThumbnailElement (model.py). Elements are drawn in z_index order (ties broken by their
position in the document, stable sort) so the caller's stacking is the only stacking that happens."""
from __future__ import annotations

from typing import Any, Dict, List

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


def draw_image_element(canvas: "Image.Image", content: ImageContent, source: "Image.Image", where: str) -> None:
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
    canvas.alpha_composite(img, dest=(x, y))


def _measure_lines(draw: "ImageDraw.ImageDraw", lines: List[str], font: "ImageFont.FreeTypeFont", line_spacing: float) -> Any:
    ascent, descent = font.getmetrics()
    line_h = int(round((ascent + descent) * line_spacing))
    widths = []
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln if ln else " ", font=font)
        widths.append(bbox[2] - bbox[0])
    return widths, line_h, ascent, descent


def draw_text_element(canvas: "Image.Image", content: TextContent, font_file: ResolvedFont) -> None:
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(font_file.path, content.font_size, index=font_file.index)
    lines = content.text.split("\n")
    widths, line_h, ascent, descent = _measure_lines(draw, lines, font, content.line_spacing)
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

    if content.background is not None:
        pad = content.background.padding
        box = (round(ax - pad), round(ay - pad), round(ax + block_w + pad), round(ay + block_h + pad))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(box, fill=_hex_color(content.background.color))
        canvas.alpha_composite(_apply_opacity(overlay, content.opacity))
        draw = ImageDraw.Draw(canvas)

    text_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(text_layer)
    fill = _hex_color(content.color)
    for i, ln in enumerate(lines):
        lx = ax
        if content.align.horizontal == "center":
            lx = ax + (block_w - widths[i]) / 2
        elif content.align.horizontal == "right":
            lx = ax + (block_w - widths[i])
        ly = ay + i * line_h
        if content.shadow is not None:
            tdraw.text((lx + content.shadow.offset_x, ly + content.shadow.offset_y), ln, font=font, fill=_hex_color(content.shadow.color))
        if content.stroke is not None and content.stroke.width > 0:
            tdraw.text((lx, ly), ln, font=font, fill=fill, stroke_width=content.stroke.width, stroke_fill=_hex_color(content.stroke.color))
        else:
            tdraw.text((lx, ly), ln, font=font, fill=fill)
    canvas.alpha_composite(_apply_opacity(text_layer, content.opacity))


def render_document(document: ThumbnailDocument, images: Dict[str, "Image.Image"], fonts_used: Dict[str, ResolvedFont]) -> "Image.Image":
    """`images` maps asset_id -> an already-opened, already-validated Pillow Image (the still image
    itself, or the decoded video frame). `fonts_used` maps font_id -> ResolvedFont. Pure function:
    no file I/O, no ffmpeg, no randomness; the same inputs always draw the same pixels."""
    canvas = Image.new("RGBA", (document.canvas.width, document.canvas.height), _hex_color(document.canvas.background))
    ordered: List[ThumbnailElement] = sorted(enumerate(document.elements), key=lambda pair: (pair[1].z_index, pair[0]))
    for _, element in ordered:
        if element.type == "image":
            content = element.image
            assert content is not None
            source = images[content.asset_id]
            draw_image_element(canvas, content, source, f"elements[{element.element_id}].image")
        else:
            content = element.text
            assert content is not None
            draw_text_element(canvas, content, fonts_used[content.font_id])
    return canvas


__all__ = ["render_document", "draw_image_element", "draw_text_element"]

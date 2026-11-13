"""`options.kit` (ADR-18, roadmap step 9): a Velisa `kit@1` brand kit mapped onto the ThumbnailDocument's existing colours,
font and logo. A kit fixture renders a thumbnail whose sampled text and band colours and reported font match the kit; the
same document with an explicit colour keeps that colour; three variants with a kit use only the kit's colours."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from thumbnail_skill.cli import main as cli_main
from thumbnail_skill.contract import skill_contract
from thumbnail_skill.fonts import resolve_font
from thumbnail_skill.kit import font_for_family
from thumbnail_skill.model import parse_document
from thumbnail_skill.renderer import text_box
from thumbnail_skill.security import PathPolicy
from thumbnail_skill.skill import run_tool

FIXTURE = Path(__file__).parent / "fixtures" / "kit_brand.json"
UNMAPPED_IN_RENDER = ["colors.accent", "colors.primary", "colors.secondary", "caption", "contrast", "font_file", "fonts", "lang", "logo_variants",
                      "loudness", "name", "styles"]


def local_family() -> str:
    """The family of the file `sans` resolves to here: DejaVu Sans on Linux, Arial on macOS and Windows."""
    from PIL import ImageFont
    rf = resolve_font("sans")
    return ImageFont.truetype(rf.path, 12, index=rf.index).getname()[0]


def kit_file(directory: Path, media, **changes) -> Path:
    """The fixture next to a logo, with its font set to this machine's sans family unless the change says otherwise."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["font"] = local_family()
    data.update(changes)
    data = {k: v for k, v in data.items() if v is not ...}
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(media["logo"], directory / "logo.png")
    path = directory / "kit.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def doc(title_color=None, band=True, stroke=False, font_id=None, background=None):
    text = {"text": "HH", "font_size": 200, "position": {"x": 120, "y": 400}}
    if title_color:
        text["color"] = title_color
    if font_id:
        text["font_id"] = font_id
    if band:
        text["background"] = {"padding": 24}
    if stroke:
        text["stroke"] = {"width": 3}
    canvas = {} if background is None else {"background": background}
    return {"document_id": "kit", "canvas": canvas, "elements": [{"element_id": "title", "type": "text", "text": text}]}


def render(params, workspace, tool="thumbnail/render"):
    return run_tool(tool, params, PathPolicy(str(workspace)))


def colour_counts(img) -> Counter:
    return Counter({c: n for n, c in img.getcolors(img.width * img.height)})


def top_colours(path: Path, box, n=2):
    with Image.open(path) as im:
        region = im.convert("RGB").crop(box)
        counts = colour_counts(region)
    return ["#%02X%02X%02X" % c for c, _ in counts.most_common(n)]


def title_box(res_document):
    d = parse_document(res_document)
    t = d.elements[0].text
    return text_box(t, resolve_font(t.font_id))


def test_kit_colours_and_font_are_the_thumbnails(media, workspace):
    kit = kit_file(workspace / "brand", media)
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}}, workspace)
    assert res["ok"] is True, res
    k = res["kit"]
    assert k["schema"] == "velisa/kit@1" and k["read"] is True and k["name"] == "harbour"
    # the reported font is the kit's: the registered font_id whose file has the kit's family
    assert k["font"]["kit"] == local_family() and k["font"]["family"] == local_family() and k["font"]["font_id"] == "sans"
    applied = {a["target"]: a for a in k["applied"]}
    assert applied["document.elements[0].text.color"] == {"target": "document.elements[0].text.color", "value": "#F4F1E8", "from": "colors.text"}
    assert applied["document.elements[0].text.background.color"]["value"] == "#0B1D2A"
    assert applied["document.canvas.background"]["value"] == "#0B1D2A"
    assert applied["document.elements[0].text.font_id"]["value"] == "sans"
    assert k["kept"] == []
    # sampled from the written file: the band and the text are the kit's colours
    filled = {"document_id": "kit", "canvas": {"width": 1280, "height": 720, "background": "#0B1D2A"},
              "elements": [{"element_id": "title", "type": "text", "text": {"text": "HH", "font_size": 200, "position": {"x": 120, "y": 400},
                                                                             "color": "#F4F1E8", "font_id": "sans", "background": {"color": "#0B1D2A", "padding": 24}}}]}
    assert sorted(top_colours(workspace / "thumb.png", title_box(filled))) == sorted(["#0B1D2A", "#F4F1E8"])
    # the logo: top-right, the kit's width and margin, at its opacity
    assert k["logo"]["box"] == {"x": 1280 - 40 - 120, "y": 40, "width": 120, "height": 120} and k["logo"]["opacity"] == 0.9
    with Image.open(workspace / "thumb.png") as im:
        px = im.convert("RGB").getpixel((1280 - 40 - 60, 100))
    assert px == tuple(round(0.9 * a + 0.1 * b) for a, b in zip((220, 30, 30), (0x0B, 0x1D, 0x2A)))
    # every kit key with no counterpart is reported with its reason, and warned about
    assert sorted(u["key"] for u in k["unmapped"]) == sorted(UNMAPPED_IN_RENDER)
    assert all(u["reason"] for u in k["unmapped"])
    assert sorted(u["key"] for u in k["unused"]) == ["colors.outline"]   # the title gives no stroke
    assert sum(1 for w in res["warnings"] if w.startswith("kit: ")) == len(UNMAPPED_IN_RENDER)
    assert res["provenance"]["kit"]["sha256"] == k["file"]["sha256"]


def test_an_explicit_colour_in_the_document_wins(media, workspace):
    kit = kit_file(workspace / "brand", media)
    res = render({"document": doc(title_color="#00FF00", stroke=True, font_id="sans-bold", background="#202020"),
                  "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}}, workspace)
    assert res["ok"] is True, res
    kept = {x["target"]: x for x in res["kit"]["kept"]}
    assert kept["document.elements[0].text.color"] == {"target": "document.elements[0].text.color", "value": "#00FF00", "kit_key": "colors.text", "kit_value": "#F4F1E8"}
    assert kept["document.elements[0].text.font_id"]["value"] == "sans-bold"
    assert kept["document.canvas.background"]["value"] == "#202020"
    applied = {x["target"]: x["value"] for x in res["kit"]["applied"]}
    assert applied["document.elements[0].text.stroke.color"] == "#101010" and applied["document.elements[0].text.background.color"] == "#0B1D2A"
    filled = {"document_id": "kit", "canvas": {"width": 1280, "height": 720},
              "elements": [{"element_id": "title", "type": "text", "text": {"text": "HH", "font_size": 200, "position": {"x": 120, "y": 400}, "color": "#00FF00",
                                                                             "font_id": "sans-bold", "background": {"color": "#0B1D2A", "padding": 24}, "stroke": {"color": "#101010", "width": 3}}}]}
    assert "#00FF00" in top_colours(workspace / "thumb.png", title_box(filled))
    with Image.open(workspace / "thumb.png") as im:
        assert im.convert("RGB").getpixel((640, 100)) == (0x20, 0x20, 0x20)


def test_three_variants_with_a_kit_use_only_the_kits_colours(media, workspace):
    kit = kit_file(workspace / "brand", media)
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}, "variants": {"count": 3}},
                 workspace, "thumbnail/render_variants")
    assert res["ok"] is True, res
    kit_colours = set(res["kit"]["colors"].values())
    assert res["axes"]["palettes"][0] == "as-given" and all(p.startswith("kit-") for p in res["axes"]["palettes"][1:])
    assert "colors.primary" not in [u["key"] for u in res["kit"]["unmapped"]]   # the palettes use it
    palettes = {p["name"]: p for p in res["kit"]["palettes"]}
    for v in res["variants"]:
        name = v["choices"]["palette"]
        if name == "as-given":
            colours = {"#F4F1E8", "#0B1D2A"}
        else:
            p = palettes[name]
            colours = {p["color"]} | {x["color"] for x in (p["background"], p["stroke"]) if x}
        assert colours <= kit_colours, (name, colours)
        with Image.open(v["output"]) as im:
            counts = colour_counts(im.convert("RGB"))
        # the picture is the kit's colours, the logo's red, and the antialiased edges between them
        main = {"#%02X%02X%02X" % c for c, n in counts.items() if n > 2000}
        logo = "#%02X%02X%02X" % tuple(round(0.9 * a + 0.1 * b) for a, b in zip((220, 30, 30), (0x0B, 0x1D, 0x2A)))
        assert main - {logo} <= kit_colours, (v["variant"], main)
    assert [v["choices"]["palette"] for v in res["variants"]] == ["as-given", "kit-text-on-background-band", "kit-background-on-primary-band"]


def test_caller_palettes_win_over_the_kit(media, workspace):
    kit = kit_file(workspace / "brand", media)
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)},
                  "variants": {"count": 2, "palettes": [{"name": "mine", "color": "#123456"}]}}, workspace, "thumbnail/render_variants")
    assert res["ok"] is True, res
    assert res["axes"]["palettes"] == ["as-given", "mine"] and "palettes" not in res["kit"]
    assert "colors.primary" in [u["key"] for u in res["kit"]["unmapped"]]


def test_values_the_kit_lacks_are_not_filled(media, workspace):
    kit = kit_file(workspace / "brand", media, colors={"background": "0B1D2A"}, logo_scale=..., font="No Such Family")
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_REQUEST"   # no text colour, no font: the document must give them
    assert any("font" in w and "No Such Family" in w for w in res["warnings"])
    res = render({"document": doc(title_color="#FFFFFF", font_id="sans"), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}}, workspace)
    assert res["ok"] is True, res
    unmapped = {u["key"]: u["reason"] for u in res["kit"]["unmapped"]}
    assert "logo_scale" in unmapped["logo"] and "logo" not in res["kit"]          # no size stated, so no logo: none is invented
    assert "No Such Family" in unmapped["font"] and res["kit"]["font"]["font_id"] is None


def test_a_plain_brand_json_is_a_kit(media, workspace):
    """ffmpeg-skill's brand.json (no schema, a null font_file) reads as a kit; `colours` is the remotion spelling."""
    d = workspace / "brand"
    d.mkdir()
    shutil.copy(media["logo"], d / "logo.png")
    (d / "brand.json").write_text(json.dumps({"font": local_family(), "font_file": None, "colours": {"text": "FFFFFF", "background": "000000"},
                                              "logo": "logo.png", "logo_position": "bottom-left", "logo_scale": 100, "safe_margin": 10}), encoding="utf-8")
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(d / "brand.json")}}, workspace)
    assert res["ok"] is True, res
    assert res["kit"]["unmapped"] == [] and res["kit"]["logo"]["box"] == {"x": 10, "y": 720 - 10 - 100, "width": 100, "height": 100}
    assert res["kit"]["logo"]["opacity"] == 1.0 and "default" in res["kit"]["logo"]["opacity_from"]


def test_the_document_s_own_kit_logo_wins(media, workspace):
    kit = kit_file(workspace / "brand", media)
    document = doc()
    document["assets"] = [{"asset_id": "kit-logo", "kind": "image", "path": str(media["tall"])}]
    document["elements"].append({"element_id": "kit-logo", "type": "image", "image": {"asset_id": "kit-logo", "position": {"x": 0, "y": 0}, "size": {"width": 50, "height": 200}}})
    res = render({"document": document, "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}}, workspace)
    assert res["ok"] is True, res
    assert "logo" not in res["kit"] and any(x["kit_key"] == "logo" for x in res["kit"]["kept"])
    assert {u["key"] for u in res["kit"]["unused"]} >= {"logo", "logo_position", "logo_scale", "safe_margin", "logo_opacity"}


def test_batch_applies_the_kit_to_every_target(media, workspace):
    kit = kit_file(workspace / "brand", media)
    template = doc()
    template["elements"][0]["text"]["text"] = "{{title}}"
    template["elements"][0]["text"]["font_size"] = 80
    template["elements"][0]["text"]["position"] = {"x": 60, "y": 200}
    res = render({"rows": [{"title": "One"}, {"title": "Two"}], "targets": [{"preset": "youtube", "document": template}, {"preset": "youtube-shorts", "document": template}],
                  "output": {"directory": "out", "format": "png"}, "options": {"kit": str(kit)}}, workspace, "thumbnail/render_batch")
    assert res["ok"] is True, res
    assert res["count"] == 4
    boxes = [a["value"] for a in res["kit"]["applied"] if "kit-logo" in a["target"]]
    assert boxes == [{"x": 1120, "y": 40, "width": 120, "height": 120}, {"x": 1080 - 160, "y": 40, "width": 120, "height": 120}]
    with Image.open(workspace / "out" / "1-youtube-shorts.png") as im:
        assert im.convert("RGB").getpixel((540, 1800)) == (0x0B, 0x1D, 0x2A)


def test_layers_with_a_kit(media, workspace):
    kit = kit_file(workspace / "brand", media)
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}, "layers": {"directory": "layers"}},
                 workspace, "thumbnail/render_layers")
    assert res["ok"] is True, res
    assert res["layers"]["verified"] and "kit-logo" in [x["element_id"] for x in res["layers"]["layers"]]
    assert res["kit"]["logo"]["element_id"] == "kit-logo"


def test_validate_does_not_read_the_kit(workspace):
    res = run_tool("thumbnail/validate", {"document": dict(doc(), canvas={"width": 1280, "height": 720}), "options": {"kit": "missing/kit.json"}}, PathPolicy(str(workspace)))
    assert res["ok"] is True, res
    k = res["validation"]["kit"]
    assert k["read"] is False and "document.elements[0].text.color" in k["slots"] and "document.elements[0].text.font_id" in k["slots"]


@pytest.mark.parametrize("change, field", [({"colors": {"text": "#FFFFFF"}}, "colors.text"), ({"logo_position": "middle"}, "logo_position"),
                                           ({"logo_scale": 0}, "logo_scale"), ({"schema": "velisa/kit@2"}, "schema"),
                                           ({"colours": {"text": "FFFFFF"}}, "colours")])
def test_a_malformed_value_that_maps_is_refused(media, workspace, change, field):
    kit = kit_file(workspace / "brand", media, **change)
    res = render({"document": doc(), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_INPUT"
    assert res["error"]["details"] == {"reason": "bad_kit", "field": field}
    assert not (workspace / "thumb.png").exists()


def test_kit_refusals(media, workspace):
    res = render({"document": doc(), "output": {"path": "t.png", "format": "png", "preset": "youtube"}, "options": {"kit": "nope.json"}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_INPUT"
    res = render({"document": doc(), "output": {"path": "t.png", "format": "png", "preset": "youtube"}, "options": {"kit": 5}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_REQUEST"
    (workspace / "big.json").write_text("{" + " " * (256 * 1024) + "}", encoding="utf-8")
    res = render({"document": doc(), "output": {"path": "t.png", "format": "png", "preset": "youtube"}, "options": {"kit": "big.json"}}, workspace)
    assert res["ok"] is False and res["error"]["details"]["reason"] == "bad_kit"
    res = run_tool("thumbnail/extract_frame", {"source": {"path": str(media["bg_png"]), "timestamp": 0}, "output": {"path": "f.png", "format": "png"}, "options": {"kit": "k.json"}},
                   PathPolicy(str(workspace)))
    assert res["ok"] is False and res["error"]["code"] == "INVALID_REQUEST" and res["error"]["details"]["field"] == "options.kit"
    outside = workspace.parent / "elsewhere"
    kit = kit_file(outside, media)
    res = run_tool("thumbnail/render", {"document": doc(), "output": {"path": "t.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)}},
                   PathPolicy(str(workspace), [str(workspace)]))
    assert res["ok"] is False and res["error"]["code"] == "PATH_NOT_ALLOWED"


def test_without_a_kit_nothing_changes(media, workspace):
    res = render({"document": doc(), "output": {"path": "t.png", "format": "png", "preset": "youtube"}}, workspace)
    assert res["ok"] is False and res["error"]["code"] == "INVALID_REQUEST"   # colour and font_id stay required
    res = render({"document": doc(title_color="#FFFFFF", font_id="sans", band=False), "output": {"path": "t.png", "format": "png", "preset": "youtube"}}, workspace)
    assert res["ok"] is True and "kit" not in res and "kit" not in res["provenance"]


def test_the_fixture_font_maps_where_dejavu_is_registered():
    match, seen = font_for_family("DejaVu Sans")
    if not any(s["family"] == "DejaVu Sans" for s in seen):
        pytest.skip("no registered font_id resolves to DejaVu Sans here")
    assert match["font_id"] == "sans" and match["family"] == "DejaVu Sans"


def test_cli_kit_flag(media, workspace, capsys):
    kit = kit_file(workspace / "brand", media)
    req = workspace / "req.json"
    req.write_text(json.dumps({"document": doc(), "output": {"path": "cli.png", "format": "png", "preset": "youtube"}}), encoding="utf-8")
    code = cli_main(["render", str(req), "--kit", str(kit), "--workspace", str(workspace), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["kit"]["read"] is True and (workspace / "cli.png").is_file()


def test_contract_declares_the_kit_block_and_leaves_the_rest():
    c = skill_contract()
    k = c["kit"]
    assert k["schema"] == "velisa/kit@1" and k["field"] == "options.kit" and set(k["tools"]) >= {"thumbnail/render", "thumbnail/render_batch", "thumbnail/render_variants"}
    assert {m["kit"] for m in k["mapping"]} >= {"colors.text", "colors.background", "colors.outline", "font"}
    assert "font_file" in k["unmapped"] and k["decision"] == "docs/decisions.md ADR-18"
    render_tool = next(t for t in c["tools"] if t["tool_id"] == "thumbnail/render")
    assert render_tool["input"] == {"document": "ThumbnailDocument", "output": "OutputSpec", "options": "RenderOptions"}


def test_variants_with_a_one_colour_kit_stay_in_the_kit(media, workspace):
    kit = kit_file(workspace / "brand", media, colors={"text": "F4F1E8"})
    res = render({"document": doc(band=False), "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)},
                  "variants": {"count": 2}}, workspace, "thumbnail/render_variants")
    assert res["ok"] is True, res
    assert res["axes"]["palettes"] == ["as-given", "kit-text"] and res["variants"][1]["choices"]["palette"] == "kit-text"
    kit = kit_file(workspace / "brand2", media, colors=...)
    res = render({"document": doc(title_color="#FFFFFF", band=False), "output": {"path": "t2.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)},
                  "variants": {"count": 2}}, workspace, "thumbnail/render_variants")
    assert res["ok"] is False and res["error"]["details"]["reason"] == "kit_without_colors"


def test_the_logo_goes_above_a_float_z_index(media, workspace):
    kit = kit_file(workspace / "brand", media)
    document = doc()
    document["elements"][0]["z_index"] = 5.0
    res = render({"document": document, "output": {"path": "thumb.png", "format": "png", "preset": "youtube"}, "options": {"kit": str(kit)},
                  "layers": {"directory": "layers"}}, workspace, "thumbnail/render_layers")
    assert res["ok"] is True, res
    assert [x["element_id"] for x in res["layers"]["layers"]][-1] == "kit-logo"

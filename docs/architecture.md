# Architecture

## Modules

| Module | Owns |
|---|---|
| `model.py` | Typed `ThumbnailDocument`/`Canvas`/`Element`/`Asset`/`OutputSpec` and their structural validation. Touches no file system, no font, no ffmpeg-skill. Every `_obj()` call is an explicit field allowlist; `_reject_forbidden()` walks the whole structure (including free-form `metadata`) for command/filter/shell/HTML-CSS-JS field names. |
| `fonts.py` | The font registry (`font_id -> ordered per-platform candidate paths`) and `resolve_font()`/`font_status()`. The only place a font file path is ever produced. |
| `security.py` | `PathPolicy`: input containment (allowed roots, symlink-safe), output/workspace containment, safe file names. No process is started here. |
| `adapter.py` | `FfmpegSkill`: the only place a subprocess is started. Locates an ffmpeg-skill checkout, calls `probe` and `look --at --no-timecode` with typed argv, parses their JSON, maps failures to `TOOL_ERROR`. |
| `renderer.py` | Pure compositing: canvas fill, per-element `draw_image_element`/`draw_text_element`, z-ordered. Takes already-opened Pillow images and already-resolved fonts; no file I/O, no ffmpeg, no randomness. |
| `executor.py` | Orchestrates one tool call end to end: parse -> resolve assets/fonts (content identity) -> identity/cache check -> render or reuse -> validate output -> provenance -> response document. Never raises past `Executor.response()`. |
| `contract.py` / `doctor.py` | The machine-readable contract and environment diagnosis, derived from the same tables `model.py`/`fonts.py`/`errors.py` define — not hand-maintained separately. |
| `skill.py` | `run_tool()` / `run_request()`: the single dispatch point and the `run -` stdin/stdout transport. |
| `cli.py` | argparse CLI over `skill.py`. |

## Data flow (`thumbnail/render`)

```
params (JSON)
  -> model.parse_render_request           structural validation, no I/O
  -> Executor._resolve_asset_identity     per asset: PathPolicy.resolve_input, then
                                             image:       Pillow open+verify -> sha256
                                             video_frame: ffmpeg-skill/probe (duration, timestamp bound) -> sha256 of the *source video*
  -> Executor._resolve_font_identities    fonts.resolve_font() per font_id actually used by a text element
  -> Executor._identity                   sha256 of canonical JSON {document, asset content identity, font content identity, skill+engine version}
  -> Executor._try_reuse                  cache hit? re-validate the cached file, copy it to the output path, done (video is never decoded)
  -> (miss) resolve each asset to a Pillow Image
       image:       already opened above
       video_frame: ffmpeg-skill/look --at <timestamp> --no-timecode -> PNG -> Pillow open
  -> renderer.render_document              canvas fill -> elements sorted by (z_index, document order) -> Pillow paste/crop/resize/draw
  -> finalize_for_save                     PNG keeps alpha; JPEG is flattened onto the canvas's own background colour first
  -> Executor._validate_output             re-open, check format/dimensions, sha256
  -> Executor._store_cache + _place_output write the cache entry, then copy into the final output path
  -> response document                     output, format, width, height, size, sha256, reused, provenance
```

`thumbnail/extract_frame` is the same shape without a canvas: source video -> `ffmpeg-skill/probe`
(duration check) -> identity(source sha256, timestamp) -> reuse check -> `ffmpeg-skill/look` ->
Pillow re-encode to the requested format -> validate -> response. `thumbnail/validate` stops after
`model.parse_document`: no PathPolicy, no font, no ffmpeg-skill, no file ever opened.

## Why Pillow, not another ffmpeg-skill call, for compositing

ffmpeg-skill's `overlay`/`graphics`/`caption` tools operate on a video input and always write a new
video artifact (re-encoded with libx264, `-shortest`, audio mapped or dropped) — they are not a
still-image compositor, and forcing a still-image canvas through them would mean either producing a
one-frame "video" file or hand-building a filter graph neither this skill nor ffmpeg-skill's own
contract is meant to expose generically. thumbnail-skill therefore draws the canvas itself with
Pillow — a bounded, typed, well-audited raster library, not a shell, not a filter-string builder —
and delegates to ffmpeg-skill only the one thing only ffmpeg-skill can do safely: decoding a frame
out of a video container at a caller-given timestamp. This keeps the "no arbitrary ffmpeg filter"
rule (see docs/security.md) trivially true for compositing: no ffmpeg process is involved in it at all.

# thumbnail-skill

Deterministic thumbnail rendering as an independent, reusable Skill: a typed **ThumbnailDocument**
in, a validated **PNG/JPEG** artifact out.

**thumbnail-skill does not decide what a thumbnail should show.** It does not pick a "best" video
frame, detect faces or objects, run OCR, generate a title, judge design quality, predict
click-through rate, or decide whether to publish. It renders exactly the canvas, image(s) and text
the caller specifies, and nothing else.

```
Still image, or an explicit video timestamp + text + layout (all caller-decided)
    ↓
thumbnail-skill                (this repository)
    ↓
Validated PNG/JPEG + provenance
    ↓
video-production-agent         (decides what to show, how to lay it out: a separate repository)
```

## Install

```bash
pip install "thumbnail-skill @ git+https://github.com/kajisho5/thumbnail-skill"
# or from a checkout
pip install -e .
```

Requirements: Python 3.9+, [Pillow](https://python-pillow.org/) (the rendering engine — pure
Python + libraries it vendors; no ffmpeg dependency for still images or text). A `video_frame`
asset additionally needs an [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) checkout
reachable (env var `THUMBNAIL_SKILL_FFMPEG_SKILL_DIR`, or `--ffmpeg-skill`); still-image-only
documents never need it. `thumbnail doctor --json` reports exactly what is available.

## Architecture

| layer | owns | does not do |
|-------|------|-------------|
| **video-production-agent** | what a thumbnail should show, which frame/timestamp, layout, copy | render pixels |
| **thumbnail-skill** (this repo) | rendering a typed ThumbnailDocument to a validated PNG/JPEG | choose content, judge design, run ffmpeg |
| **ffmpeg-skill** | decoding the one video frame this skill was told to use (`probe`, `look --at`) | scene detection, "best frame" search |
| **Pillow** (in-process) | raster compositing: paste, crop, resize, draw text | anything ffmpeg-skill doesn't already refuse |

Pipeline: `request → ThumbnailDocument validation → asset/font resolution → render plan (identity,
reuse check) → Pillow compositing (or a cache hit) → output validation → structured response with
provenance`. thumbnail-skill never builds an ffmpeg filter string and never runs a shell; the only
subprocess it starts is ffmpeg-skill's own CLI, with typed argv, exactly the way
[audio-production-skill](https://github.com/kajisho5/audio-production-skill) delegates to it.

## CLI

```bash
thumbnail doctor --json                        # fonts, ffmpeg-skill, path policy, output formats
thumbnail validate request.json --json         # structural check only; no file system, no font, no ffmpeg-skill
thumbnail render request.json --json           # canvas + assets + elements -> PNG/JPEG
thumbnail extract-frame request.json --json    # one video timestamp -> one frame, nothing else
thumbnail skill --json                         # the machine-readable contract (source of truth)
echo '{"tool":"thumbnail/render","params":{...}}' | thumbnail run -
                                                # process-boundary transport: one JSON request in, one JSON response out
```

Exit codes: `0` success; otherwise `errors.EXIT_CODES[error.code]` (see the table below). With
`--json`, stdout carries exactly one JSON document on success or failure alike; without it, a short
human line goes to stdout and errors to stderr.

## ThumbnailDocument

```json
{
  "document": {
    "document_id": "ep42_thumb",
    "canvas": {"width": 1280, "height": 720, "background": "#000000"},
    "assets": [
      {"asset_id": "bg",   "kind": "video_frame", "path": "episode42.mp4", "timestamp": 812.5},
      {"asset_id": "logo", "kind": "image", "path": "brand/logo.png"}
    ],
    "elements": [
      {"element_id": "bg_layer", "type": "image", "z_index": 0,
       "image": {"asset_id": "bg", "position": {"x": 0, "y": 0}, "size": {"width": 1280, "height": 720}, "fit": "cover"}},
      {"element_id": "logo_layer", "type": "image", "z_index": 5,
       "image": {"asset_id": "logo", "position": {"x": 1060, "y": 20}, "size": {"width": 200, "height": 200}, "fit": "contain", "opacity": 0.9}},
      {"element_id": "title", "type": "text", "z_index": 10,
       "text": {"text": "第38回学会\n特別講演のお知らせ", "font_id": "cjk", "font_size": 64, "color": "#FFFFFF",
                "position": {"x": 40, "y": 560}, "align": {"horizontal": "left", "vertical": "top"},
                "background": {"color": "#000000AA", "padding": 16},
                "stroke": {"color": "#000000", "width": 3}, "shadow": {"color": "#00000080", "offset_x": 3, "offset_y": 3}}}
    ],
    "metadata": {"episode": 42}
  },
  "output": {"path": "episode42_thumb.png", "format": "png", "overwrite": false},
  "options": {"allowed_input_roots": ["/jobs/42/media"], "workspace": "/jobs/42/work", "reuse": true, "timeout": 120}
}
```

| Concept | Fields | Notes |
|---|---|---|
| `ThumbnailCanvas` | `width`, `height` (16..7680), `background` (`#RRGGBB`/`#RRGGBBAA`) | the output is always exactly this size; no hard-coded 1280x720 |
| `ThumbnailAsset` | `asset_id`, `kind` (`image` \| `video_frame`), `path`, `timestamp` (video_frame only) | `timestamp` is the caller's decision; this skill fetches exactly that frame, never a "better" one |
| `ThumbnailElement` (image) | `asset_id`, `position {x,y}`, `size {width,height}`, `fit` (`cover`\|`contain`\|`fill`\|`none`), `crop {x,y,width,height}`, `opacity`, `rotation` (`0`\|`90`\|`180`\|`270`) | source-pixel crop is applied before fit/scale |
| `ThumbnailElement` (text) | `text`, `font_id`, `font_size` (6..400), `color`, `position`, `align {horizontal,vertical}`, `line_spacing`, `opacity`, `background {color,padding}`, `stroke {color,width}`, `shadow {color,offset_x,offset_y}` | line breaks are literal `\n` in `text`; there is no automatic word-wrap or layout — that is a caller decision |
| stacking | `z_index` on every element, ties broken by document order | no implicit ordering |

Unknown fields anywhere are rejected (`INVALID_REQUEST`); `command`, `argv`, `shell`, `exec`,
`executable`, `script`, `filter`, `filter_complex`, `vf`, `af`, `ffmpeg`, `env`, `cwd`, `eval`,
`html`, `css`, `javascript` are rejected by name wherever they appear, including inside the
free-form `metadata` object.

## Fonts

A request never carries a font path — only a registered `font_id`. `fonts.py` lists, per `font_id`,
the well-known per-OS locations a real font of that role lives at; the first one that exists on this
machine is used (detected, like ffmpeg-skill detects encoders — never assumed). `thumbnail doctor
--json` reports which `font_id`s resolved on this machine, to which exact file, and that file's
sha256; a `font_id` with no resolvable file fails `MISSING_INPUT` — this skill never substitutes a
different typeface and calls it success.

| font_id | role |
|---|---|
| `sans` | default UI sans-serif |
| `sans-bold` | emphasis / headline sans-serif |
| `serif` | serif text |
| `mono` | monospaced text |
| `cjk` | Japanese / Chinese / Korean text |

Adding a font means adding a registry entry (a reviewed change), not a runtime decision by a caller.

## Video frame source

`kind: "video_frame"` fetches exactly the frame at `timestamp` (seconds) via
`ffmpeg-skill/look --at <timestamp> --no-timecode`. There is no scene detection, no frame scoring,
no "most representative frame" search anywhere in this skill — if that is ever needed, it belongs
in a separate analysis/inference layer that hands this skill a concrete timestamp. A timestamp
beyond the source's own duration is `INVALID_TIME_RANGE`; a negative or non-finite one is rejected
before ffmpeg-skill is ever invoked. A timestamp that is technically within the reported duration but
lands after the last frame actually decodable from the source (a container's `duration` commonly
extends about one frame interval past the last frame's own timestamp, so the very end of any video
has a small window like this) is also `INVALID_TIME_RANGE`, not retryable: retrying the identical
timestamp will fail identically every time, so pick an earlier one instead.

## Output formats

PNG (lossless, keeps the alpha channel) and JPEG (`jpeg_quality` 1..100; flattened onto the canvas's
own background colour before encoding, since JPEG has no alpha channel). Only formats actually
implemented are advertised by `thumbnail skill --json` and `thumbnail doctor --json`. Every output
is re-opened and checked before being reported successful: real image, declared format, exact
canvas dimensions, non-empty, sha256 recorded.

## Security

- No shell, ever (`subprocess.Popen` with an argv list, `shell=False`, in the adapter to
  ffmpeg-skill only).
- No arbitrary executable: the only process this skill starts is `python3 <ffmpeg-skill>/scripts/
  {probe,look}.py <typed argv> --json`, resolved from a checkout directory, never from a
  request-supplied path.
- No arbitrary ffmpeg filter, no `filter_complex`/`vf`/`af`, no raw argv: video decoding is exactly
  ffmpeg-skill's own `--at <timestamp> --no-timecode`; nothing from the request reaches that argv
  except a validated, formatted timestamp and resolved absolute paths.
- No JavaScript, HTML or CSS execution or interpretation anywhere: text is drawn as data
  (`PIL.ImageDraw.text`), never evaluated; those field names are refused outright.
- Every input path is resolved (symlinks followed) and, when `allowed_input_roots` is declared,
  checked for containment by resolved path components — never by string prefix
  (`/w/media` does not authorise `/w/media_evil`); a `..` component that resolves outside a root, or
  a symlink/junction that does, is `PATH_NOT_ALLOWED`. Without `allowed_input_roots`, any readable
  regular file is accepted (unrestricted mode), matching transcription-skill's default.
- Every output, the reuse cache and the temp directory resolve inside `workspace`; an output may
  never be an input, may not exist unless `overwrite: true`, and its file name is checked against
  reserved Windows device names, invalid characters, and option-like (`-...`) names.
- A decompression-bomb-scale source image is refused (`INVALID_INPUT`) rather than exhausting memory.

## Deterministic execution and reuse

Given the same document, the same asset/font *content* (by sha256, not by path), the same
skill/Pillow version and — when a `video_frame` asset is involved — the same ffmpeg-skill version,
`thumbnail/render` and `thumbnail/extract_frame` reuse a cached artifact instead of re-rendering; a
`video_frame` asset's identity is its **source video's** sha256 plus the requested timestamp, not the
decoded frame's bytes, so a cache hit never has to decode video. Any of those inputs changing —
different image bytes, a different font, a different document (layout, z-order, text, ...), a
different timestamp, a different source video, an ffmpeg-skill or Pillow upgrade — changes the
identity and busts the cache; a source video whose duration cannot be established at all is refused
outright (`INVALID_INPUT`) rather than silently skipping the beyond-duration check. A cache entry is
re-validated (exists, opens as the declared format, size and dimensions match) before being reused; a
corrupted entry is rebuilt, never returned as `reused: true`. No randomness anywhere in the render
path. Two renders of the same document on the same machine produce byte-identical output; across
machines, only the resolved font file, the Pillow version and (for `video_frame`) the ffmpeg-skill
version need to match for the same guarantee (all are recorded in `provenance`, matching
ffmpeg-skill's own `content_equivalent` convention for environment-dependent encoders).

## Error codes

| Code | Meaning |
|---|---|
| `INVALID_REQUEST` | document shape, unknown/forbidden field, bad type or bound |
| `INVALID_INPUT` | a source file is missing, unreadable, or not a valid image |
| `PATH_NOT_ALLOWED` | input outside allowed roots, output outside workspace, traversal, symlink escape |
| `UNSUPPORTED_OPERATION` | unknown tool name, or an unimplemented option (e.g. arbitrary-angle rotation) |
| `UNSUPPORTED_FORMAT` | output format not in the contract |
| `MISSING_INPUT` | an element references an `asset_id`/`font_id` that is not declared or not registered |
| `INVALID_TIME_RANGE` | a `video_frame` timestamp is negative, non-finite, beyond the source's duration, or lands after the last decodable frame (not retryable) |
| `DEPENDENCY_ERROR` | duplicate id or another structural inconsistency |
| `TOOL_ERROR` | ffmpeg-skill failed, timed out, or is unavailable |
| `OUTPUT_ERROR` | output could not be written, is empty, collides with an input, or already exists |
| `VALIDATION_ERROR` | output written but failed post-render validation |
| `CANCELLED` | interrupted by signal |
| `INTERNAL_ERROR` | a bug in this skill |

## Provenance

Every successful `render`/`extract_frame` response carries `provenance`: skill id/version, the
rendering engine and its version, the render/extraction identity hash, `reused`, every asset's
resolved path + sha256 (+ timestamp and source duration for `video_frame`), every font actually
used (`font_id`, path, sha256), and the output's own sha256.

## Where this skill ends and others begin

| Repository | Question it answers | Example |
|---|---|---|
| **media-analysis-skill** | What exists in the media? (measurement) | `duration = 312.4`, `silence = [...]` |
| **transcription-skill** | What is being said, and when? | `"本日の講演を始めます"  start 12.3  end 15.8` |
| **subtitle-skill** | How is speech shown as on-screen subtitles? | line breaks, styling, SRT/ASS, burn-in |
| **thumbnail-skill** (here) | Render a decided thumbnail specification | one canvas, positioned image/text layers, PNG/JPEG |
| **video-editing-skill** | Cut, arrange and assemble a video timeline | trims, transitions, sequencing |
| **motion-graphics-skill** | In-video graphics / motion rendering | lower thirds, animated titles inside the video itself |
| **qc-skill** | Inspect a finished artifact | does the delivered file meet spec? |
| **video-production-agent** | What should be done, with what content? (inference, decisions, planning) | "use frame at 13:32.5", "title reads X", "put the logo top-right" |

thumbnail-skill deliberately contains none of: AI reasoning, automatic frame selection, face/object
detection, OCR, semantic analysis, automatic title/layout/design generation, click-through
prediction, A/B testing, publish decisions, video editing, color grading, subtitle rendering, cloud
upload, a plugin loader, arbitrary shell/ffmpeg/JavaScript/HTML/CSS execution.

## Documentation

- [SKILL.md](SKILL.md): how a coding agent should use the skill
- [docs/architecture.md](docs/architecture.md): modules, data flow, boundaries
- [docs/model.md](docs/model.md): the full ThumbnailDocument / Canvas / Element / Asset schema
- [docs/security.md](docs/security.md): execution boundary, path policy, validation
- [docs/testing.md](docs/testing.md): unit, security, integration/real-media tests; fixtures

## Support

If this skill saves you time, you can help keep it maintained through [GitHub Sponsors](https://github.com/sponsors/kajisho5). Issues and pull requests are just as welcome.

## License

MIT

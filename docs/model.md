# ThumbnailDocument model

Full field reference for `model.py`. All bounds are enforced structurally before anything touches
a file, a font or ffmpeg-skill.

## ThumbnailCanvas

| Field | Type | Bounds | Notes |
|---|---|---|---|
| `width`, `height` | int | 16..7680 | the rendered output is always exactly this size; never hard-coded |
| `background` | `#RRGGBB` \| `#RRGGBBAA` | — | default `#000000`; fills any area no image element covers |

## ThumbnailAsset

| Field | Type | Notes |
|---|---|---|
| `asset_id` | id (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`) | unique within the document |
| `kind` | `image` \| `video_frame` | |
| `path` | string | resolved through `PathPolicy` at render time; untrusted |
| `timestamp` | number, seconds | required iff `kind: video_frame`; forbidden otherwise; `0 <= timestamp <= 604800`; checked against the source's own duration once probed |

A `video_frame` asset never triggers a search: `timestamp` is used exactly as given. Choosing a
timestamp (best frame, a moment after a cut, a specific slide) is the caller's job.

## ThumbnailElement

| Field | Type | Notes |
|---|---|---|
| `element_id` | id | unique within the document |
| `type` | `image` \| `text` | selects which of `image`/`text` below is required |
| `z_index` | int, -1000..1000 | default 0; stacking order, ties broken by document order |

### `image` content

| Field | Type | Notes |
|---|---|---|
| `asset_id` | id | must reference a declared asset (`MISSING_INPUT` otherwise) |
| `position` | `{x, y}` | top-left of the target box, canvas pixels; may be negative (partial off-canvas placement is allowed, deliberately, not auto-clamped) |
| `size` | `{width, height}` | 1..7680 each; the target box on the canvas |
| `fit` | `cover` \| `contain` \| `fill` \| `none` | default `cover`. `fill` stretches (no aspect preserved); `cover` scales to fully cover the box and centre-crops the overflow; `contain` scales to fit entirely inside the box (result may be smaller); `none` places the source at native size, centred in the box |
| `crop` | `{x, y, width, height}` \| null | source-pixel space, applied before `fit`; validated against the asset's actual decoded size at render time (`INVALID_REQUEST`, reason `crop_out_of_bounds`, if it doesn't fit) |
| `opacity` | 0..1 | default 1 |
| `rotation` | `0` \| `90` \| `180` \| `270` | default 0; clockwise; axis-aligned only — arbitrary-angle rotation is not implemented (`UNSUPPORTED_OPERATION`), to keep output deterministic and free of resampling artefacts at the canvas edge |

### `text` content

| Field | Type | Notes |
|---|---|---|
| `text` | string, 1..2000 chars, <= 50 lines | control characters other than `\n` are rejected; **line breaks are literal `\n`** — there is no automatic word-wrap, no reflow, no layout decision made here |
| `font_id` | one of the registry (`fonts.py`) | never a path; unresolvable on this machine -> `MISSING_INPUT` |
| `font_size` | int, 6..400 | pixels |
| `color` | `#RRGGBB` \| `#RRGGBBAA` | |
| `position` | `{x, y}` | the anchor point; `align` says which corner/edge of the text block sits there |
| `align` | `{horizontal: left|center|right, vertical: top|middle|bottom}` | default `left`/`top` |
| `line_spacing` | 0.5..5.0 | default 1.2, multiplies the font's own line height |
| `opacity` | 0..1 | default 1 |
| `background` | `{color, padding}` \| null | a filled box drawn behind the text block, `padding` 0..200 |
| `stroke` | `{color, width}` \| null | outline, `width` 0..40 |
| `shadow` | `{color, offset_x, offset_y}` \| null | flat offset shadow, each offset -200..200 (no blur: kept simple and deterministic) |

## OutputSpec

| Field | Type | Notes |
|---|---|---|
| `path` | string | resolved through `PathPolicy`; must end in the extension the `format` expects |
| `format` | `png` \| `jpeg` | only formats actually implemented are ever advertised |
| `overwrite` | bool | default false |
| `jpeg_quality` | 1..100 | only accepted when `format: jpeg` |

## Options

| Field | Type | Notes |
|---|---|---|
| `allowed_input_roots` | array of directories \| null | default: unrestricted (any readable regular file) |
| `workspace` | directory \| null | default: current directory; confines output, the reuse cache and temp files |
| `reuse` | bool | default true |
| `timeout` | 1..3600 seconds | default 120; per ffmpeg-skill invocation |
| `ffmpeg_skill` | directory \| null | explicit ffmpeg-skill checkout, overriding the environment-variable/well-known-path discovery |

## What is deliberately not a field

`command`, `commands`, `argv`, `args`, `cmd`, `shell`, `exec`, `executable`, `script`, `filter`,
`filters`, `filter_complex`, `vf`, `af`, `ffmpeg`, `env`, `cwd`, `eval`, `html`, `css`, `javascript`
— rejected by name at any depth, including inside `metadata`, which otherwise accepts arbitrary
caller data (capped at 8 KiB of canonical JSON) that this skill never reads or renders.

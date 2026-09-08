# ffmpeg-skill relationship

thumbnail-skill is a client of ffmpeg-skill's **public contract** (`ffmpeg-skill contract --json`,
`contract_version "1.0"`; verified against ffmpeg-skill 0.9.1 at commit `2abd89c`). It never calls
`ffmpeg`/`ffprobe` itself — `adapter.py` is the only place a subprocess is started, and it only ever
invokes ffmpeg-skill's own typed CLI.

Unlike [audio-production-skill](https://github.com/kajisho5/audio-production-skill), which depends
on ffmpeg-skill's wider audio-processing surface and pins a version *window*, thumbnail-skill uses
exactly two read-only tools and pins `contract_version` by exact match (see ADR-8 in
docs/decisions.md).

## Tools and flags used

| ffmpeg-skill tool | used for | flags emitted (`adapter.FLAGS_USED`) |
|---|---|---|
| `ffmpeg-skill/probe` | source video duration and video-stream presence, before extracting a frame | positional input |
| `ffmpeg-skill/look` | extract exactly the frame at one caller-given timestamp | `--at`, `--no-timecode`, `-o`, `--json` |

`doctor` / `Executor._ffmpeg_skill()` (`adapter.FfmpegSkill.info()`) checks that the located
checkout declares `contract_version` exactly `"1.0"` and that both tools' `input_schema.properties`
include every flag this adapter emits; a mismatch is `TOOL_ERROR` / `ffmpeg_skill_incompatible`
(non-retryable) — thumbnail-skill never falls back to calling ffmpeg directly when ffmpeg-skill is
missing or incompatible, only exposes still-image-only rendering as unaffected (see README's
"Requirements" note: a still-image-only document never touches ffmpeg-skill at all).

`--compare` (ffmpeg-skill/look's multi-timestamp mode) is never used: thumbnail-skill always wants
exactly one frame at one caller-decided timestamp, never a "best of several" search — that decision
belongs to a caller (video-production-agent), not to this skill (see README's responsibility
boundary).

## Observed behaviour this skill relies on (measured, ffmpeg-skill 0.12.2 / real ffmpeg; contract
verified against 0.9.1 at commit `2abd89c` and unchanged in shape through 0.12.2)

- `look.py --at <timestamp> --no-timecode -o <stem>` writes exactly one file, named
  `<stem>_<timestamp:.3f>s.png` (`adapter.frame_filename()`), and reports it in `outputs`/`output`
  on success.
- A timestamp landing after the last frame actually present in the stream but at or before the
  reported `duration` (a container's `duration` commonly extends about one frame interval past the
  last frame's own PTS) makes the underlying `ffmpeg -ss <timestamp>` decode zero frames and write
  nothing. Measured on a 3.0s / 10 fps test video: timestamps from roughly 2.91s up to (and
  including) the reported 3.0s `duration` fall in this dead zone. As of ffmpeg-skill 0.11.0's "fail
  loudly" pass (see ffmpeg-skill/CHANGELOG.md), `look.py` verifies its own output before reporting
  success and now reports this dead zone as a hard, `--json` failure document: `{"status": "failed",
  "exit_code": 1, "error": {"kind": "output", "code": "OUTPUT_INVALID", "message": "output
  verification failed: <path>: not written", "retryable": false}}` (before 0.11.0, measured against
  0.9.1, `look.py` instead claimed `{"status": "completed"}` while writing nothing — the false-
  success/no-file outcome this skill's ADR-5 was originally written against). Either way this is a
  property of ffmpeg-skill/`ffmpeg -ss` seeking a timestamp with nothing there to decode, not of
  thumbnail-skill; it is not something thumbnail-skill works around with its own ffmpeg call.
  thumbnail-skill reports this specific outcome as `INVALID_TIME_RANGE` (non-retryable) rather than
  a generic (retryable) `TOOL_ERROR`: `adapter.extract_frame()` recognizes ffmpeg-skill's current
  fail-loudly shape directly (`error.kind == "output"` with a "not written"/"0 bytes" message, via
  `run_tool()`'s `reclassify` hook), falling back to checking the output file's actual existence —
  never trusting ffmpeg-skill's own reported status — for a pre-0.11.0 checkout that still claims
  success. See ADR-5 in docs/decisions.md and `adapter.extract_frame()`'s docstring.
- `probe.py <path>` prints its document directly (no `{"status": ...}` envelope, unlike `look`); a
  non-zero exit or a document with no `duration` key means the source could not be read at all —
  reported as `INVALID_INPUT` (the source is bad), never `TOOL_ERROR` (which this skill reserves for
  ffmpeg-skill itself being unavailable, incompatible, or failing after having accepted the input).

## Compatibility gaps (required capability → not implemented here)

None currently known. thumbnail-skill's entire dependency on ffmpeg-skill is "read this video's
duration" and "decode exactly this one timestamp to a PNG" — both are covered by `probe` and `look`
as ffmpeg-skill has implemented them since 0.9.1. If a future requirement needs more of
ffmpeg-skill's contract (frame-accurate seeking by frame index rather than timestamp, for example),
add it here as a gap, and as a request to ffmpeg-skill, before working around it with a private
ffmpeg invocation — see ADR-1.

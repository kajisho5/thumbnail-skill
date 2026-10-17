# ffmpeg-skill relationship

thumbnail-skill is a client of ffmpeg-skill's **public contract** (`ffmpeg-skill contract --json`).
It works with any ffmpeg-skill version that offers the flags it emits: compatibility is
**detected**, not decided by a version window or an exact `contract_version` match (ADR-8). It has
been run against ffmpeg-skill 0.12.2 (commit `336e0c4`, the oldest checkout CI still tests) and
2.10.3 (main at the time of writing), and CI runs the suite against ffmpeg-skill `main` on every
push, pull request and once a day, plus once against `336e0c4`. It never calls `ffmpeg`/`ffprobe`
itself — `adapter.py` is the only place a subprocess is started, and it only ever invokes
ffmpeg-skill's own typed CLI.

## Tools and flags used

| ffmpeg-skill tool | used for | flags emitted (`adapter.FLAGS_USED`) |
|---|---|---|
| `ffmpeg-skill/probe` | source video duration and video-stream presence, before extracting a frame | positional input |
| `ffmpeg-skill/look` | extract exactly the frame at one caller-given timestamp | `--at`, `--no-timecode`, `-o`, `--json`; `--overwrite` only when offered (`adapter.OPTIONAL_FLAGS`) |

`doctor` / `Executor._ffmpeg_skill()` (`adapter.FfmpegSkill.info()`) detects what the located
checkout can do:

- the contract is read from `scripts/_contract.py --json --static`, falling back to
  `scripts/contract.py` and to running without `--static` for older layouts;
- a flag this adapter emits counts as present if the tool's `input_schema.properties` lists it,
  or, failing that (or with no readable contract at all), if the tool's own `--help` shows it;
- `--overwrite` is passed to `look` only when detected: ffmpeg-skill 1.10+ refuses to replace an
  existing output without it (2.0 made that an error), and earlier releases reject the unknown
  flag. The frame file name is deterministic per request identity, so a re-render with
  `reuse: false` finds the previous frame in place;
- `skill.version` and `contract_version` are read and reported (doctor, provenance, cache identity)
  but never compared. Only a flag that is genuinely absent is a problem, and that is `TOOL_ERROR` /
  `ffmpeg_skill_incompatible` (non-retryable).

`doctor --json` reports `version`, `contract_version`, `detected_via` (per tool: `contract`,
`contract+help` or `help`) and `capabilities` (e.g. `look_overwrite`). thumbnail-skill never falls
back to calling ffmpeg directly when ffmpeg-skill is
missing or incompatible, only exposes still-image-only rendering as unaffected (see README's
"Requirements" note: a still-image-only document never touches ffmpeg-skill at all).

`--compare` (ffmpeg-skill/look's multi-timestamp mode) is never used: thumbnail-skill always wants
exactly one frame at one caller-decided timestamp, never a "best of several" search — that decision
belongs to a caller (video-production-agent), not to this skill (see README's responsibility
boundary).

## Observed behaviour this skill relies on (measured against ffmpeg-skill 0.12.2 and 2.10.3 / real
ffmpeg; contract first verified against 0.9.1 at commit `2abd89c`)

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

### What ffmpeg-skill 2.0 changed, and why none of it needs a version check here

ffmpeg-skill 2.0 removed `--crf` (for `--quality`), moved `scripts/_common.py` into a package,
changed `probe`'s `hdr` meaning (`bt2020_or_hdr` keeps the old one) and refused to overwrite
outputs without `--overwrite`. thumbnail-skill passes no `--crf`/`--quality`, imports nothing from
`scripts/_common*`, and reads only `duration` and `video` from `probe`, so only the overwrite change
reaches it, and that one is handled by detecting `--overwrite` (above). Result JSON top-level keys
(`status`, `outputs`/`output`, `error.kind`) are unchanged across versions.

## Compatibility gaps (required capability → not implemented here)

None currently known. thumbnail-skill's entire dependency on ffmpeg-skill is "read this video's
duration" and "decode exactly this one timestamp to a PNG" — both are covered by `probe` and `look`
as ffmpeg-skill has implemented them since 0.9.1. If a future requirement needs more of
ffmpeg-skill's contract (frame-accurate seeking by frame index rather than timestamp, for example),
add it here as a gap, and as a request to ffmpeg-skill, before working around it with a private
ffmpeg invocation — see ADR-1.

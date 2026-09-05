# Decisions

- **ADR-1 Compositing is Pillow, in-process; ffmpeg-skill decodes exactly one frame and nothing
  else.** ffmpeg-skill's `overlay`/`graphics`/`caption` tools operate on a video input and always
  write a new *video* artifact; forcing a still-image canvas through them would mean building a
  filter graph neither skill's contract is meant to expose generically. Pillow keeps "no arbitrary
  ffmpeg filter" trivially true for the actual pixel work: no ffmpeg process is involved in
  compositing at all (see docs/architecture.md).
- **ADR-2 A `video_frame` asset's cache identity is its source video's sha256 + timestamp, not the
  decoded frame's bytes.** A cache hit therefore never has to decode video. The cost: two different
  timestamps on the same source are two different cache entries even if they happen to decode to
  the same pixels — accepted, since detecting that would require decoding the frame anyway.
- **ADR-3 Fonts are a registry, never a request-supplied path.** `fonts.py` maps `font_id` to
  ordered per-platform candidate paths; the first one that exists is used. An unresolvable
  `font_id` is `MISSING_INPUT`, never a silent substitution — this skill does not guess what
  typeface the caller meant.
- **ADR-4 Path containment is resolved-path, never string-prefix; write-path resolution uses
  `os.path.realpath`, not walk-to-nearest-existing-ancestor.** String-prefix containment admits
  `/w/media_evil` under an allowed root `/w/media`; walking to the nearest *existing* ancestor
  before resolving admits a dangling symlink placed at a not-yet-existing path component. Both were
  found and closed during pre-release review (see git history around `security.py`); `PathPolicy`
  now resolves the full target (including a non-existent leaf) and checks containment on the
  resolved result.
- **ADR-5 ffmpeg-skill/look reporting success with no file written is `INVALID_TIME_RANGE`
  (non-retryable), never `TOOL_ERROR`.** Measured against ffmpeg-skill 0.9.1: a timestamp landing
  after the last frame actually decodable from a source but still inside its reported `duration`
  (a container's `duration` commonly extends about one frame interval past the last frame's own
  timestamp) makes `look` claim `{"status": "completed"}` while writing nothing. That is a
  permanent fact about the timestamp — retrying the identical request fails identically forever —
  so classifying it as a retryable tool failure would be actively misleading to a calling agent.
  `adapter.extract_frame()` decides this from the output file's actual existence, never from
  ffmpeg-skill's own claim of success (root cause is in ffmpeg-skill; out of scope here — see
  docs/ffmpeg-skill.md).
- **ADR-6 Forbidden-field rejection has its own recursion-depth bound, independent of
  `MAX_METADATA_BYTES`.** `model._reject_forbidden()` walks the raw, not-yet-structurally-validated
  request (including free-form `metadata`, which has no field allowlist of its own) before any
  other check runs. A payload can be small in bytes but deeply nested, so a byte-size cap alone
  does not bound recursion depth; `MAX_NESTING_DEPTH` does, raising a clean `INVALID_REQUEST`
  instead of letting Python's own recursion limit surface as an uncaught `RecursionError`.
- **ADR-7 The CLI's JSON reader catches `RecursionError` explicitly, in the one function every
  entry point shares.** `cli._read_document()` backs `validate`, `render`, `extract-frame` and
  `run -` alike; a deeply nested-but-syntactically-valid payload makes the stdlib `json` decoder
  raise `RecursionError` from inside its own C-accelerated scanner, before ADR-6's model-level bound
  ever gets a chance to run. Fixing it once in the shared reader — rather than in each subcommand —
  is what makes the fix apply to all four entry points at once.
- **ADR-8 Track ffmpeg-skill's contract by exact `contract_version` match ("1.0"), not a semantic
  version range.** Unlike audio-production-skill (which depends on a wide, evolving processing
  surface and therefore pins a version *window*), this skill uses exactly two read-only tools
  (`probe`, `look`) whose flags have been stable since ffmpeg-skill 0.9.1; there is currently no
  known capability gap to track (see docs/ffmpeg-skill.md). An exact `contract_version` match is
  simpler and just as safe for this narrow a surface — revisit if this skill ever needs more of
  ffmpeg-skill's contract.

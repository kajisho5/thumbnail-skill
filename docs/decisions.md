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
- **ADR-5 A dead-zone `look` timestamp (past the last decodable frame, still inside reported
  `duration`) is `INVALID_TIME_RANGE` (non-retryable), never `TOOL_ERROR`.** A timestamp landing
  after the last frame actually decodable from a source but still inside its reported `duration` (a
  container's `duration` commonly extends about one frame interval past the last frame's own
  timestamp) makes the underlying `ffmpeg -ss <timestamp>` decode zero frames and write nothing.
  Measured against ffmpeg-skill 0.9.1, `look` used to claim `{"status": "completed"}` regardless of
  this; as of ffmpeg-skill 0.11.0's "fail loudly" pass, `look` instead verifies its own output and
  reports a hard failure instead — `{"status": "failed", "error": {"kind": "output", "code":
  "OUTPUT_INVALID", "message": "output verification failed: ...: not written"}}` (current behaviour,
  measured against ffmpeg-skill 0.12.2). Either shape describes the same permanent fact about the
  timestamp — retrying the identical request fails identically forever — so classifying it as a
  retryable tool failure would be actively misleading to a calling agent. `adapter.extract_frame()`
  reclassifies ffmpeg-skill's current fail-loudly shape directly, via `run_tool()`'s `reclassify`
  hook (matched on `error.kind == "output"` and a "not written"/"0 bytes" message), and keeps the
  output file's actual existence as a defensive fallback for the pre-0.11.0 claimed-success shape
  (root cause of the underlying decode gap is in ffmpeg-skill; out of scope here — see
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
  known capability *gap* to track (see docs/ffmpeg-skill.md). An exact `contract_version` match is
  simpler and just as safe for this narrow a surface — revisit if this skill ever needs more of
  ffmpeg-skill's contract. Caveat found in practice (see ADR-5): a *behavioural* change to `look`
  landed between ffmpeg-skill 0.9.1 and 0.12.2 (0.11.0's "fail loudly" pass, changing how a
  dead-zone timestamp is reported) with `contract_version` unchanged at `"1.0"` throughout — the
  contract's shape is unchanged, but a specific failure's presentation is not, and `info()`'s
  contract check has no way to see that. There is still no gap in required *capability*, so this
  doesn't change the ADR's conclusion, but pinning by `contract_version` alone does not guarantee
  runtime-behavior stability for the exact failure shapes this skill pattern-matches on; ADR-5's
  reclassification logic is the kind of code that can silently go stale across an ffmpeg-skill
  upgrade with no contract-check signal, and should be re-checked against ffmpeg-skill's CHANGELOG
  when bumping the pinned checkout, not just against `contract_version`.

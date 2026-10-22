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
- **ADR-8 Detect ffmpeg-skill's capabilities; no version window and no exact `contract_version`
  match.** This skill uses exactly two read-only tools (`probe`, `look`) and four `look` flags, so
  what it needs is directly checkable: each emitted flag must appear in the located checkout's
  contract or, failing that, its `--help`. `skill.version` and `contract_version` are reported and
  feed the cache identity, but are never compared, so a new ffmpeg-skill release (including a
  major, or a `contract_version` bump that leaves `probe`/`look` alone) is not refused just for its
  number. Optional behaviour follows the same rule: `--overwrite` is passed only when detected
  (ffmpeg-skill 2.0 refuses to replace an existing output without it; earlier releases reject
  it). Superseded the exact `contract_version` "1.0" match, which could not see that 2.0 change:
  the contract shape was unchanged, yet a re-render with `reuse: false` failed because `look`
  refused to overwrite the deterministic frame file of the previous run. Caveat that still holds
  (see ADR-5): a *behavioural* change to `look` (like 0.11.0's "fail loudly" pass) shows up in no
  contract, which is why CI runs the suite against ffmpeg-skill `main` daily as well as against the
  oldest tested commit (`336e0c4`, 0.12.2); `tests/test_ffmpeg_skill_compat.py` pins the detection
  against fake current, old, contract-less and unseen-version layouts.

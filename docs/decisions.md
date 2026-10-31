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
- **ADR-9 Platform size presets are a named `output.preset`, checked against the written artifact
  after the render; every figure cites the platform's own documentation or says "unverified".**
  `model.PLATFORM_PRESETS` is the one table: the request parser, the post-render check and the
  contract's top-level `platform_presets` (beside `output_formats`, declared the same way) all read it.
  *Where the field lives:* on `output`, not `canvas`, because a preset is a delivery target (format
  and byte-size limits as well as a shape), and because `output`'s fields are not part of the
  contract's `document` section that consumers pin (see "Consumers" below). *Precedence:* the preset
  supplies `canvas.width`/`height` when the document omits both; explicit `width` and `height` win;
  giving only one of the two with a preset is `INVALID_REQUEST` (`partial_canvas_size`) rather than
  a silent mix. Explicit sizes are still held to the preset's limits, so `youtube` accepts 1920x1080
  but refuses 1000x1000. *The check:* after `_validate_output` (real image, declared format, exact
  canvas size), the file's real width/height (min/max per preset, aspect-ratio range by integer
  cross-multiplication, no float tolerance), format and byte size are compared with the preset;
  any violation is `VALIDATION_ERROR` with `details.reason: "preset_limit_exceeded"`, `preset`, and
  every `violations[] {check, limit?, expected, got}` at once. It runs before the artifact is cached
  or placed, so a refused render leaves no output and no cache entry; a cache hit (presets are not
  part of the render identity, since they do not change a pixel) is checked the same way, as is
  `thumbnail/extract_frame`. Checking the artifact rather than the request costs one render on a
  request whose format or shape was already wrong, in exchange for one check that also covers file
  size (unknowable before encoding) and the reuse path. Byte limits use decimal megabytes
  (1 MB = 1,000,000 bytes), the smaller reading, so a file that passes is under the limit either way.
  Researched 2026-10-30; when an official page gives a range or several limits, the narrowest is
  used. A figure no official page states for that exact surface is listed in the preset's
  `unverified` and set conservatively: an unverified byte limit is 2,000,000 (the smallest official
  thumbnail limit found, YouTube's mobile upload limit).
  - `youtube` 1280x720, exactly 16:9, width >= 640, PNG/JPEG, <= 2 MB. Verified,
    <https://support.google.com/youtube/answer/72431> (fetched verbatim): "Have a resolution of 3840
    x 2160 pixels for videos ..., with a minimum width of 640 pixels for videos", "image formats
    such as JPG or PNG", "Mobile: 2 MB for video thumbnails ... Desktop: 50MB", "aspect ratio of
    16:9". 1280x720 is kept as the default size (the roadmap's figure and within the page's limits);
    the larger recommended 3840x2160 is reachable with explicit width/height. The upload device is
    unknown here, so the smaller (mobile) limit applies.
  - `youtube-shorts` 1080x1920, exactly 9:16, height >= 640, PNG/JPEG, <= 50 MB. Verified, same
    page: "2160 x 3840 for Shorts ... a minimum height of 640 pixels for Shorts", "For Shorts, try to
    upload a thumbnail with an aspect ratio of 9:16", and custom Shorts thumbnails "are currently only
    available to add in YouTube Studio on a computer", so only the desktop limit (50MB) applies.
  - `tiktok` 1080x1920, exactly 9:16, at most 1080x1920, JPEG only, <= 2 MB. **Unverified** for the
    cover itself: TikTok's Creator Academy article on covers
    (<https://www.tiktok.com/creator-academy/article/video-covers-thumbnails>) is rendered by
    JavaScript and could not be read directly; only a search index's summary of it (9:16,
    1080x1920) was seen. The one official image spec found is for photo posts, not covers
    (<https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide>, fetched verbatim:
    formats "WebP, JPEG", "Maximum 1080p", "Maximum of 20MB for each image"); hence JPEG only (PNG is
    not listed), the 1080p ceiling, and the conservative 2 MB instead of 20MB.
  - `instagram-reels` 1080x1920, exactly 9:16, width >= 720, PNG/JPEG, <= 2 MB. Partly verified:
    <https://help.instagram.com/1038071743007909> (JavaScript-rendered; its indexed text, not a
    verbatim fetch) says reels may be "between 1.91:1 and 9:16" with a "minimum resolution of 720
    pixels"; the preset takes the vertical end exactly. No official cover-image size, format or byte
    limit was found: those are **unverified** (Meta's Reels ads guide lists video files only).
  - `instagram-feed` 1080x1080, aspect 4:5..1.91:1, width >= 320, PNG/JPEG, <= 2 MB.
    <https://help.instagram.com/1631821640426723> (indexed text): photos keep their resolution at a
    width of 320 to 1080 pixels and an aspect ratio between 1.91:1 and 3:4 (an older copy says 4:5;
    the narrower 4:5 is used). Byte limit **unverified** for organic posts; the only official figure
    is Meta's ads guide (<https://www.facebook.com/business/ads-guide/update/image/instagram-feed>:
    "JPG or PNG", "Maximum File Size: 30MB", "Minimum Width: 500 pixels"), which is for ads, so 2 MB
    is used.
  - `x` 1600x900, exactly 16:9, PNG/JPEG, <= 5 MB. Format and size verified,
    <https://docs.x.com/x-api/media/quickstart/best-practices> (fetched verbatim): "Supported image
    media types: JPG, PNG, GIF, WEBP", "Image size: <= 5 MB". Size and aspect **unverified**: no
    official page names 1600x900; X's ads specs (help.x.com, refused automated fetches) are reported
    to recommend 1920x1080 for 16:9. 1600x900 is the requested size and is 16:9.

  *Consumers:* video-production-agent pins this contract (`contract_0.1.3.json`) and gates on
  `FATAL_DRIFT_KEYS` (`schema`, `skill_id`, `kind`, `execution`, `document`, `output_formats`, `fonts`,
  `rendering`, `schema_versions`, `errors`, `provenance`, `deterministic`, plus each tool's
  declaration). The only contract change is the new top-level `platform_presets`, which none of those
  covers, so the pinned contract reports no drift and no breaking drift. The response gains `preset`
  (and `provenance.preset`) only when a request names one, which the agent never does today.
- **ADR-10 The Pillow floor depends on the Python version, because the release that clears every
  advisory does not install on all supported Pythons.** `Pillow>=10.0` allowed releases with 50+
  published advisories (OSV, 2026-10-30; several CRITICAL heap overflows in image decoders, which
  this skill runs on caller-supplied images). Pillow 13.0.0 is the first release with none, but it
  requires Python 3.11, and this skill supports and tests 3.9. Dropping 3.9 to raise the floor
  would be a breaking change made for a dependency, so the floor is split with environment markers:
  Python 3.11+ gets `Pillow>=13.0.0`; Python 3.10 gets `>=12.3.0` (the last 3.10 release, which
  still carries the advisories fixed in 13.0.0); Python 3.9 gets `>=11.3.0` (the last 3.9 release,
  which also carries those fixed in 12.2.0/12.3.0). Pip already picks the newest compatible
  release, so this only raises the minimum; anyone who needs the fully patched Pillow runs Python
  3.11+.
- **ADR-11 The version lives only in `pyproject.toml`; `contract_version` and the ffmpeg-skill
  `dependencies` entry are derived from the constants they describe.** (`ECOSYSTEM_ROADMAP.md` step 1.)
  *Version:* `_version()` already read this checkout's `pyproject.toml`, the only file
  `release.yml` bumps. The installed-copy fallback globbed `thumbnail_skill-*.dist-info/METADATA`
  over `sys.path`, which picks in lexicographic order, so `0.10.0` comes ahead of `0.9.0`. It now
  asks `importlib.metadata.version("thumbnail-skill")`. The full rule, the same as qc-skill's
  ADR-020 and motion-graphics-skill's ADR-21, is:
  1. This checkout's `pyproject.toml`, used only if its `name` is `thumbnail-skill`. This covers an
     editable install too, whose recorded metadata goes stale on the next bump.
  2. Otherwise the installed wheel's metadata, built from that same file.
  3. Otherwise `0.0.0+unknown`, never a plausible but wrong number.

  The release's inline bump moved into `.github/scripts/bump_version.py`. It still writes only
  `pyproject.toml`, then imports the package from `src/` and fails the release, before anything is
  committed or tagged, unless `VERSION` equals the new version. It imports only the package, not
  the contract, because the release job installs no Pillow. `tests/test_version_source.py` checks
  that the contract and the CLI follow `VERSION`, and it runs the script on a copy of the checkout.
  Not chosen: bumping a second constant as well, as audio-production-skill's
  `bump_version_files.sh` does. Two copies stay correct only while a second regex keeps matching.
  *Contract:* two new top-level keys.
  - `contract_version` is `str(CONTRACT_SCHEMA_VERSION)`, i.e. `"1"`, the same value as
    `schema_versions.contract` and the `@1` in `schema`. It is a string because this contract's other
    version fields are strings; the OS schema accepts either type.
  - `dependencies` lists ffmpeg-skill, in the OS `dependencies[]` shape that audio-production,
    color-grading and motion-graphics use: `version_range` `">=1.0"` of its `contract_version`. That
    value is derived from `adapter.SUPPORTED_CONTRACT_VERSION`, the same constant
    `ffmpeg_skill.contract_version` already reports.
    - The range is a lower bound only: the OS schema says Skills do not cap the upper version, and
      ADR-8 says there is no version window.
    - It is never compared at run time; the flags are still detected.
    - `required` is `false`, because only a `video_frame` asset or `extract_frame` needs ffmpeg-skill.
    - `for` lists the tool specs that delegate, and `tools` lists `TOOLS_USED`.

  Nothing else in the contract changed.
  *Consumers:* video-production-agent's thumbnail adapter compares `DRIFT_KEYS` (`schema`,
  `skill_id`, `version`, `kind`, `execution`, `document`, `output_formats`, `fonts`, `rendering`,
  `ffmpeg_skill`, `schema_versions`, `errors`, `provenance`, `deterministic`). Neither new key is in
  that list, and `check_contract()` reads neither, so this is no drift and no breaking drift. Its
  version gate is `("0.1.",)`, so this ships as a patch release, never with a `feature` label.

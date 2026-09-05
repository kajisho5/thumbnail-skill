# Security

## Execution boundary

- `shell=False` always; the only `subprocess.Popen` call in this codebase is in `adapter.py`, with
  an argv list built entirely from validated numbers and resolved absolute paths.
- The only executables ever started: `sys.executable` (this same Python interpreter) running
  `<ffmpeg-skill checkout>/scripts/probe.py` or `.../scripts/look.py`. The checkout directory comes
  from an explicit option, an environment variable, or a small list of well-known paths — never from
  the request document.
- ffmpeg-skill itself resolves `ffmpeg`/`ffprobe` from `PATH`; this skill never names or configures
  a codec, filter, or encoder — it uses exactly `look --at <timestamp> --no-timecode`, nothing else.
- No network access anywhere in this skill.
- No JavaScript, HTML or CSS interpreter anywhere; those field names (`eval`, `html`, `css`,
  `javascript`) are on the forbidden-field list and rejected even before validation of anything else.
  Text is always drawn as literal glyphs (`PIL.ImageDraw.text`), never evaluated as markup or code.

## Forbidden fields

`model.FORBIDDEN_KEYS` is checked recursively (`_reject_forbidden`) over the entire request,
including the free-form `metadata` object which otherwise has no field allowlist of its own:
`command`, `commands`, `argv`, `args`, `cmd`, `shell`, `exec`, `executable`, `script`, `filter`,
`filters`, `filter_complex`, `vf`, `af`, `ffmpeg`, `env`, `cwd`, `eval`, `html`, `css`, `javascript`.
Every other field in the document is validated against an explicit allowlist (`model._obj`), so an
unrecognised field is rejected either way — the forbidden list exists to give these specific,
security-relevant names a distinct, greppable `reason: forbidden_field` regardless of where they
appear or whether the surrounding object happens to be free-form.

## Path policy (`security.PathPolicy`)

Vocabulary:

| term | meaning |
|---|---|
| input | an asset's `path`; untrusted |
| allowed root | a directory `options.allowed_input_roots` declares as the only place inputs may come from; unset means "any readable regular file" (unrestricted mode, matching transcription-skill's default) |
| workspace | the directory every write (`output.path`, the reuse cache, temp files) must resolve inside |

Rules, in order, for an input:

1. The path is resolved to an absolute, symlink-followed path (`Path.resolve(strict=True)`).
   A path that doesn't exist is `INVALID_INPUT` (not `PATH_NOT_ALLOWED`: existence and authorisation
   are different facts).
2. If `allowed_input_roots` is set, the *resolved* path must sit inside a *resolved* root by path
   components (`Path.relative_to`) — never by string prefix, so `/w/media` never authorises
   `/w/media_evil`. A symlink whose target resolves outside the root fails the same check; there is
   no separate "symlink" code path to bypass.
3. The resolved path must be a regular file, and readable.

Rules for a write (`output.path`, the reuse cache under
`<workspace>/.thumbnail-skill/cache`, temp files under `<workspace>/.thumbnail-skill/tmp`):

1. A literal `..` path component anywhere in the given string is refused outright
   (`PATH_NOT_ALLOWED`, reason `traversal`) before any resolution happens.
2. The target is resolved against the deepest existing ancestor (so a symlinked directory earlier
   in the path cannot redirect the write) and must land inside the resolved workspace.
3. Every path component that does not exist yet is checked by `check_filename`: no NUL or control
   characters, no `<>:"|?*`, not `.`/`..`, not longer than 255 bytes, not a Windows reserved device
   name (`CON`, `PRN`, `COM1`.. `LPT9`), no trailing space or dot, and does not start with `-`
   (which could otherwise be parsed as a flag if this value ever reached a CLI — it never does, but
   the check costs nothing and closes the class of bug outright).
4. An existing path that isn't the right kind of thing (a file where a directory was expected, or
   the reverse) is refused; an existing output file is refused unless `overwrite: true`.

## Image decoding

- `Image.MAX_IMAGE_PIXELS` is capped (64,000,000) so a decompression-bomb-scale source image raises
  a structured `INVALID_INPUT` instead of exhausting memory.
- Every image is opened, `.load()`ed (forcing full decode, not just header parsing) and closed after
  use; `UnidentifiedImageError`/`OSError` map to `INVALID_INPUT`.

## What a request can never do

- Name a font by path (only a registered `font_id`; see `fonts.py`).
- Choose which ffmpeg/ffprobe binary runs, or pass it any flag beyond a validated timestamp.
- Cause a write outside `workspace`, or a read outside `allowed_input_roots` when declared.
- Cause this skill to evaluate anything as code (no `eval`, no template engine, no markup renderer).

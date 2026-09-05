---
name: thumbnail-skill
description: Deterministic thumbnail rendering execution Skill for the AI Video Production Ecosystem. Use it when a caller (normally video-production-agent) has already decided exactly what a thumbnail should show - which still image or which explicit video timestamp, what text, what layout - and needs it rendered safely to PNG/JPEG with provenance. Do NOT use it to choose what a thumbnail should show, pick a "best" video frame, detect faces, generate a title, judge design quality, or run arbitrary ffmpeg/image commands (it refuses them); those decisions belong to the caller.
---

# thumbnail-skill

Machine interface: `thumbnail run - --json` with a `{"tool": "thumbnail/<name>", "params": {...}}`
document on stdin; exactly one response document on stdout. `thumbnail skill --json` prints the
contract, `thumbnail doctor --json` the environment (fonts, ffmpeg-skill, path policy).

Tools: `thumbnail/validate` (structural check only), `thumbnail/render` (canvas + assets + elements
-> PNG/JPEG), `thumbnail/extract_frame` (one video timestamp -> one frame, nothing else).

Rules for a calling agent:
1. Decide everything about content before calling this skill: which still image, or which exact
   video timestamp (`asset.timestamp`, seconds); what the text says (with literal `\n` for line
   breaks — this skill never wraps or reflows text); where every element sits (`position`, `size`,
   `z_index`). This skill renders exactly what it is given and searches for nothing.
2. Reference fonts by `font_id` from the registry (`thumbnail skill --json` -> `fonts.font_ids`, or
   `thumbnail doctor --json` for what actually resolves on this machine). A request never carries a
   font path; an unresolvable `font_id` fails `MISSING_INPUT`, never a silent substitution.
3. Never send `command`, `argv`, `shell`, `filter`/`filter_complex`/`vf`/`af`, `executable`, `env`,
   or HTML/CSS/JS: the request is rejected with `INVALID_REQUEST` wherever such a key appears.
4. Keep outputs inside the workspace (`--workspace` / `options.workspace`), never at an input path;
   set `overwrite: true` deliberately. Restrict inputs with `options.allowed_input_roots` when the
   caller's own inputs come from an untrusted or shared location.
5. Read `output`, `sha256`, `width`/`height`, `reused` and `provenance` from the response; the exit
   code is 0 only when `ok` is true. A `video_frame` asset needs ffmpeg-skill reachable (env var
   `THUMBNAIL_SKILL_FFMPEG_SKILL_DIR` or `--ffmpeg-skill`); a still-image-only document never needs it.

See README.md for the ThumbnailDocument schema, element/font/output tables, error codes, security
boundary and the responsibility split with video-production-agent and the other media skills.

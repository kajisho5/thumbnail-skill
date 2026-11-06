# Roadmap — thumbnail-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

0.1.9, version single-sourced (step 1). Python + Pillow. validate, render (ThumbnailDocument), extract_frame, platform presets (step 2), optional generate_background (step 3), batch from a CSV (step 5), choose_frame and place_text (step 6), render --variants with the small-size check (step 7), render --layers (step 8). CI on 3 OSes.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| サムネイル | done | deterministic composition; the AI background engine named in ecosystem.json (FLUX.2 [klein] 4B, ADR-012) as the optional `thumbnail/generate_background` ([#20](https://github.com/kajisho5/thumbnail-skill/pull/20)) |

## Wiring

Agent uses extract_frame and render with the full ThumbnailDocument: up to four texts, a logo, colours (step 4, video-production-agent#100). Pinned at 0.1.8. Delegates to ffmpeg-skill probe, look.

## Steps

- [x] **1. Single-source the version + contract_version/dependencies** — version from pyproject.toml only; `contract_version` and `dependencies` (ffmpeg-skill `>=1.0`) from their constants (ADR-11, [#19](https://github.com/kajisho5/thumbnail-skill/pull/19))
  - Done when: contract version == pyproject
- [x] **2. Platform size presets + output check** — YouTube 1280x720 etc. ([#17](https://github.com/kajisho5/thumbnail-skill/pull/17))
  - Done when: test per preset
- [x] **3. Optional AI background provider** — FLUX.2 klein 4B behind doctor detection; provenance records engine and licence ([#20](https://github.com/kajisho5/thumbnail-skill/pull/20), ADR-12)
  - Done when: absent model = doctor DEGRADED and a refusal, not a crash
- [x] **4. (agent side) full ThumbnailDocument** — logo image, several text elements ([video-production-agent#100](https://github.com/kajisho5/video-production-agent/pull/100))
  - Done when: tracked in video-production-agent
- [x] **5. Batch thumbnails from a CSV** — one ThumbnailDocument template plus a CSV (title, speaker, photo, session) renders one thumbnail per row in 16:9 and, when asked, 9:16; names that do not fit are shrunk to a stated minimum, never cut. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：学会の全セッション分や連載回のサムネイルを一度に揃えられる
  - Done when: a 10-row fixture writes 20 files at the platform sizes, each passing the existing output check
  - Done (ADR-14): `thumbnail/render_batch` takes one template per `output.preset` (16:9 `youtube` and 9:16 `youtube-shorts` are two targets, each with its own layout) and a CSV (UTF-8 by default, `cp932` opt-in for Japanese Excel) or inline rows, with `{{column}}` placeholders. `text_fit` shrinks a text element one point at a time down to `min_font_size` until its painted box fits, and never cuts or wraps it; a row that cannot fit is refused before anything is written. Each file is an ordinary `thumbnail/render`. `tests/test_batch.py`: the 10-row fixture with two targets writes 20 files, each at its platform size and passing the preset check ([#25](https://github.com/kajisho5/thumbnail-skill/pull/25))
- [x] **6. Frame choice and face-aware layout** — candidate frames ranked by measured facts (sharpness and exposure from media-analysis-skill, eyes open and face size from detection-skill landmarks when present); text placed away from the chosen face
  - なぜ要るか：目をつぶったコマや顔に文字がかかったサムネイルを避けられる
  - Done when: a fixture with a blinking frame and a sharp frame ranks the sharp one first; the title box never overlaps the face box
  - Done (ADR-15): `thumbnail/choose_frame` ranks a detection-skill `detections@1` face document (eyes open, face size and angle from its landmarks and attributes) with the caller's frame sharpness and exposure; a closed-eyes frame ranks after every open one. `thumbnail/place_text` moves the title to the first position whose painted box clears every face, or refuses. `tests/test_frames.py`: the sharp open frame ranks first; on a grid of face positions the title box never overlaps the face box, geometrically and on rendered pixels ([#26](https://github.com/kajisho5/thumbnail-skill/pull/26)). Caveat outside the Done when: media-analysis-skill does not publish frame sharpness or exposure (its ADR-029 lists them as a gap), so those come from the caller here; the detections fixture is synthetic, in detection-skill's published shape, not a live run
- [x] **7. A/B variants and the small-size check** — `render --variants N` writes N documented variants (layout, colour, frame) of one thumbnail, and each is checked at the size it is seen on a phone (text height in pixels at 320 px wide, contrast through qc-skill text legibility)
  - なぜ要るか：YouTube のサムネイル A/B テストに出す案を、文字が小さすぎる案を除いたうえで用意できる
  - Done when: three variants are written and one with 10 px text at phone size is reported, not dropped
  - Done (ADR-16): `thumbnail render --variants N` (`thumbnail/render_variants`) writes `<stem>-v1..vN`. Variant k takes the k-th layout, palette and frame round robin, and variant 1 is the original; every choice is listed. Each variant is checked at 320 px wide: glyph ink height and contrast through qc-skill's `video.text_regions`. A variant below a minimum is flagged and kept. `tests/test_variants.py`: three variants are written and the 10 px one is flagged `text_too_small`, not dropped ([#27](https://github.com/kajisho5/thumbnail-skill/pull/27)). Caveat: the 12 px default minimum is this skill's heuristic, because no platform publishes one
- [x] **8. Layered output** — each composed layer (background, photo, text, logo) also written as a PNG with alpha and a manifest (position, size, order) for a designer to rebuild it
  - なぜ要るか：デザイナーが文字だけ直したいときに、最初から作り直さずに済む
  - Done when: the layers composited by the manifest equal the thumbnail pixel for pixel
  - Done (ADR-17): `thumbnail render --layers DIR` (`thumbnail/render_layers`) records the renderer's own composites. It writes the background, each image (photo, logo) and each text, with its background band as a separate layer, as a PNG with alpha cropped to its pixels, plus `layers.json` (order, role, element id, position, size). Before answering it rebuilds the image from the files on disk and checks it against the thumbnail. `tests/test_layers.py` rebuilds with its own code and gets the written PNG byte for byte, with opacity, rotation, negative positions, stroke and shadow; for JPEG it compares against the lossless composite ([#28](https://github.com/kajisho5/thumbnail-skill/pull/28))

## Boundaries

imagemagick `preset youtube-thumbnail` and remotion `still` can also produce thumbnails; ownership stays here.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).

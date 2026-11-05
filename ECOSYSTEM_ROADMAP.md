# Roadmap — thumbnail-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

0.1.9, version single-sourced (step 1). Python + Pillow. validate, render (ThumbnailDocument), extract_frame, platform presets (step 2), optional generate_background (step 3). 95 tests, CI on 3 OSes.

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

## Boundaries

imagemagick `preset youtube-thumbnail` and remotion `still` can also produce thumbnails; ownership stays here.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).

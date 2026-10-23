## Summary

<!-- What changed and why. Link an issue if there is one. -->

## What this does NOT do

<!-- Optional. Call out scope boundaries explicitly when it isn't obvious from the Summary
     (e.g. "no behavior change", "docs only", "no new tools/capabilities"). -->

## Test plan

- [ ] `python -m pyflakes src tests`
- [ ] `python -m pytest -q`
- [ ] (if `video_frame`/ffmpeg-skill paths are touched) verified against a real ffmpeg-skill
      checkout, not mocked
- [ ] New/changed behavior has a regression test

<!--
Labels drive both the changelog section and the version bump for the next automated release
(see .github/release-drafter.yml). One is applied automatically where the title/branch/files
match; add or correct it manually if needed:
  major         breaking change
  feature       new capability (minor)
  fix           bug fix (patch)
  security      security-relevant fix (patch)
  documentation docs only (patch)
  chore         maintenance, no behavior change (patch)
  (none)        still ships, counted as patch
-->

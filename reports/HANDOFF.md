# Weekly Safety Walk Handoff

Date: 2026-06-19

## Summary
- Replaced the Telegram-driven workflow with a local folder-based runner.
- New entrypoint: `build_local_ha_report.py`
- Launchers:
  - `Start-HA-Local-Report.ps1`
  - `Start-HA-Local-Report.cmd`

## Current Workflow
1. Put photos in `Photo\YYYYMMDD`.
2. Run the local report builder with the same `YYYYMMDD`.
3. The builder creates a suggested `photo_mapping.suggested.json`.
4. If any issue is marked `needs-review`, visually inspect the photos against the issue/action text and add `photo_mapping.json`.
5. The builder generates PDF and Excel into `outputs\Test` by default.

## Current Output
- `20260512` rebuild completed to `outputs\Test\Site Safety and Environment Walk No.80(12-05-2026).xlsx`.
- `20260512.pdf` also regenerated in `outputs\Test`.
- Gmail was reauthorized successfully and the rebuild now works again.
- `20260519` rebuild completed to `outputs\Test\Site Safety and Environment Walk No.81(19-05-2026).xlsx`.
- `20260519.pdf` also regenerated in `outputs\Test`.
- `20260528` local rebuild completed to `outputs\Test\Site Safety and Environment Walk No.82(28-05-2026).xlsx`.
- `20260528.pdf` also regenerated in `outputs\Test`.
- `20260616` local rebuild completed to `outputs\Test\Site Safety and Environment Walk No.85(16-06-2026).xlsx`.
- `20260616.pdf` also regenerated in `outputs\Test`.

## Important Notes
- Gmail remains the source for issue/action text.
- Telegram is no longer required for the main workflow.
- `photo_mapping.json` is the intended manual override point.
- `photo_mapping.suggested.json` now includes `method` and `reason` for each proposed pair.
- Photo placement must be based on issue/action text and visual photo contents, not filename timestamps.
- GitHub should stay code/config only. Do not push secrets, outputs, or photos.

## Recent Fixes
- Updated `weekly-safety-walk` skill with the latest workflow fixes: output-folder report sequencing, template-detected Cover date/signature rows, template-detected Rectification photo rows, hidden unused photo blocks, and final verification checks.
- Fixed report number sequencing for local output runs: `find_report_no()` now considers the target output directory, so `20260611` follows `20260604` as No.84.
- Fixed `20260604` manual photo mapping: issue 1 now uses `105503 -> 105449`; issue 2 now uses `105303 -> 105325`.
- Fixed HA email parsing for combined photo references such as `Photo 1 & 2):`.
- Fixed local photo collection to accept extensionless JPEG files, covering WhatsApp-style files such as `來自Hei的相片`.
- Added `Photo\20260616\photo_mapping.json`: issue 1 now uses `IMG-20260617-WA0030.jpg -> 來自Hei的相片`.
- Added action wording conversion for `should be removed ...` so `20260616` outputs use rectified action text.
- Updated the `weekly-safety-walk` skill with stricter BEFORE/AFTER photo-placement guardrails: define defect-visible BEFORE versus rectification-visible AFTER, require a one-line evidence note before manual mapping, forbid assumptions from zoom/framing/brightness/file order, ask the user when evidence is ambiguous, and verify regenerated Excel against `photo_mapping.json`.
- Fixed Cover sheet signature/date placement by detecting the template date rows instead of writing to fixed rows; duplicate signer labels and stray date rows are cleared.
- Fixed Rectification sheet filling by detecting each template's `Photo No.` rows; this fixes bi-weekly templates with different row offsets and hides unused blank photo blocks.
- Removed timestamp-split fallback from `build_local_ha_report.py`; unresolved issues now become `needs-review` and require visual mapping before final output.
- Updated the `weekly-safety-walk` skill to require Codex to inspect issue/action text and photo contents instead of relying on photo time order.
- Added `Photo\20260528\photo_mapping.json` so No.82 uses the correct per-issue before/after photo pairs.
- Reapplied the Cover sheet inspection table borders after writing inspection people, fixing the missing right-side border on merged designation cells.
- Fixed Cover sheet signature block duplicate name/title text; signer labels now stay on the parenthesized row and the row below is cleared.
- Added description-aware photo pairing in `build_local_ha_report.py`.
- Current description-aware rules cover only issues with clear visual signals, currently paving-block cover/plastic-sheet and generator/fire-extinguisher cases.
- Updated the `weekly-safety-walk` skill to require review of suggested pairing method/reason and to prefer code-rule fixes for repeatable pairing errors.
- Cover sheet inspection people now use the expected default names.
- Unused follow-up rows are hidden so there are no blank rows.
- Weekly and bi-weekly signature blocks are cleaned up and normalized.
- Bi-weekly uses `Patrick, P. T. KO / CE/T243` for the extra signature block.

## Verification
- Final skill static validation passed with fallback frontmatter checks: `name,description` only, description length 341 chars, required verification phrases present.
- `quick_validate.py` and `package_skill.py` are blocked on this machine by missing Python module `yaml`; manual fallback validation and manual `.skill` zip packaging were used instead.
- Packaged final skill at `G:\我的雲端硬碟\Codex-System\skills\weekly-safety-walk.skill`; package contents verified: `SKILL.md` and `agents/openai.yaml`.
- Fresh rebuilds completed after skill update: `20260604 -> No.83`, `20260611 -> No.84`.
- `20260604` rebuilt after photo mapping fix; embedded Excel image order verified:
  `105503 -> 105449`, `105303 -> 105325`.
- `20260611` rebuilt after Rectification/Cover fixes; embedded Excel image order verified:
  `110314 -> 110320`, `111522 -> 111636`.
- `20260611` report number verified as No.84 in `outputs\Test\Site Safety and Environment Walk No.84(11-06-2026).xlsx`; stale No.83 copy for the same date was removed.
- `20260611` Rectification sheet verified: issue 2 writes to the bi-weekly template's row 41/42/43 block, and unused rows 47-68 are hidden.
- `20260604` and `20260611` Cover sheets verified: signer labels are on the parenthesized row, duplicate rows are cleared, and date values are on the Date line.
- Trial run completed for `20260604`.
- Trial run completed for `20260611`.
- Local rebuild completed for `20260604` to `outputs\Test\20260604.pdf` and `outputs\Test\Site Safety and Environment Walk No.83(04-06-2026).xlsx`.
- Local rebuild completed for `20260611` to `outputs\Test\20260611.pdf` and `outputs\Test\Site Safety and Environment Walk No.84(11-06-2026).xlsx`.
- Local rebuild completed for `20260616` to `outputs\Test\20260616.pdf` and `outputs\Test\Site Safety and Environment Walk No.85(16-06-2026).xlsx`.
- `20260616` embedded Excel image order verified:
  `IMG-20260617-WA0030.jpg -> 來自Hei的相片`.
- `20260616` Gmail action text verified:
  `The bucket at the upper wailing of trench has been removed for safety of work at height.`
- `weekly-safety-walk.skill` repackaged after BEFORE/AFTER guardrail update; package contents verified: `SKILL.md` and `agents/openai.yaml`.
- Skill quick validation remains blocked by missing Python module `yaml`; fallback frontmatter and packaged-content checks passed.
- Python compile check passed for `ha_walk_excel.py`, `build_local_ha_report.py`, and `gmail_ha_actions.py`.
- `20260512` fresh rebuild verified in `outputs\Test`.
- `20260519` fresh rebuild verified in `outputs\Test`.
- `20260528` fresh rebuild verified in `outputs\Test`.
- `20260528` embedded Excel image order verified:
  `105517 -> 105503`, `111003 -> 110920`, `111440 -> 111414`.
- `20260528` Cover sheet inspection table right border verified: `N11:N16` all have thin right borders.
- `20260519` embedded Excel image order verified:
  `100520 -> 20260520_105307`, `101826 -> 101914`, `102148 -> 102224`.
- `20260519` Cover sheet verified: `C27/H27/M27` are blank after regeneration.

## Shutdown Status
- Current shutdown prepared after running `20260616`, fixing combined HA photo-reference parsing, supporting extensionless JPEG collection, adding the reviewed `20260616` photo mapping, and strengthening the skill's BEFORE/AFTER placement rules.
- `build_local_ha_report.py` no longer uses timestamp-split fallback; unresolved photo placement becomes `needs-review`.
- `weekly-safety-walk` skill confirmed updated in `G:\我的雲端硬碟\Codex-System\skills\weekly-safety-walk\SKILL.md`.
- Obsidian note confirmed updated at `G:\我的雲端硬碟\secondbrain\Projects\Weekly Safety Walk\工作筆記.md`.
- Final packaged skill is at `G:\我的雲端硬碟\Codex-System\skills\weekly-safety-walk.skill`.
- No secrets, photos, generated outputs, Gmail tokens, credentials, logs, or local assistant folders should be staged.

# CLAUDE.md - AutoEditor (v7.5.0)

Guide for Claude Code working in this repository. Read this first, then the code. The user-facing manual is
`README.md`; this file is for the developer/agent.

## 1. What this is

AutoEditor is a **local Windows desktop app** (Python 3.11/3.12, PySide6, FFmpeg, faster-whisper) that turns a
voiceover + a folder of images/clips into a finished YouTube-style video: transcription → scene timing →
visuals with motion → transitions → burned-in captions → SFX + ducked music → one verified MP4.

Owner: Badrul (faceless YouTube/Facebook creator). Install path on his PC:
`C:\Users\marji\Downloads\Tools\AutoEditor` (setup with `setup.bat`, run with `run.bat`, venv in `venv\`).
Development so far happened on Linux; **nothing has been run on Windows by the developer** - be careful with
paths (`os.path`, never hard-coded `/`), subprocess flags (`util.popen_kwargs()` hides console windows), and
fonts.

### Non-negotiable product rules (the user asked for these explicitly)
- **Local-first.** No cloud uploads, no paid APIs, no network calls unless the user explicitly enables a
  configuration (only the optional Stock tab talks to Pexels/Pixabay, with the user's own keys stored in the OS
  keyring via `keyring`, never in project files). Don't assume a consumer Gemini/ChatGPT subscription gives API access.
- **Never modify or delete source media.** Renders go to the output folder; intermediates in `_autoedit_work`
  or `cache/`.
- **Never change the narration**: no speed change, no cutting words, no stretching to fit instructions.
- **Honesty**: never silently ignore an instruction or fake a feature. Unknown/unsupported → listed as "not
  understood"/"unavailable - NOT applied". No similarity scores shown as probabilities. Don't claim something
  works until a test proves it. Report limitations plainly.
- **One export action = one video file** in the chosen format (batch only when the user turns it on). Default
  for new projects: 16:9, 1920×1080, 30 fps, MP4 H.264/AAC, "Standard" quality.
- **Preserve old projects**: explicit saved settings win; migration in `exportcfg.migrate` / `project.load`.
- Out of scope (user decided): TTS, voice cloning, podcast generation, AI clipping/viral detection, face
  tracking. Never help evade platform AI-detection.
- The user is not a programmer: UI text must be plain language; every change must be undoable; errors must say
  what to do.

## 2. Run, test, lint

```bash
pip install -r requirements.txt          # faster-whisper PySide6 opencv-python numpy Pillow imageio-ffmpeg keyring
python app.py                            # GUI
python autoedit_cli.py analyze --voice vo.mp3 --media imgs --scene-mode smart --out p.json
python autoedit_cli.py render p.json [--package]
python -m pyflakes app.py autoedit tests tools autoedit_cli.py     # must stay clean
QT_QPA_PLATFORM=offscreen python -m tests.<name>                   # each test prints "... OK" at the end
python -m tools.bench_render OUT_DIR [--quality Standard|High|Draft] [--x264 preset:crf]
python -m tools.compare_video test.mp4 reference.mp4                # PSNR/SSIM + stream durations
```
FFmpeg: `util.ffmpeg_exe()` uses `ffmpeg` on PATH, else the binary from `imageio-ffmpeg`. There is **no
ffprobe** dependency - `mediainfo.probe()` parses `ffmpeg -i` output.

### Test suite (all must pass before delivering; run each; ~10 min total on 2 cores)
| test | what it proves |
|---|---|
| test_core | scenes, captions, effects, chapters, quality report |
| test_sfx | SFX trigger → plan → mix → audible in the final MP4 |
| test_codec_fallback | clips OpenCV can't decode (codec_id=61 PNG .mov) preview via proxy |
| test_cues | [1] markers, CSV cues, Match-images dialog, directions box |
| test_stock | fake Pexels/Pixabay server: keys, errors, cache, download, add to scene |
| test_gui_state | GUI never changes data by accident; undo; layout; preview sizing |
| test_planner | smart planning: meaning match via prompts, missing visual flagged, rename-invariance, locks |
| test_v7_export | real exports: 1920×1080 SAR 1:1, Fill no borders, preview==export PSNR, fit modes, border detection, one file, no overwrite, metadata/captions all aspects, GPU fallback, cancel/missing/failed-verify |
| test_v7_gui | Script/Instructions/Review/Framing/Export tabs, migration |
| test_v6_render | end-to-end render (keyframes, V2, crossfade, SFX loudness, music ducking, draft vs final) |
| test_edit_instructions | editing-instruction checks 1-10 on real exports (SFX within 1 frame, gain/fade measured, B-roll interval, transitions no drift, locks, stale narration, JSON/CSV import) |
| test_instructions_gui | the Editing instructions window (F8) + guidelines |
| test_timed_plan | timed edit plan: template, line errors, exact build, real export, window |
| test_scene_sheet | SCENE-block sheet format, strict None defaults, GENERAL RULES export format, write-back |
| test_editing_plan | one Editing plan panel; compact/key=value/Markdown/CSV/JSON/scene-block/notes/guidelines → one schema (incl. the user's failing Markdown row `\| 23 \| 023.jpg \| 01:56.84 \| 02:06.76 \| 9.92 \|`); explicit None = off; templates parse; broken plan never falls back to file order; strict 3-scene plan: last/first frame at every cut correct in PREVIEW and EXPORT; revision + diagnostic plan; save/reload/replan stable; table edit → plan text; Apply changes the timeline AND exported frames |

`tests.test_sfx` and `tools/make_sfx.py` also need `scipy` (not needed by the app itself). On a bare Linux
box Qt needs system libraries such as `libegl1`, `libgl1`, `libxkbcommon0`, `libfontconfig1`.

Tests build their own fixtures (tone "voices", noise/tinted images, ffmpeg lavfi clips) - no network, no
Whisper. Use `AE_SHOTS=<dir>` to save GUI screenshots and **look at them** after UI changes.

## 3. Architecture (data flow)

```
voiceover ─► transcribe.py (faster-whisper, cached <voice>.<model>.words.json)
script     ─► transcribe.apply_script (caption spelling), planner.script_check (script vs recording)
            ▼
project.replan()  ── scene_mode ──►  timed  : timedplan.build()   (user's plan; REQUIRED; default for new projects)
                                     smart  : planner.plan()       (topics + meaning via descriptions)
                                     auto / match_media / timestamps / script_cues : scenes.py / cues.py (classic)
            ▼  then: instructions.reapply_after_replan()  (explicit per-scene instructions win), keep_ids()
project["scenes"] / ["broll"] / ["sfx_events"] / ["overlays"]   = THE timeline (authoritative render plan)
            ▼
engine.Compositor.frame_at(t)  ← used by BOTH the preview (preview=True, SeekVideoSource) and the export
            ▼
render.render_all → preflight (missing files + instructions.validate fatal) → build_audio (audio.mix) →
_encode (raw rgb24 pipe → ffmpeg, temp file in _autoedit_work) → mediainfo.check_export → os.replace to final name
```
Key principle: **intent vs result.** What the user asks for lives in `project["edit_instructions"]`
(per-scene records) / `project["timed_plan"]` (text); resolving it writes concrete values into the timeline
with `scene["provenance"]` saying which instruction produced each value. Preview and export never invent
anything - they only draw the timeline.

## 4. Module map (`autoedit/`)

| module | role |
|---|---|
| `project.py` | project JSON: `new_project`, `load` (+migration), `save`, `analyze`, `replan`, `keep_ids`, `estimate_words` (flagged fallback when speech recognition fails), `project_media` |
| `transcribe.py` | faster-whisper word timestamps; `apply_script` corrects spelling from the script |
| `timedplan.py` | **Editing plan** (stored in `project["timed_plan"]`). `parse_entries` turns every format into one schema: compact lines (`0:00 - 0:04 \| 001.png \| slow zoom in, crossfade`), `key=value` extras (`motion=zoom_in`), Markdown tables (header + separator rows, `COLUMNS` synonyms), CSV with a header, versioned JSON (`{"version": 1, "guidelines", "scenes"}`), a PROJECT GUIDELINES section (`_guidelines`: format + motion_level/transition/fit overrides applied in `build`, the rest listed NOT applied), **scene sheets** (`SCENE 001` blocks → `parse_sheet`), and note lines (`- **065.jpg:** …`, `Scene 12: …`) attached as notes (never timing rows, never applied as edits). Start/end are authoritative; a duration is only checked. Empty cell = inherit, `none` = off (`_col_phrase`, `NONE_PHRASE`). `parse_plan` (rows, problems), `TEMPLATES`, `diagnostic_plan` / `save_diagnostic_plan` / `timeline_revision` (logged by `render_all`, which also writes `_autoedit_work/last_export_plan.json`), `edit_entry` / `plan_as_table` / `_sheet_set` (review-table edits → plan text), `build`, `template`, `sheet_template`, `export_sheet`, `problems_for_export` |
| `instructions.py` | per-scene editing instructions: records, deterministic NL parser `parse()`, `resolve()` (words→times, names→files, choices for ambiguity), `apply()` (writes timeline; `placed=True` fast path), `place()` (carves the timeline, conflicts), `validate()` (fatal vs warning), JSON/CSV import/export, `lock_field`, `refresh_resolved` (narration changed), `flag_framing_review` (aspect changed) |
| `planner.py` | smart planning: `sentences`, topic boundaries, TF-IDF matching `similarity_matrix/assign`, project/scene instruction phrase parsing, scene map CSV/JSON, `keep_locked`, `apply_guidelines` (transition default/frequency, no-shake, no-flash), `scene_warnings` |
| `scenes.py` | classic splitting (pauses), motion levels `apply_motion_level`, `reassign_transitions`, `scene_text` |
| `cues.py` | `[n]` markers, directions box (one paragraph per image), `media_index/resolve` (number/filename refs), `align`, `cut_time` |
| `asset_info.py` | asset IDs (content hash) + descriptions from imported prompts / sidecar .txt / embedded PNG/EXIF text. **No AI vision.** |
| `engine.py` | `Compositor`: scenes, transitions (`timeline_windows`, per-boundary `transition_s`), V2/B-roll (insert or overlay box), per-scene timed `effects`, per-scene captions override, atmosphere, overlays |
| `visuals.py` | `ImageSource` (Ken Burns with base cache, easing, zoom+pan combos; pan names follow film convention since v7.3), `VideoSource` (ffmpeg, export), `SeekVideoSource` (OpenCV, preview), proxies, `blend` transitions |
| `framing.py` | Fill/Crop with focal point, Fit/Blur, Fit/Colour, Fit/Black bars, legacy `blur`; `cover_tf` (zoom never uncovers edges); `detect_borders` (baked-in bars, multi-frame) |
| `fx.py` | atmosphere looks; `clip_adjust`; `vignette`; `apply_effects` (timed per-scene effects) |
| `captions.py` | `CaptionRenderer` (word highlight styles, safe margin) |
| `overlays.py` | text cards: hook, title, chapter, label, lower third |
| `audio.py` | SFX library/rules/planning; `mix()` (ffmpeg filtergraph: trims, fades, pan, delays, sidechain ducking, two-pass loudnorm linear, clipping guard) |
| `render.py` | `render_all/render`, encoder selection with real test encodes (`_hw_works`), `final_path` (never overwrite unless asked), `build_audio` (auto vs explicit SFX, `sfx_mode`, B-roll audio), `preflight`, SRT, editor package |
| `exportcfg.py` | aspects, resolution table, fps, quality presets → x264/NVENC/QSV/AMF args, `summary`, `migrate` |
| `mediainfo.py` | `probe`, `check_export` (size, SAR, rotation, fps, duration, audio, h264) |
| `editorial.py` | hook cuts, chapters, YouTube description, `quality_report` (includes instruction validation) |
| `guidelines.py` | style presets for project guidelines (`style_changes` preview, `apply_style`) |
| `profiles.py` / `presets.py` | video categories; editing presets, caption styles, SFX levels, transitions |
| `stock.py` / `stock_panel.py` | optional Pexels/Pixabay search (user keys in keyring) |
| GUI | `app.py` (MainWindow, bindings, undo, actions), `app_parts_base.py`, `gui_widgets.py` (PlayerWidget, PreviewCanvas), `timeline.py`, `properties.py` (right panel incl. Framing tab), `gui_panels.py`, `media_bin.py`, `plan_panels.py` (Script; Instructions = `EditingPlanPanel` (alias `TimedPlanBox`; text box, Import Plan / Validate / Apply to Timeline, review table Image\|Start\|End\|Duration\|Motion\|Notes\|Status, scene details) + collapsible GuidelinesBox and Smart-mode helpers; Review), `instructions_panel.py` (F8 window), `cue_dialog.py`, `theme.py` |

## 5. Data model (project JSON, `*.autoedit.json`)

Top level: `name, voiceover, script, script_text, media_dir, extra_media, excluded_media, music, sfx_dir,
extra_sfx, output_dir, words[{w,s,e}], duration, scenes[], broll[], sfx_events[], overlays[], chapters[],
directions, timed_plan, edit_instructions{scene_id: record}, scene_map[], asset_meta{asset_id: {...}},
next_scene_id, script_check[], alignment{status,note}, plan_report[], stock_assets{}, settings{}`.

**Scene** (timeline item): `id` (stable, e.g. S001 / T003 / S002-2 for extra segments), `start,end` (seconds,
absolute, frame-snapped), `media`, `text`, `motion` (zoom_in, zoom_out, pan_left/right/up/down, combos like
`zoom_in+pan_right`, static, subtle, none), `motion_amount`, `motion_ease`, `transition` (cut, crossfade,
dip_black, slide, whip, zoom) = transition to the NEXT scene, `transition_s`, `zoom,x,y,rot,opacity`,
`keyframes[]`, `adjust{}`, `effects[{type,amount,start,end}]` (scene-relative), `fit, focus[x,y], trim[l,t,r,b],
bg_color`, `clip_in, clip_out, short_clip`, `captions` ('off'|'on'), `caption_position`, `sfx_mode`,
`b_roll_mode`, `locked`, `locked_fields[]`, `status` (matched/assigned/locked/order/fallback/missing/manual),
`why`, `warnings[]`, `provenance{field: source}`, `instruction`, `segment_of`, `plan_line`.

**B-roll item**: `media,start,end,mode('insert'|'overlay'),size,position,clip_in,clip_out,audio_gain_db
(None=muted),short_clip,fade,source(scene id)`. **SFX event**: `t,file,cat,enabled` + auto: `vol_db`;
explicit: `rel_db` (dB relative to narration), `fade_in,fade_out,trim_in,dur,pan,source`.

**Instruction record** (`instructions.new_record`): `scene_id, instruction_text, notes, settings
(blank_settings: timing_mode follow_narration|absolute|scene_relative, narration_anchor{text,occurrence},
end_anchor, start/end/duration/offset_seconds, primary_visual{asset_filename}, segments[], clip_in/out,
framing{mode,fit,focus}, motion{mode,type,strength,ease,focus,target}, b_roll_mode,b_roll[], sfx_mode,sfx[],
effects_mode,effects[], transition_out{mode,type,duration_seconds}, captions{mode,position}, locked,
locked_fields[]), interpretation[[clause, meaning]], unresolved[], issues[{level,key,msg,options,accept}],
choices{key: index}, applied, resolved{start,end,explicit_timing,segments,words_sig}, accepted[], status,
from_plan, stale, aspect_review`.
Modes: `inherit` (guidelines) ≠ `auto` (tool may choose) ≠ `none` (explicitly off) ≠ `explicit`.
Issue levels: `choice`/`missing`/`conflict`/`error` block applying; `warning` doesn't (can be accepted).

**Settings** (see `project.DEFAULT_SETTINGS`): export `aspect, resolution, fps, enc_quality, encoder,
batch_export, batch_formats, overwrite`; planning `scene_mode (timed default), preset, category, motion_level,
repeat_media, hook_fast_cuts, auto_vary, seed, shuffle, pacing, shot_min, shot_max, project_instructions,
transition_default, transition_seconds, transition_frequency, restrict_no_shake, restrict_no_flash, style,
continuity`; framing `fit, bg_color, short_clip`; audio `voice_db, music_db, music_fade_in/out, music_duck,
duck_ratio, duck_release, sfx_level, sfx_db, sfx_mode (auto|explicit_only|none), builtin_sfx, loudness`;
captions `captions, caption_style, caption_font, caption_color, highlight_color, caption_position,
caption_size, caption_anim, caption_margin`; misc `whisper_model, language, atmosphere, overlay, autosave`.
Legacy `formats`, `quality` are kept only for migration.

## 6. Precedence & rules implemented
1. Locked values (scene `locked` / `locked_fields`) › explicit scene instruction › project guidelines ›
   category preset › automatic. Two explicit visuals on one interval = conflict (nothing applied).
2. Timed mode ("My editing plan (strict)"): the plan decides every cut and picture. With NO plan text you get
   a DRAFT (statuses `fallback`) for preview; a plan that FAILS is never replaced by pictures in file order -
   `project.replan` leaves the timeline unchanged and lists the problems. Export is **blocked** either way
   (`instructions.validate` fatal). Scene sheets are strict: an unspecified field = None. There is ONE plan box
   (Instructions → Editing plan; F9 opens the same panel large) - no start-page copy, and the Directions box is
   only shown in 'Follow my directions' mode.
3. Narration changed (new words) → `refresh_resolved` re-anchors, flags moved/locked scenes "needs review".
4. Export blocks on: missing explicitly requested files, gaps/overlaps, invalid clip ranges, unsupported
   effects, invalid timed plan. Warnings (unapplied/unresolved, short clips…) appear in the quality gate.
5. Levels: SFX `rel_db` = dB relative to the narration's loud speech (90th pct 50 ms RMS); B-roll audio dB =
   gain on the clip's own audio. Never percentages.

## 7. Conventions (follow them)
- **GUI bindings**: controls load under `QSignalBlocker` and write only from user-edit signals via
  `MainWindow._set` / `bind_combo|bind_check|bind_spin|bind_line|bind_path` (each registers a `load` in
  `self._bindings`; `load_controls()` refreshes all). Every edit ends with `self.commit("Label")` → undo step
  (snapshot of `STATE_KEYS` - **add new project keys to `STATE_KEYS`** or undo/dirty tracking breaks).
- After changing project data from code: `win.commit(label)` then `win.refresh_all()`.
- New project keys: add to `project.new_project()`, `STATE_KEYS`, migration if old projects need a value.
- New settings: `DEFAULT_SETTINGS`; if old projects must keep old behaviour, set it in `exportcfg.migrate`
  (old saved settings are authoritative).
- Long work off the UI thread: `MainWindow.start(job, done)` with `Worker`; cancellation via `w.stop` →
  `render.Cancelled`.
- Everything visual must go through `Compositor` (preview == export). Don't add render-only or preview-only
  behaviour.
- Times: seconds as floats, absolute; snap with `instructions.snap(t, fps)` (`round(t*fps)/fps` rounded to 6
  decimals, no accumulation). `Compositor.frame_at` rounds t to 6 decimals too - without it a cut whose snapped
  value rounded UP showed one frame late (fixed in v7.5). The muxer pads audio (`-af apad -shortest`) so every rendered frame is kept.
- Exports: temp file in `_autoedit_work`, `check_export`, then `os.replace`; never report success otherwise.
- Keep `pyflakes` clean; line length ~120; user-facing strings in plain English, no jargon; say "NOT applied"
  when something can't be done.
- Don't break existing tests; when a default changes on purpose, update the test that asserts it and say so.
- Before delivering: run the full suite, look at screenshots for UI changes, bump `autoedit/__init__.py`
  `__version__`, add a "New in vX" section at the top of README.md, zip as
  `AutoEditor_vX.zip` excluding `__pycache__`, `cache/`, `venv/`.

## 8. Known limitations / honest notes
- No AI image understanding: meaning-matching uses text (imported prompts, sidecars, embedded metadata).
  "Zoom toward the letter" can't locate the letter → focal point is the user's job (flagged).
- Instruction language is rule-based (fixed phrase set) - no LLM. Extend the regexes in
  `instructions.parse` / `timedplan._phrase` / `timedplan._sheet_settings`, and add tests.
- Live preview plays SFX at the right time/level but without fades/pan/trim (QMediaPlayer); export is exact.
- GPU encoders (NVENC/QSV/AMF) implemented with test-encode + CPU fallback but **never measured on real GPUs**.
- Not tested on Windows by the developer. Speech model downloads on first Analyze (~0.5 GB "small").
- Benchmark (2-core Linux, 60 s 1080p): v6.6 default 104.8 s; v7 Standard (x264 veryfast crf18) 55.2 s at equal
  SSIM/PSNR; High 129 s; Draft 37.6 s. Frame building (~40-48 s) is the next bottleneck if speed is needed.
  v7.5 on a 4-core Xeon 2.1 GHz: v7.4.0 43.0 s → v7.5.0 44.1 s total (noise), compose 29.7 s of 38-39 s render.
- Pan direction semantics were flipped in v7.3 to film convention (pan right = camera moves right).

## 9. Version history (short)
v1-v5 basics, SFX, captions, atmosphere, editor package · v6 CapCut-style workspace (timeline, properties,
V2, keyframes, undo) · v6.1-6.3 categories, motion levels, big preview, theater · v6.4 Stock B-roll ·
v6.5 cues/markers, Match images dialog · v6.6 directions box, no repeats · v7.0 smart planning, Script/
Instructions/Review, framing modes, single-aspect export, verified exports, faster Standard encode ·
v7.1 per-scene Editing instructions (F8), guidelines, JSON/CSV import · v7.2 required Timed edit plan (F9) ·
v7.3 scene-sheet format, editable per-scene summaries, write-back, film-convention pans ·
v7.3.1 bug-fix release from a code review (no new features; regression checks in test_core
`check_review_fixes*` and test_gui_state `check_review_fixes`) · v7.4 one Editing plan panel (Markdown/CSV/notes,
review table, strict apply without file-order fallback) · v7.5 main tabs Media / Editing Plan / Export (+More),
Strict/Assisted plan, JSON + key=value + guidelines in plans, None = off, exact-frame cuts, diagnostic plan.

## 10. Typical user workflow (what to keep working)
Start page: voiceover, images folder, script, video shape (More options: category, mode "Strict plan") → Create
project (auto-Analyze) → Editing Plan tab (or F9): paste / *Import Plan* (or *More → Fill
template*) → *Validate* → check the table (fix cells or text) → *Apply to Timeline* → Editing instructions (F8)
opens: verify/adjust each scene → Review scenes (Ctrl+Shift+R) / Quality check (F7) → Export (Ctrl+E) → one
verified MP4.

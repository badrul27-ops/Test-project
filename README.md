# AutoEditor v7.5.1

Automatic video editor for Windows. Everything runs on your own PC, and there's no subscription or API cost.

**You provide:** a voiceover, plus a folder of images and/or video clips.
**It does:** transcribes the voice → splits it into scenes at natural pauses → places your visuals on the timeline → adds smooth zoom/pan → adds transitions → burns in captions with the spoken word highlighted → adds SFX and ducked background music → exports **one** MP4 in the shape you choose (16:9, 9:16 or 1:1; more only if you turn on Batch export).

---

## 1. Install (one time)

1. Install **Python 3.11 or 3.12** from https://www.python.org/downloads/
   In the installer, **tick "Add python.exe to PATH"**.
2. Unzip AutoEditor somewhere simple, e.g. `C:\AutoEditor`.
3. Double-click **`setup.bat`**. It takes a few minutes and downloads about 300–500 MB of packages.
4. Double-click **`run.bat`** to open the app.

The first time you press *Analyze*, the speech model downloads once. The "small" model is roughly 0.5 GB.

## Upgrading from an older version

Unzip v6 **over** your existing AutoEditor folder and replace the files when Windows asks. Keep the `venv` folder, so you don't need to run setup again. Then start it with `run.bat` as before.

## New in v7.5.1: changing the images folder really replaces the images

Before, choosing a new images folder (or clearing it with ×) left old pictures behind: images you had added
separately (+ Files, dragging a folder in) stayed in the media bin, and scenes kept showing pictures from the old
folder. Now AutoEditor asks once - **Yes** clears the old pictures from the media bin and the timeline (those
scenes keep their timing, without a picture, until you apply your plan again), **No** switches folders but keeps
them, **Cancel** keeps the old folder. Nothing on your PC is deleted, and Ctrl+Z brings everything back.

## New in v7.5: simpler workflow, exact cuts, every plan format

**Workflow.** The editor has three main tabs: **Media**, **Editing Plan**, **Export**. Script, Audio, Text,
Effects, Transitions, Captions and Stock are under **More ▸**. The start screen asks only for the voiceover,
images/clips, optional script and video shape; category and plan mode are under *More options*. The button is
**Create project**. The Recent list only shows when there is something in it. Scene modes are called **Strict
plan** (your exact images and times) and **Assisted plan** (matches images by their descriptions - review it).

**Cuts land on the exact frame.** Some cuts used to appear **one frame late** in both the preview and the video
(when the cut time was rounded up to a whole frame). Fixed and checked on the last frame before and the first
frame after each cut, in the preview and in the exported video.

**More plan formats** (all become the same scene list):
- lines with settings: `00:00.000 - 00:04.200 | 001.png | motion=zoom_in | transition=cut`
- CSV such as `scene_id,image,start,end,motion,transition` with values like `zoom_in`, `none`, `cut`, and IDs like
  `S001`
- JSON: `{"version": 1, "guidelines": [...], "scenes": [{"image": "001.png", "start": "00:00.000", "end": ...}]}`
- a **PROJECT GUIDELINES** section at the top of any plan (e.g. "Use subtle motion", "Cuts only"). Guidelines
  are applied to scenes that don't set that thing themselves; anything not understood is listed as NOT applied.
- Times can be `116.84`, `01:56.84` or `00:01:56.84` - they all mean the same.

**None means off.** Writing `none` for motion, transition, sound effects, B-roll or effects switches that thing
OFF for the scene. An empty cell means "follow the guidelines".

**Review table:** Image (with a thumbnail) | Script excerpt (what is said during the scene) | Start | End |
Duration | Motion | Status. Notes, sound effects, B-roll, effects and transitions are in **Scene details** below
the table (notes are editable there).

**Templates and diagnostics.** *More → Save a plan template* writes a ready-to-fill Markdown, CSV, JSON or plain
template (also in the `examples` folder). *More → Save diagnostic plan* writes every scene's image, path, the
times you asked for, the exact frame times used, and its settings (JSON + CSV). Every export also records its
**timeline revision** in the log and saves `last_export_plan.json` in the `_autoedit_work` folder - so you can
check what a video was made from.

**Measured speed** (fixed 60 s sample, 1080p, Standard, 4-core PC): 43.0 s before → 44.1 s after - the same within
measuring noise, and the same picture (average difference 0.16 of 255 grey levels). Building the frames is ~75 %
of the render time; that is the part to speed up next.

## New in v7.4: one Editing plan

**One place for your plan.** The Instructions tab now has a single **Editing plan** panel. The extra boxes that
could overwrite each other are gone: the plan box on the start page, the separate "Scene instructions" box, and
the "Plan scenes" button (that one picked pictures in file order). The narration script stays in the Script tab.

**Paste it the way you have it.** All of these work, and they can be mixed:
- lines like `01:56.84 - 02:06.76 | 023.jpg | slow zoom in`
- Markdown tables, e.g. `| 23 | 023.jpg | 01:56.84 | 02:06.76 | 9.92 |` - the header row and the `|---|` row are
  recognised; extra columns (Motion, Transition, SFX, B-roll, Effects, Text, Notes…) are used
- CSV with a header row (image, start, end, …)
- SCENE blocks (scene sheets)
- notes such as `- **065.jpg:** Moved before 064…` or `Scene 12: keep the map visible` - they are attached to that
  image's scene and shown in the Notes column. Notes are **kept, not applied** as editing instructions.

Start and End decide the timing. If you also give a Duration, it is only checked (a mismatch is listed).

**Buttons:** *Import Plan…*, *Validate* (changes nothing), *Apply to Timeline*. *More* has the templates,
"Update plan from timeline" and the large window (F9).

**Review table:** Image | Start | End | Duration | Motion | Notes | Status - one row per scene, including broken
ones. You can edit Image, Start, End, Motion and Notes right in the table; the plan text is updated (other
formats are rewritten once as one table - nothing is dropped, Ctrl+Z undoes). The timeline only changes when you
press *Apply to Timeline*. *Scene details* shows the sound effects, B-roll, effects, text and transition of the
selected scene, and anything that was NOT understood.

**Strict:** if the plan has a problem (a missing image, a gap, an overlap, an unreadable time), nothing is applied
and every problem is listed. AutoEditor no longer falls back to putting pictures in file order. Cuts land on whole
frames, so 116.84 s becomes 116.833 s at 30 fps (the details panel shows both).

## New in v7.3.1: bug fixes

No new features - this release fixes problems found while checking the code. Your projects open as before.

**Exports**
- A video clip that repeats to fill a long scene now repeats only the part you chose. Before, the export
  could show footage you had cut out, even though the preview looked right.
- Typing `:` `/` `?` or similar in the file name no longer makes the export fail at the very end
  (those characters are replaced with `_`).
- Exports no longer crash when the sound-effect level is *None* but a scene asks for a sound effect, or when a
  video clip's length can't be read.
- An export stopped by an error now cleans up after itself, and an interrupted sound mix is never reused.
- The "Editor package" export now checks for missing files first, like the normal export.

**Timeline**
- Dragging, swapping or duplicating a picture now takes its clip range and framing (fit, focus point, trim)
  with it. A locked scene stops the move and tells you why.
- Splitting a video scene or B-roll clip: the second part continues the footage instead of replaying it, timed
  effects stay where they were, and each half gets its own scene number.
- After moving a B-roll or text clip past another one, the same clip stays selected.
- The CC (captions) track updates when you change caption size, style or video shape, so clicking a caption
  edits the right words. In 9:16 videos the caption editor now shows the same captions as the video.
- "Apply motion level to all" no longer changes locked scenes or motion you set in Editing instructions, and
  "Dynamic" never gives a scene near-still motion.
- Overlay videos (dust, grain…) loop in the preview like in the export.

**Plans and instructions**
- Scene sheets you write back and load again keep Zoom transitions, open-ended B-roll and "Inherit" B-roll.
- Double-clicking a scene-sheet problem jumps to the right line.
- In the timed plan, `image #3` and `#` inside quoted text are no longer cut off as comments.
- An instruction whose end phrase isn't in the recording shows a clear message instead of crashing.
- Timed effects in a scene that switches pictures are shared out across the pictures correctly.
- Images named by time (`0-05.png`): duplicate times no longer create empty scenes (the extra image is listed
  as NOT used).
- Imported instruction files with badly shaped entries are listed as import errors.

**Other**
- The Sound effects table shows the real level of effects placed by an editing instruction (change it in F8).
- Clearing the images folder no longer empties scenes from a different folder with a similar name.
- A new project no longer silently copies the previous project's instructions or continuity notes.
- Directions box with no images, and cue CSV files with short rows or `1:23.5` times, no longer crash.

**Still true:** AutoEditor has been tested on Linux only, not yet on Windows.

## New in v7.3: scene sheets ("STRICT VISUAL EDITING INSTRUCTIONS")

The Timed edit plan also accepts the block format, one block per scene:

```
SCENE 003
Image: 003.png
Script sentence: She opened the letter.
Start: 00:08.800
End: 00:13.500
Duration: 4.700 seconds
Framing: Fill / Crop
Motion: Gentle pan
Direction: Left to right
Focus: top left            (or 30%, 40%)
B-roll: 006.mp4
B-roll timeline start/end: 00:10.000 - 00:12.000
B-roll source in/out: 00:01.000 - 00:03.000
B-roll placement: Full-frame replacement      (or Overlay top right 30%)
B-roll source audio: Muted                    (or -12 dB)
Sound effect: paper.wav
SFX start: 00:10.200                          (or a word in quotes: "opened")
SFX level: -14 dB                             (relative to the narration; or inherit)
Visual effect: None
Transition to next scene: Crossfade 0.5 s     (Cut / Dissolve / Fade to black / Automatic / None)
Lock visual and timing: Yes
```

- **Strict:** anything you don't specify is **None** (no motion, cut, no B-roll, no SFX, no effect). "Automatic" lets AutoEditor choose; "None" disables.
- **Checked before anything is built:** missing or ambiguous images, start/end/duration that disagree, overlaps, gaps, a plan shorter/longer than the narration (all block the build); unfilled `[placeholders]`, unknown fields, values it doesn't understand, effects the renderer can't draw (e.g. lens flare) and a script sentence that's spoken at a different time than its scene (all listed - never silently ignored or faked).
- **GENERAL RULES:** "16:9, 1920 × 1080, 30 fps" sets the export format; the other rules are how timed mode always works.
- **Editable timeline settings:** *Build timeline from plan* opens *Editing instructions*: every scene with its image, resolved start/end/duration, status and a summary of the real settings (framing, motion, B-roll, SFX, effects, transition). Change anything there and the scene sheet text is rewritten to match; *Update plan from timeline* does the same on demand. Preview and export use that same timeline.
- *Fill scene-sheet template* (after Analyze) writes the blocks for you with every sentence and its exact times - you fill in `Image:` and the settings. Example: `examples/scene_sheet_example.txt`.
- **Pan directions now follow film convention:** "pan right" / "Left to right" = the camera travels to the right. (Before v7.3 the internal pan directions were reversed; automatic pans in older projects may now move the other way.)

## New in v7.2: the Timed edit plan (required)

You decide **which image is on screen from which second to which second**. AutoEditor builds exactly that - it never moves a cut or swaps a picture - and **won't export until the plan covers the whole narration**.

1. Start page → step 4 is now **"My timed edit plan - exact seconds per image (required)"**. Open the editor (Analyze runs).
2. Instructions tab → **Timed edit plan** (or **Project → Timed edit plan…**, **F9**, for a big window) → **Fill template from narration**. Every sentence gets a line with its exact times from your recording:
   `0:03.15 - 0:06.26 | ??? |   # He buys a ticket at the counter.`
3. Replace each `???` with the image (`012.png` or `12`; if two folders have the same name write `folder/012.png`). Change the times if you want - shots just can't overlap or leave gaps.
4. Optionally, after the image, how it should look - comma separated: `slow zoom in`, `pan right`, `zoom in + pan left`, `still`, `cut` / `crossfade 0.8` / `fade` (to the next shot), `sfx whoosh.wav` (at “word”, -14 dB), `b&w`, `sepia`, `vignette`, `text "Morocco, 1910"`, `fill` / `fit`, `no captions`, `lock`. Shots without it follow the project guidelines.
5. **Check plan** lists every problem with its line number (double-click to jump there): missing image, `???` left, overlap, gap, plan shorter/longer than the narration, unreadable line, words it doesn't understand. **Build timeline from plan** builds it (Ctrl+Z undoes). Re-analyzing rebuilds from the plan.

Without a plan (or with errors) you get an automatic **DRAFT** to preview, marked as such, and the export is blocked. You can also paste a ready plan on the start page (step 5) or load a .txt / .csv (`start, end, image, look`). Older projects keep their own scene mode.

Verified in `tests/test_timed_plan.py` on a real export: shots at the written seconds (checked on decoded frames), black & white only on its shot, the crossfade only where asked, the sound effect within one frame of its shot's start, broken plans rejected line by line with nothing built, export blocked without a plan.

## New in v7.1: Editing instructions, scene by scene

**Open:** Project → *Editing instructions…* (**F8**), or the button on the Instructions tab. Left: every scene (number, ID, narration, thumbnail, start/end/duration, status, lock). Right: the selected scene's controls. You never have to fill everything - each field is **Inherit** (project guidelines), **Automatic**, **None** (explicitly off) or **My setting**.

**How it's built (and why):** what you *ask for* is stored separately from the *timeline*. Each scene's instruction (your text, the structured settings, your choices for ambiguous references) lives in the project under its stable scene ID. **Apply** resolves it - words → times, file names → files - and writes the result into the one timeline that both the preview and the export draw; each scene records which instruction produced each value. Re-planning keeps applied instructions (their pictures and times are reserved before automatic matching) and re-applies them; scene IDs are kept when the cuts stay similar. Precedence: locked explicit values › explicit scene values › project guidelines › category preset › automatic. Two explicit visuals on the same time range are a **conflict** - nothing is applied, there's no arbitrary winner.

**Typing instructions** (Instruction tab → Parse → check → Apply, Ctrl+Z undoes):
> Use 003.png when the narrator says “She opened the letter.” Hold it until “She knew what to do next.” Slowly zoom in. Play paper.wav at “opened,” quietly below the narration. Add no B-roll. Cut to the next scene. Lock the visual and timing.

Each sentence is shown beside what it became. Sentences it doesn't understand are listed as **NOT understood** and never applied. If a phrase is spoken twice, two files share a name, or you write "the third image", you get a choice list - nothing is picked silently. Parsing is rule-based on your PC (no AI model, no online service). *Parse all* / *Apply valid instructions* work on every scene. The full list of phrases is under *Supported phrases*.

**Per-scene controls:** visual + timing mode (*follow narration* with start/end words and occurrence, *absolute time*, *relative to scene start*; typing a time switches to absolute and start/end/duration stay consistent), source clip in/out, more visuals per scene ("Then use 004.png when…"), framing + focal point, motion (type incl. zoom+pan combinations, strength, easing; *Show start and end frames*), B-roll (full-frame insert or picture-in-picture overlay with size/position, anchored to words or times, own sound muted unless switched on, short-clip choice: freeze or approved loop), sound effects (local file picker + Audition, words or time, offset, level, fades, start trim, length, pan), effects (only ones the renderer draws: brightness, contrast, saturation, black & white, sepia, warm, cold, blur, vignette, text label - each with its own time range), transition out (one boundary, duration, shows the overlap in frames and when it's clamped), captions off/on/position, locks (whole scene or single fields), notes.

**Units:** SFX level is **dB relative to the narration** (0 dB = the effect's loud part as loud as the voice's loud parts; "quietly" = -14 dB). B-roll sound level is dB applied to the clip's own sound. Never percentages.

**Project guidelines** (Instructions tab → *Structured guidelines*): style starting presets (documentary, narrative story, car explainer, psychology explainer, animation narration - the changes are listed before *Apply this style*), pacing + preferred shot length, default framing, motion, default transition + how often (every cut / topic changes) + length, sound effects (automatic suggestions / only the ones I assign / none), music level and ducking (strength, recovery to avoid pumping), captions + safe margin, continuity notes, restrictions (no shake, no flashing transitions).

**Import / export:** *Export JSON* (complete, `schema_version` 1) and *CSV template* (basic columns: `scene_id, asset_filename, script_excerpt, phrase_occurrence, timing_mode, start_seconds, end_seconds, motion_mode, fit_mode, transition_type, transition_seconds, sfx_filename, sfx_anchor_text, sfx_offset_seconds, sfx_gain_db, locked`). *Import* checks every row/field first, lists errors with row and field, previews the changes and only then writes them (then press *Apply valid instructions*). Blank = inherit; `none` = off. Examples: `examples/instructions_example.json`, `examples/instructions_example.csv`.

**Before export** (*Check before export*, and automatically when you render): missing files, invalid clip ranges, gaps/overlaps, unknown effects and similar **block** the export; warnings (unapplied or not-understood instructions, short clips, narration changed) can be accepted and the acceptance is saved with the scene. When the voiceover changes, instructions re-anchor to the new words and anything that moved is flagged; locked times stay and say "Review its locked times". Changing the video shape flags scenes with manual framing.

**Verified** (`tests/test_edit_instructions.py`, real exports, decoded frames and audio): the instructed picture is on screen exactly during its sentence and survives re-planning; Inherit/Automatic/None differ and survive reload; repeated phrases/duplicate names/ordinals ask; zoom+pan preview = export (PSNR > 32 dB) with no empty edges; B-roll only on its interval, narration audio identical with or without it; the SFX starts within one frame of its word (measured 1 ms off), a -10 dB change measured -10.0 dB, a 0.2 s fade-in measured; a 0.6 s crossfade leaves every cut time and the frame count unchanged (745 frames = 24.83 s × 30) and other boundaries stay clean cuts; locks survive re-planning and a moved narration is flagged; malformed JSON/CSV is rejected with row/field errors; a missing requested sound blocks the export; one export = one file. `tests/test_instructions_gui.py` drives the window itself.

**Limitations:** the instruction language is a fixed set of phrases (no language model). AutoEditor can't see what's in a picture - "zoom toward the letter" asks you to set the focal point. The live preview plays sound effects at the export level and time but without fades/pan/trim (the export and *Draft preview* apply them exactly). Region highlight and other effects without a renderer implementation aren't offered. Windows was not available for testing.

## New in v7: script-driven scenes, honest matching, one export, faster render

### Why the old version went wrong (root causes)
- **Visuals didn't match the script.** Older scene modes cut at pauses (or evenly) and handed out images in file order. AutoEditor had no idea what an image shows, so a picture could land on the wrong sentence and nothing told you.
- **Two videos per export.** New projects had 16:9 *and* 9:16 ticked by default, and Render made every ticked format - double the time.
- **Slow render.** On the fixed benchmark the x264 encoder (preset *fast*) was the main cost: ~57 s of a 105 s render was spent waiting for the encoder. The default of two formats doubled it again.
- **Black sides.** The old default framing *Blur background* deliberately puts a pale blurred copy beside pictures whose shape differs a lot (e.g. square pictures in 16:9). New projects now use **Fill / Crop**. Separately, a player such as VLC adds its own black bars when its *window* has a different shape than the video - that isn't in the file. Every export is now checked: the Log line `Verified: 1920x1080, square pixels, 30 fps …` comes from reading the finished file.

### The new workflow
1. **Media** - voiceover + images/clips (as before). On the start page choose the **Video shape** too.
2. **Script** tab - paste the *exact* narration. It fixes caption spelling, finds topic changes (blank lines between paragraphs help), and **Compare with recording** lists passages that are missing, extra or worded differently (double-click to hear the spot). Timing always comes from the recording; narration is never sped up or stretched.
3. **Instructions** tab
   - *Project instructions* (whole video), e.g. `no camera motion`, `cuts only`, `keep the images in chronological order`, `scenes of 3-8 seconds`, `fill the frame`. Lines it doesn't understand are listed - never guessed. Click **Supported phrases** for the list.
   - *Scene instructions*, e.g. `Use image 003 while the narrator discusses the letter; keep it until the next topic.` · `Use 12.png from 1:20 to 1:31` · `Show clip 7 when the narrator says "the river burst its banks" for 5 seconds (lock)`. The v6.6 one-paragraph-per-image directions and `[1]` markers still work.
   - **Import image prompts…** - the prompts/descriptions your images were generated from (CSV/JSON with `asset_filename,prompt`, or a .txt with one prompt per paragraph in image order). This is what lets AutoEditor match images **by meaning**. A `.txt` next to an image with the same name, and text that image generators embed in PNG/JPEG files, are used too. Descriptions follow the image by content, so renaming files changes nothing.
   - **Import scene map…** - CSV/JSON with `scene_id, asset_filename, script_excerpt, start_seconds, end_seconds, fit_mode, motion, locked`. Every row is checked (duplicate IDs, missing files, end before start, past the end of the narration, unknown fit mode, overlaps) and problems are listed.
   - **Plan scenes** - *Smart* mode: the script's topic changes decide **when** to cut, the descriptions decide **what** to show. Order of authority: locked scenes › your scene instructions / scene map › project instructions › automatic choices.
4. **Review scenes** (bottom tab, or *Project → Review scenes…* Ctrl+Shift+R for a big window) - every scene with ID, time, thumbnail, file, status and the reason: *Matched* (shows the shared words), *Your instruction*, *Locked*, *File order* (no descriptions available), *Fallback* (nothing matched - an unused picture is shown and flagged), *No visual*. Buttons: **Play scene** (with narration), **Replace visual…**, **Edit timing**, **Adjust framing**, **Lock/Unlock**, **Accept fallback**, **Reanalyze unlocked scenes**. The filter *Needs attention* shows only what needs a decision. Similarity is a ranking, not a probability, so no percentages are shown.
5. **Properties → Framing** (per scene): *Fill / Crop* (default) with a **focal point**, *Fit / Blurred background*, *Fit / Colour background*, *Fit / Black bars* (only when you pick it). **Detect borders** finds black/white bars baked into a picture (in several frames of a video) and **Trim them** removes them reversibly - dark artwork isn't mistaken for a border. For video clips: **Clip starts at / ends at** and what happens if the clip is shorter than its scene (freeze last frame, loop, or slow the *clip* down - the voice is never changed). **Lock this scene** keeps it through re-planning.
6. **Export** tab: **Aspect ratio** (one choice; the preview switches to it), **Resolution** with exact pixels (Draft 854×480 … Full HD 1920×1080 … 4K-class 3840×2160, and the portrait/square equivalents), **Frame rate** 24/25/30/60, **Quality** Draft / Standard / High. The summary line says exactly what you'll get, e.g. `1 video · 16:9 · 1920 × 1080 · 30 fps · Standard`. **Advanced** (closed by default): Batch export of extra shapes, encoder choice, replace-existing.
   - An existing video is never replaced unless you say so (you're asked: keep both / replace / cancel).
   - The video is written to a temporary file in `_autoedit_work`, checked (size, square pixels, frame rate, duration, audio, H.264) and only then given its name. Cancel, a missing picture file, or a failed check never leave a "finished" video behind.
   - GPU encoders (NVIDIA NVENC, Intel QuickSync, AMD AMF) are used only after a real 1-second test encode works, with their own quality settings; otherwise the CPU is used and the Log says so.

### Measured render speed (fixed benchmark)
`python -m tools.bench_render` builds the same 60-second project every time (12 photos 3000×2000, one 1080p clip, music, captions) and renders one 16:9 1920×1080 30 fps video on the CPU. Measured in my 2-core Linux test environment - your PC will differ:

| | render time | file | SSIM / PSNR vs. near-lossless reference |
|---|---|---|---|
| v6.6 default (x264 fast, crf 20) | 104.8 s | 47.7 MB | 0.9756 / 44.6 dB |
| **v7 Standard** (x264 veryfast, crf 18) | **55.2 s** | 55.5 MB | **0.9765 / 44.7 dB** |
| v7 High (x264 medium, crf 18) | 129.3 s | 66.5 MB | 0.9813 / 45.7 dB |
| v7 Draft (x264 ultrafast, crf 26) | 37.6 s | 32.6 MB | 0.9288 / 40.3 dB (visibly compressed) |

Video and audio stream lengths were identical in every file (60.36 s / 60.39 s, both starting at 0). Standard matched the old default's measured quality at about half the time; files are ~16 % bigger. With v6.6's default of two ticked formats an export ran two renders, so for those projects the saving is bigger (not measured separately). Building frames was *not* made faster in v7 (about 39-48 s of each run) - the gain is the encoder setting plus exporting one video. The benchmark pictures are random texture (hard to compress); real photos usually compress better. `python -m tools.compare_video test.mp4 reference.mp4` repeats the quality/sync comparison.

### Old projects
Opening a v6 project keeps every choice it saved: two ticked formats become *aspect + Batch export of the other one*, 720p → HD, 1080p → Full HD, the old Blur framing and pause-based scene mode stay. Only new projects get the new defaults.

### Limitations (please read)
- **No AI looks at your pictures.** Meaning matching uses text: imported prompts, sidecar `.txt` files, embedded generator metadata. Pictures without any description are placed in file order and labelled *File order*. No media is sent to any online service, and no API key is needed or used for planning. (A consumer Gemini/ChatGPT subscription does not give API access; none is used.)
- Matching compares words, not concepts: a prompt that says "canine" won't match narration that says "dog". On a synthetic 12-minute test with 96 described images, 94 of 96 scenes got the image written for that passage - real scripts will differ.
- Video clips: descriptions come only from a sidecar `.txt` (no frame analysis).
- Speech-recognition failure: if a script is available, timing is *estimated* from the script and clearly flagged (Script tab + Quality check) - it isn't real alignment.
- GPU encoding (NVENC/QSV/AMF) and Windows were not available in my test environment: the GPU options are implemented with test-encode + CPU fallback, and the fallback was tested, but GPU speed/quality is unmeasured.

## New in v6.6: a Directions box and no repeated images

- **Images are no longer repeated by default.** When the narration has more natural pauses than you have images (e.g. 10½ minutes and 96 images), AutoEditor now makes the scenes longer so **each image is shown once, in number order** (1, 2, … 10, 11 - not 1, 10, 11, 2). Tick **Allow images to repeat** in the Media tab if you ever want the old behaviour.
- **Directions box** - on the start page (step 4) and in the Media tab. Paste your script split into scenes, **one paragraph per image** (blank line between), in the same order as your numbered images. Paragraph 1 → image 1, paragraph 2 → image 2 … AutoEditor finds each paragraph in the recording and starts that image exactly there. "Scene 3:" / "3)" numbering at the start of a paragraph is ignored. You can also use markers (`[1] …`, `[2] …`, `[3, 4] …`) for exact control.
  - Typing directions switches the scene mode to **Follow my directions**. In the editor, **Apply directions (re-plan scenes)** rebuilds the timeline (Ctrl+Z undoes it).
  - If the paragraph and image counts differ you're told: extra paragraphs stay on the last image; extra images are left unused.
- Step 4 on the start page also lets you pick how images follow the narration before the first analysis.

## New in v6.5: tell AutoEditor which image goes with which words

The automatic scene modes only know *when* you pause - not *what* each image shows. Now you can direct it:

- **Project → Match images to narration…** (Ctrl+L, also a button in the Media tab and the timeline toolbar). Every sentence of your transcript is a row. Type the image number (or file name) next to the sentence where that image should **start** - it stays on screen until the next number. Empty rows keep the previous image; `4, 5` on one row splits that part between two images at a pause. Unknown numbers are flagged before you apply. **Apply** rebuilds the timeline (Ctrl+Z undoes it).
  - *From current scenes* shows the current plan as numbers, *Number every row 1, 2, 3…* gives one image per sentence, *Import [1] markers…* reads a marked script or CSV.
- **Image markers in your script:** put `[1]`, `[2]`, `[3, 4]`, `[05.png]` (or `Image 6:` at a line start) before the part each image covers, choose it as the Script, and pick Scene mode **Image markers in script**. Markers are matched to the recorded words, so small differences between script and recording are fine; partly matched markers are listed in the Log. Markers never appear in the captions. A CSV with `image,text` or `image,start` (e.g. `12,1:23.5`) works too.
  - Image numbers come from the first number in each file name (`001.png`, `image_12.jpg`, `Scene 7.png`). If two files share a number you're told which one is used.
- **Exact scene timing** in Properties → Video for a scene: **Starts at / Ends at** boxes and **Start at playhead / End at playhead** buttons move the cut (the neighbouring scene follows; the narration never changes).
- Long videos open zoomed to about one minute, so scenes are wide enough to click and drag; **Fit** still shows everything.

## New in v6.4: Stock B-roll search (Pexels + Pixabay)

- New **Stock** tab (left panel). It searches **existing** stock videos on Pexels and Pixabay with **your own free API keys** – it doesn't generate video.
- **API keys…** (link at the top of the tab): get free keys at https://www.pexels.com/api/ and https://pixabay.com/api/docs/. Keys are stored in **Windows Credential Manager** (via the `keyring` package – run `setup.bat` once after updating to install it). They are never written to project files, settings or the code. Without `keyring`, keys are kept only until you close the app.
- **Search** by typing, or click **Suggest from scene** for editable ideas from the selected scene's narration (it skips people's names), or **Ideas ▾** for broad concepts: locations, objects, weather & nature, silhouettes & moods, textures.
- Results show a thumbnail, duration, size, creator, provider and links to the clip page and license. **▶ Preview** streams a small version (nothing is saved). Nothing downloads until you click one of:
  - **Download → scene** – puts the clip on the selected scene; if the scene already has a visual it asks: Replace / Add on V2 instead / Keep in media bin only.
  - **Download → V2** – lays it over the main scenes at the playhead.
  - **Download to media bin only.**
- Clips are saved in your images folder under `_autoedit_stock` (a folder the scene planner ignores, so stock clips never change the order of your own media), appear in the media bin, and their provider, creator, page link and license are saved in the project. **Copy credits** (and Export → Copy YouTube description) add "Video by … on Pexels/Pixabay" lines for the clips you used.
- **Provider rules** (from their API docs in Oct 2026 – check them, they change): Pexels allows 200 requests/hour and asks for a visible link to Pexels and creator credit; Pixabay allows 100 requests/minute, requires search results to be cached for 24 hours and forbids systematic mass downloads. AutoEditor caches every search for 24 h, keeps under the request limits, shows the provider links, and only downloads clips you pick one at a time.
- A warning appears when a search is likely to return identifiable people: don't use real people's footage in a way that connects them to events in your story – it can be misleading or defamatory, and both licenses forbid it.
- Free use still depends on each provider's current terms and each clip's rights; open **Source page** to check.
- If a provider is down or a key is missing/invalid, the tab says so clearly; local import keeps working.

## New in v6.3: bigger preview by default

- The preview now gets at least **55 % of the window height** (the timeline takes the rest, with thinner tracks when space is tight), and the Preview title row is gone, so the picture is much larger. On a 1920-wide window the 16:9 picture is about 670×380 instead of about 355×200.
- On wide previews all controls sit in **one row** under the picture; on narrow ones they wrap to two.
- Layouts saved by older versions are reset once to the new defaults. **View → Reset layout** applies them again any time; drag the dividers to taste.

## New in v6.2: a big, inspectable preview

- The preview picture now uses **the whole preview panel** and is recalculated whenever the window or a divider moves. **Fit** (default) shows the whole frame as large as it fits; black space appears only where the panel's shape differs from the video's.
- **Fill** covers the panel (may crop the edges *in the preview only*), **100%** shows one preview pixel per screen pixel, and **− / +** zoom (or Ctrl+wheel). Drag the picture to pan when zoomed; click the zoom readout or double-click the picture to reset to Fit. None of this changes the project or the export.
- **Theater mode** (⛶ Theater button, F11, View menu) hides the tool, properties and timeline panels so the preview gets most of the window. Click **✕ Exit theater** or press F11 again – every panel and divider comes back exactly as it was.
- The preview is rendered sharper when it's shown larger (up to 1280 px on the long side; preview only).
- **1:1 square** joins 16:9 and 9:16: tick it under Export → Aspect ratio (1080×1080 or 720×720), and choose 16:9 / 9:16 / 1:1 in the preview's aspect menu to see each shape. The preview menu never changes which shapes are exported.
- On short windows the timeline tracks get thinner and the lower tabs start folded, so the preview gets more height; drag the dividers to rebalance.

## New in v6.1: video categories, volume, layout fixes

- **Video category / editing style** – choose it on the start page (step 3) or later in **Project → Project settings** (Ctrl+,), also reachable from **Media → Video category → Change…**:
  - **Vox Style** – purposeful cuts, motion used selectively, slides; expects your maps/charts/graphics (AutoEditor adds labels and callouts as text cards, it doesn't draw maps).
  - **Long Story History** – 5–11 s shots, restrained zooms/pans, long crossfades, archival "Old Film" look.
  - **Horror** – slow creeping push-ins, dark desaturated look, dips to black, tension/heartbeat/riser effects (add your own ambience as Music for a continuous bed).
  - **2D Animation** / **Stickman** – for your imported illustrated scenes; readable moves and clean cuts. AutoEditor doesn't draw or animate characters – use keyframes for precise moves.
  - **Documentary** – restrained motion, documentary captions, factual pacing.

  A category only sets **starting defaults** (editing preset, motion level, caption style, atmosphere, SFX amount, music level). Every one stays editable, the category is saved in the project, and the start page / Media tab say which assets the category expects and whether you've imported any. After changing category on an analysed project, use **Re-plan scenes** (Shift+F5) to apply its pacing.
- **Automatic motion level** (Media tab): **None** (still images), **Subtle**, **Medium**, **Dynamic**. Each image gets its own amount (no more identical zoom on every image), long scenes prefer gentle pans, and video clips are never given image motion. **Apply motion level to all scenes** re-chooses motion now without touching timing, visuals or keyframes. Per-scene presets, keyframes, Apply to all and Reset are in Properties → Animation.
- **Volume:** a speaker button next to Play opens the **preview volume** slider and **Mute** (Ctrl+Up / Ctrl+Down, Ctrl+Shift+M). It only affects what you hear while editing, never the export. Export levels: **Voice level, Music volume, SFX volume** in the Audio tab, or click the A1 / A2 / A3 track label on the timeline. The preview now also plays **planned sound effects** (tick **SFX**; levels are approximate – the render matches them to your voice).
- **Preview (6.1.1):** the preview screen takes the video's own shape (16:9 or 9:16, from the aspect menu) and is as large as the panel allows, so the video fills it – never stretched or cropped. Fit / 1:1 and aspect stay next to it. Drag the dividers to resize, or hide it with **View → Preview panel**.
- **Whole workspace visible:** thicker, highlighted panel dividers; the lower tabs (Scenes, Text & chapters, Sound effects, Quality check, Log) can be hidden and shown with **▾ Hide / ▴ Panels** (timeline toolbar) or **Ctrl+J** (View → Lower panel). On short screens (e.g. 1366×768 or 125–150 % scaling) they start folded so every timeline track, the scrollbar and the status bar stay visible. Header labels are shorter so Draft preview and Export always fit. Fractional Windows scaling (125 %/150 %) is rendered at its exact size.
- **Timeline scrollbar** sits directly under the tracks: drag it to move through the project; it follows zoom (+ / − / Ctrl+wheel) and becomes active as soon as the timeline is wider than the view.

## New in v6: CapCut-style editing workspace

Layout after you open a project (panels are resizable; **View → Reset layout** restores the defaults):

- **Header:** project name, Back to setup, New, Open, Save, Undo, Redo, the Saved / ● Unsaved pill, **Analyze voiceover**, **Draft preview** and **Export**.
- **Left – tool tabs:** Media · Audio · Text · Effects · Transitions · Captions.
  - **Media** has the inputs, a **media bin** with thumbnails and file names, and the scene-planning settings (scene mode, editing preset, variety, opening cuts, odd-shaped images – these used to be in the Motion tab).
  - **Audio:** voice level, music, music volume, **fade in / fade out**, **ducking on/off**, loudness, **▶ Preview music**, SFX amount (None / Minimal / Normal / Cinematic) and your SFX library folder.
- **Centre – preview:** play/pause, current time / duration, Captions / Effects / Music toggles, **Fit / 1:1**, and 16:9 / 9:16.
- **Right – Properties** for whatever you selected on the timeline:
  - a **scene (V1) or V2 clip** – tabs **Video** (scale, X/Y position, rotation, opacity, replace/remove/delete, reset), **Animation** (motion preset + amount, apply to all, **keyframes**), **Effects** (filter, brightness, contrast, saturation, blur, strength; apply to all) and **Transition** (type + **▶ Preview transition**)
  - a **text card** – type, text, second line, start/end
  - a **caption** – edit the words (fix a mis-heard word; timing is kept)
  - a **sound effect** – on/off, time, level, **▶ Preview**, delete
  - the **Voice (A1)** or **Music (A2)** track – click its label on the timeline
- **Bottom – multi-track timeline:** V2 B-roll · V1 Scenes (thumbnails, ◆ keyframes, transition markers) · T Text cards · CC Captions · A1 Voice (waveform) · A2 Music (waveform) · A3 SFX. The Scenes table, Text & chapters, Sound effects, Quality check and Log are in the tabs under it.

### Timeline editing
- **Click** selects (Properties follows) and moves the playhead. A plain click never changes anything – edits start only after you drag a few pixels.
- **V1:** drag a cut edge to retime (snaps to pauses); drag a scene's body onto another scene to **reorder visuals** (timing stays with the narration); double-click to replace.
- **V2 / Text:** drag to move, drag an edge to trim. **SFX:** drag to move in time.
- **Split** (Ctrl+B), **Duplicate** (Ctrl+D), **Delete** (Delete key on the timeline – a deleted scene gives its time to the neighbour, so the narration is never cut), Merge, Swap, right-click menus, **Snap** toggle, zoom (−, +, Fit, Ctrl+wheel), scrollbar.
- Drag a file from the **media bin** onto V1 to replace that scene's visual, or onto V2 to add a B-roll clip.

### Animation and keyframes
- Motion presets: slow zoom in / out, pan left / right / up / down, **subtle camera movement**, almost still (3 % push) and **None** (perfectly still).
- A **keyframe** stores scale, position, rotation and opacity at one moment. With two or more, the clip moves smoothly (eased) between them. Use **◆ Add at playhead**, Delete, Clear all, Previous / Next. When a clip has keyframes, the Video sliders edit the keyframe at the playhead (and add one if there isn't one). Keyframes add to the motion preset – pick **None** to move a clip only with keyframes. Keyframes show in the preview while you scrub or play, and in the export (checked by `tests/test_v6_render.py`).

### Media import
- **+ Files** (several at once), **+ Folder**, **File → Import media files…** (Ctrl+I) or drop files/folders on the window. Only the file paths are recorded – nothing is copied.
- Thumbnails and waveforms are made in the background and cached in `cache/thumbs`, so the window never freezes and reopening is instant. Files that can't be read are marked "can't read" (hover for the reason); unsupported or missing files are listed in the status bar and Log.
- **Remove** in the bin takes a file out of the project only – the file stays on your PC.

### Rendering
- **Draft preview** (Ctrl+Shift+D): 480p, at most 24 fps, fastest CPU encode, of the format shown in the preview; saved as `<name>_DRAFT_16x9.mp4` with a **Play draft** button. In our test (17.5 s video, 2-core cloud machine) the draft took 4.4 s and the 1080p final 26.6 s. Your times will differ – the app logs the real time of every render.
- **Export** = final render with the Export settings (16:9 and/or 9:16, 1080p or 720p, fps, encoder with GPU auto-detect and x264 fallback, output folder). Progress, current step and time left are shown; Cancel (Esc) stops it and deletes only the unfinished MP4.
- The audio mix is cached: if voice, music, SFX and levels haven't changed, the next render reuses it.

### Project safety
- **Autosave:** a recovery copy (`<project>.autoedit.json.autosave`) is written every 30 s while there are unsaved changes (turn off in Export). Saving removes it. If AutoEditor closes unexpectedly, opening the project offers the recovered version; for never-saved work use **Recover unsaved work…** on the start page.
- **File → Cancel project (back to setup)** closes the project (asks Save / Discard / Keep editing). **File → Clear project…** empties the working project after listing what will be cleared. **File → Delete project file…** deletes only the `.json` project file after confirming. None of these ever delete your voiceover, images, clips, music or rendered videos.

## New in v5

- **Setup screen:** AutoEditor opens on a start page. Choose your voiceover and images folder (plus a script if you have one), then click **Open the editor**; it can start analyzing straight away. You can also open a project or pick one from **Recent** on this page.
- **Tool tabs in a row:** Media, Audio, Motion, Effects, Transitions, Captions and Text sit in one horizontal row with icons, just under the header. The active tab is highlighted, and the row scrolls sideways if the panel is narrow.
- **Clear a wrong pick:** every input (voiceover, script, images folder, music, SFX folder, overlay, output folder) has an **✕** button. A line under it shows what's selected (for example "✓ voice.mp3 · 1.4 MB", or "⚠ File not found"). Clearing never deletes anything from your PC:
  - Clearing the **voiceover** also removes its transcript and scene plan, and it tells you that before it does.
  - Clearing the **images folder** leaves the scenes that used it without a visual, but keeps their timing.
  - You can undo either one with Ctrl+Z.
- **Remove visual:** in the Clip panel, the Edit menu, or the Delete key on the timeline, next to **Replace visual…**.
- **Back to setup** (header button, or File → Back to setup, Ctrl+W) closes the project and returns to the start page. If you have unsaved edits it asks **Save / Discard changes / Keep editing**. "Discard" only drops the edits since your last save; your saved project file, media and rendered videos are never deleted.
- **Start over (clear inputs)…** (File menu) clears the inputs and scenes of the project you're working on, after listing exactly what will be cleared. Your style settings are kept. It also detaches from the saved project file, so the saved copy can't be overwritten with an empty one.
- **Cancel render / analysis** (Esc, or the button in the status bar) only stops a running job; your project stays open. You can't leave a project while a job is running.

## New in v4: the editor workspace

- **Layout** (like a modern editor):
  - **Menu bar:** File, Edit, View, Project, Playback, Export, Help
  - **Header:** save, undo and redo, a "Saved / ● Unsaved" indicator, **Analyze voiceover** and **Render video**
  - **Tool tabs** (a horizontal row since v5): Media, Audio, Motion, Effects, Transitions, Captions, Text
  - **Centre:** preview
  - **Right:** **Clip** inspector (the selected scene) and **Export** (aspect ratio, resolution, frame rate, encoder, output folder)
  - **Bottom:** timeline with V1/A1 track labels, plus tabs for Scenes, Text & chapters, Sound effects, Quality check and Log

  Panels are resizable, and the app remembers their sizes. When you zoom into the timeline, drag the **scrollbar under it** to move along the video. Its handle shows which part is visible. Use **View → Reset layout** to go back to the defaults.
- **No more accidental changes:**
  - Scrolling the mouse wheel over a drop-down, slider or number box no longer changes it. You now click into the control first. This was the main cause of settings changing on their own.
  - A plain click on a cut in the timeline no longer nudges it; you have to actually drag.
  - Opening a project, switching tabs or selecting scenes never changes anything.
- **Undo / redo** (Ctrl+Z / Ctrl+Shift+Z) for every edit. You're asked to Save, Discard or Cancel before New, Open or Exit when there are unsaved changes. Projects are no longer auto-saved silently; use Ctrl+S.
- **Sound effects fixed:**
  - Each effect's volume is now matched to your voice, so quiet sounds like typing are audible (about 4–10 dB under the voice, depending on the level).
  - The **Audio** panel shows exactly which sound library is in use.
  - The **Sound effects** tab lists every planned effect with its time, reason (keyword / transition / text card) and file. You can preview, mute, re-time, change the level, add or remove effects, and your edits are kept at render and in the saved project.
  - The Log lists every effect used in each render.
- **Project settings** (Ctrl+,) holds the speech model and language.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S | New / Open / Save / Save as |
| Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) | Undo / Redo |
| F5 / Shift+F5 | Analyze voiceover / Re-plan scenes |
| Space · PgUp / PgDown · ← / → | Play/pause · previous/next scene · back/forward 1 s |
| Ctrl+B / Ctrl+D / Ctrl+M / Ctrl+R | Split at playhead / Duplicate / Merge with next / Replace visual |
| Ctrl+I | Import media files |
| Ctrl+= / Ctrl+- / Ctrl+0 | Timeline zoom in / out / fit |
| F7 | Quality check |
| Ctrl+Shift+D / Ctrl+E / Ctrl+Shift+E | Draft preview / Final export / Editor package |
| Esc | Cancel render / analysis |
| Ctrl+Up / Ctrl+Down / Ctrl+Shift+M | Preview volume up / down / mute |
| Ctrl+J | Show / hide the lower panel tabs |
| Ctrl+L | Match images to narration |
| F11 | Theater mode (big preview) on / off |
| Ctrl+W | Cancel project (back to setup) |
| Delete (timeline focused) | Delete the selected clip (a scene merges into its neighbour) |
| F1 | Shortcut list |

## New in v3

- **Quality check** (tab under the timeline, and it also runs automatically before every render). It gives a score out of 100 and flags things that make a video feel templated or slow: images held too long, repeated visuals, the same camera move over and over, a slow opening, no captions, no text cards, loud music, dead air. Double-click an item to jump to that scene. It **can't predict YouTube's decisions**; it helps you make a better, more original edit.
- **Style variety:** "Different style mix for every video" gives each video its own mix of motion and transitions, so your uploads don't all look like one template. **🎲 Shuffle style** tries a different mix for the same video, but it resets your manual scene edits.
- **Hook:** type a **Hook title** (a question or promise) to show it big over the first seconds with a bass-drop sound. **Faster cuts in the first 10 seconds** splits long opening shots into quicker ones.
- **Text & Chapters tab:**
  - **Text cards** added at the playhead:
    - place/date label with a typewriter reveal and typing sound
    - lower third (name + role)
    - title card
    - chapter card
    - hook title

    Edit the text and times directly in the table.
  - **Auto chapters** finds section breaks. You can rename them, show them as chapter cards in the video, and use **Copy YouTube description text** to paste the timestamps into your description. YouTube needs 0:00 first, at least 3 chapters, and each one 10 s or longer; the app warns you if these aren't met.
- **Captions & fonts:**
  - font picker (Roboto, Lato and Source Sans Pro are included, plus the fonts on your PC)
  - text and highlight colour
  - position (bottom, middle or top)
  - size
  - animation (Pop-in or Word bounce)

  In 9:16 the captions stay clear of the Shorts buttons and title area.
- **Loudness:** the final mix is normalized to -14 LUFS, a commonly used target for YouTube, so your videos aren't too quiet or too loud next to others.
- **SFX library:** 27 built-in categories with 3 variations each, including typing, typewriter, key and mouse clicks, whoosh, swoosh, swipe, riser, impact, boom, bass drop, cinematic hit, punch, pop, ding, cash, camera shutter, clock, heartbeat, glitch, error, page turn, tension, rumble, sparkle and engine rev. They were all synthesized from scratch by the app, so they're free to use. They trigger on transitions, text cards and keywords (edit `sfx_rules.json`). Risers are timed to **end** on the moment they lead into. Your own SFX folder takes priority over the built-in sounds.

## 2. Workflow

1. **Voiceover**: your MP3/WAV (ElevenLabs output works fine).
2. **Script .txt** (optional): your script text. Whisper sometimes misspells names (for example "Zimmermann"). With a script, the captions use your exact spelling while keeping Whisper's timing.
3. **Images / clips folder**: files are used **in file-name order**, so name them `scene_01.jpg`, `scene_02.jpg`, … Clips (`.mp4`, `.mov`) can be mixed in.
4. Pick a **preset**, **caption style**, **atmosphere** and **Scenes** mode:
   - **Auto – split at pauses**: the app decides scene lengths from the narration's pauses and sentence ends, within the preset's min/max length.
   - **One scene per media file**: exactly as many scenes as you have files, cut at the best pauses. **Use this when you made one image per script scene.**
   - **Timestamp file names**: files named `0-00.png`, `0-03.png`, `1-15.png` start at that time. With "Snap to nearest pause" on, each cut moves to the nearest pause within 0.6 s. If the last image would stay on screen much longer than the others, the log warns you, because that usually means the timestamps are wrong.
5. Click **ANALYZE VOICEOVER**.
6. **Preview and adjust:**
   - **Player:** press **Play** or **Space**; **←/→** jump 1 s. Tick or untick Captions and Effects, and switch between a 16:9 and 9:16 preview. Sound plays when your PC has an audio device.
   - **Timeline:** click to jump. **Drag the edge between two scenes** to retime a cut; with *Snap to pauses* on, it sticks to gaps in the speech. **Ctrl + mouse wheel** zooms, the wheel scrolls, and **Fit** shows everything.
   - **Split at playhead / Merge → / Swap →** edit the scene list.
   - **Clip panel (right):** set Zoom, Position X/Y and Rotation for the selected scene, plus its motion, motion amount and transition. Use **Apply zoom/position to all** to copy the settings to every scene, and **Replace visual…** to swap the image or clip.
7. Click **RENDER VIDEO**. Files are saved as `<name>_16x9.mp4` and `<name>_9x16.mp4`.

The project autosaves as `<name>.autoedit.json` in the output folder. Use **Open project** to come back and re-render later.
**Re-plan scenes** applies a new preset or scene mode without transcribing again.
Use **Draft preview** (header button, Ctrl+Shift+D) for a quick 480p check, then **Export** at 1080p.

## Atmosphere effects (new in v2)

Choose one in **Style → Atmosphere** and set its **Strength**. The effects are generated by the app itself, so no stock files are needed.

| Atmosphere | What it adds |
|---|---|
| Vintage Document | sepia tint, paper texture, fine grain, a few dust specks, vignette |
| Old Film | stronger sepia, grain, dust, hairs, scratches, flicker |
| Dreamy Glow | soft glow, warm light leak, floating particles |
| Dark Noir | black & white, contrast, grain |
| Warm Cinematic / Cold Thriller / Teal & Orange | colour grades |
| Floating Dust (subtle) | slow drifting particles |

- **Overlay video:** optional. Pick any MP4 you have the rights to (dust, light leaks, snow…). It loops and is blended in "screen" mode at the opacity you set.
- **Odd-shaped images:** when an image's shape differs a lot from the video (a landscape image in a 9:16 Short, or a portrait image in 16:9), **Blur background** shows the whole image over a blurred copy of itself. **Crop to fill** zooms in to fill the frame instead.

## Export to CapCut / Premiere / Resolve (new in v2)

**Export editor package** creates a `<name>_edit_package` folder containing:

- `clips/001_scene.mp4, 002…`: one clip per scene, with motion and atmosphere applied but no captions or transitions, each exactly its scene length
- `audio/voiceover.wav` and `audio/mix.wav`
- `captions.srt`, `scenes.csv`, and a `README.txt` with CapCut import steps

Put the clips back to back from 0:00 with the audio, and they line up with the voice.
CapCut's own project format is private and changes between versions, so AutoEditor exports these standard files rather than a CapCut project.
**Export captions .srt** saves just the subtitles, for example to upload to YouTube.

## 3. Sound effects

The built-in library in `sfx_library` works with no setup. To add your own sounds, create a folder with category sub-folders (for example `MySFX/whoosh/*.wav`) and choose it as "SFX library". Your files replace the built-in ones for those categories.

- **Transitions:** whip plays a swoosh, slide plays a swipe, zoom plays a whoosh, and dip to black plays an impact.
- **Text cards:**
  - hook: bass drop
  - title: cinematic hit
  - chapter: impact
  - label: typing
  - lower third: swipe
- **Keywords:** words in the narration trigger sounds, for example "telegram" plays a typewriter and "money" plays cash. Edit `sfx_rules.json` to change them.

For well-known viral sounds, use sources that license them for YouTube, such as YouTube's Audio Library or a paid SFX pack.

The **Sound effects** level controls how many are used: None, Minimal (transitions only), Normal or Cinematic.
Only use sounds you own or have a license for.

Background music loops if it's shorter than the video, fades in and out, and automatically ducks (gets quieter) under the voice.

## 4. Presets

| Preset | Scene length | Feel |
|---|---|---|
| Documentary | 3–8 s | slow zooms, crossfades, dip-to-black between sections |
| Dark Psychology | 3.5–9 s | slower moves, strong vignette, desaturated |
| Energetic (Cars) | 1.8–4.5 s | stronger push-ins, whip, slide and zoom transitions |
| Shorts / Reels (fast) | 1.5–3.5 s | quick cuts and punchy motion |

To tune a preset, edit the numbers in `autoedit/presets.py`.

## 5. Speed (your PC has no NVIDIA GPU, which is fine)

- **Transcription** runs on the CPU. `small` is the best balance. `base` is faster but less accurate, and `medium` is more accurate but slower.
- **Rendering**: see *Measured render speed* in the v7 section. In my 2-core test environment v7 Standard rendered 1080p at about 33 frames per second (60 s of video in ~55 s), so a 10-minute video would take very roughly 9-10 minutes there. Your PC may be faster or slower - treat this as an estimate.
- **Encoder → Auto** tries your Intel (QuickSync) or AMD GPU encoder first, which can be much faster, and falls back to the CPU if it isn't available. Whether GPU encoding works depends on your graphics chip and the FFmpeg build. If Auto always says "CPU", installing full FFmpeg may help: open a terminal and run `winget install Gyan.FFmpeg`, then restart the app.

## 6. Captions

The styles are: Modern (active word highlighted), Boxed word (TikTok style), Documentary (clean sentence case), Minimal (text on a dark box), and None. Choose the font, colours, position, size and animation in **Captions & fonts**.
Any `.ttf` or `.otf` file you drop into the `fonts` folder appears in the font list (for example Montserrat, Bebas Neue or Anton, all free from Google Fonts).

## 7. Command line (batch jobs)

```
venv\Scripts\python autoedit_cli.py analyze --voice vo.mp3 --media images --scene-mode match_media --out ep1.json
venv\Scripts\python autoedit_cli.py render ep1.json
venv\Scripts\python autoedit_cli.py render ep1.json --package     (editor package)
```
Scene modes: `smart` (default), `auto`, `match_media`, `timestamps`, `script_cues`. Export: `--aspect 16:9 --resolution "Full HD" --fps 30 --enc-quality Standard` (one video); `--batch-formats 9:16,1:1` adds more. `--prompts prompts.csv` and `--scene-map map.csv` import descriptions / a scene map. Use `--atmosphere "Vintage Document"` to set an atmosphere.

## Tests

Run these from the AutoEditor folder with `venv\Scripts\python -m tests.<name>`:

- `test_core`: scenes, captions, effects, chapters
- `test_sfx`: plays a trigger word through planning, mixing and the final MP4, and checks the effect can be heard
- `test_gui_state`: clicks and scrolls around the window and checks that nothing changes by accident; then V2 add/drag/trim, keyframes, text cards, caption edits, media import/remove, autosave and delete-project-file
- `test_codec_fallback`: a clip in a codec the preview decoder can't read still previews, thumbnails and reports its length
- `test_cues`: script markers, CSV cues, the Match images dialog and scene timing
- `test_stock`: fake Pexels/Pixabay server - missing/invalid keys, empty search, 24 h cache, failed and interrupted downloads, provider down, adding a clip to a scene (asks before replacing), credits, keys never saved in the project
- `test_planner`: v7 acceptance checks 1-2 - meaning matching with imported prompts, missing visual flagged, renaming files changes nothing, locks survive re-planning and reload, script-vs-recording check, scene instructions, scene-map validation
- `test_v7_export`: checks 3-7 on real exported files - 1920×1080 square pixels, no unintended borders in Fill, portrait/square sources, preview = export (PSNR), fit modes, border detection, one export = one file, never overwrite, metadata + captions in 16:9 / 9:16 / 1:1, GPU fallback, cancel / missing file / failed verification
- `test_v7_gui`: Script / Instructions / Review / Framing / Export controls and old-project migration
- `test_scene_sheet`: the SCENE-block format - parsing/reporting, strict None defaults, export format, real 1920×1080 export, editing in the window writes back into the sheet
- `test_timed_plan`: the required timed edit plan - template, line-by-line errors, exact build, real export, window
- `test_edit_instructions`: v7.1 verification 1-10 on real exports (see the v7.1 section)
- `test_instructions_gui`: the Editing instructions window and project guidelines
- `test_v6_render`: builds a small project (voice, 5 images, 1 clip, music, SFX, captions, crossfade, keyframes, V2 clip), renders Draft + Final in 16:9 and 9:16, and measures keyframe motion, captions, V2, crossfade, SFX loudness, music fades and ducking, draft-vs-final time, and that Cancel leaves the media untouched

## Troubleshooting

- **No sound effects:** check the **Audio** panel, which shows the library status. Make sure the level isn't "None", then look at the **Sound effects** tab or the Log. The Log explains why no effect was placed (for example, no triggers in the video, or the library is missing).

- **"FFmpeg not found"**: run `setup.bat` again, or run `winget install Gyan.FFmpeg`.
- **Transcript is wrong or in the wrong language**: set Language (for example `en`), choose a larger speech model, then delete the `<voiceover>.<model>.words.json` file next to your voiceover and analyze again.
- **Captions misspell names**: add your script `.txt`.
- **Console shows `Could not find decoder for codec_id=61` (or another number):** one of your clips uses a video codec (61 = PNG, common in .mov files with transparency) that the preview decoder can't read. Since v6.0.2 AutoEditor makes a small preview copy of that clip once (in `cache/proxies`) and uses it for the preview and thumbnails. The final render always reads your original file with the full FFmpeg. The first preview of such a clip can take a few seconds.
- **A render failed**: check `_autoedit_work\encode_*.log` and `ffmpeg_log.txt` in the output folder.

## About YouTube monetization

No editor can guarantee monetization, and AutoEditor doesn't try to hide AI use or trick detection. YouTube's July 2026 policy update targets generic, repetitive, mass-produced videos. What helps is real originality:

- your own script, research and point of view
- a varied edit
- your own editorial layer (labels, chapters, analysis)
- real footage where you can
- accurate disclosure when YouTube requires it

The quality check and variety tools support that. The creative choices are yours. Check YouTube's current Partner Program policies yourself, because they change.

## Not included yet

- AI image recognition for matching (v7 matches by imported prompts / descriptions only)
- face-aware zoom
- caption pop animations
- audio from video clips (clips play muted)
- ducking in the live preview (the preview plays music at a fixed volume; the render ducks it)
- per-keyframe easing choices (all keyframes use a smooth ease in/out)

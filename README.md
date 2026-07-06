# ComfyUI-WanAni-SQR

# [简体中文](README_CN.md) 

An automated long-video segment queue runner focused on ComfyUI core WanAnimate workflows, supporting segmented generation, transition injection, auto scene switching, breakpoint resuming, auto merging, and audio sync.

## Update 2026-JUL-06 — Director Precision Controls and SCAIL-2 Assistance

## Summary--
Enhance WAN ANI DIRECTOR with per-reference color matching, per-segment SAM3 subject locking, automatic shot-cut detection, and dynamic timeline cleanup while ensuring matched references are used by the actual SCAIL-2 Multi Ref generation path.

## Description--
This update expands WAN ANI DIRECTOR from a segmentation and reference manager into a precision-control surface for SCAIL-2 workflows.

- Extracted scale-guide frames are now available through a dedicated `IMAGE` output for optional downstream use.
- Every reference image has an independent manual Color Match preview and strength value. Final jobs use the same `mkl` behavior as `ColorMatchV2` and route each matched result into the real Multi Ref inputs instead of affecting only the UI preview.
- Added per-segment SAM3 manual tagging. The Director extracts the segment's first frame, accepts positive subject points and negative exclusion points, converts normalized clicks to the active video resolution, and injects the resulting initial mask into the driving-video tracker.
- SAM3 marks are invalidated and re-extracted when Load Video skip, frame-rate, or sampling settings change.
- Added lightweight hard-cut detection using adjacent-frame pixel differences and color-histogram distance. Detected cuts can create shot segments for independent SAM3 tagging.
- The 60-frame minimum remains active with continuous transition enabled. With transition disabled, cutting, boundary dragging, equal segmentation, and shot splitting can create shorter segments.
- Segment chips now include a direct close region. Old segments extending beyond the current effective Load Video range are highlighted and can be removed without changing valid segment ranges.
- Fixed segmented reference inheritance, replacement-state propagation, per-segment Video Combine previews, transition-off hard concatenation, and Windows log decoding issues.

Restart ComfyUI and hard-refresh the browser after updating so the new Python nodes, backend routes, and Director frontend are loaded.

## Update 2026-JUL-05 — WAN ANI DIRECTOR

## Summary--
Add a full visual directing console for manually segmenting reference videos, assigning per-segment references and prompts, controlling replacement or animation behavior, and preparing reference-image proportions before queued WanAnimate or SCAIL-2 generation.

## Description--
`WAN ANI DIRECTOR` is a timeline-oriented controller built specifically for the WanAni SQR execution model. It keeps the existing segmented queue, checkpoint, audio, transition, and merge pipeline while replacing equal-only segmentation with a visual editing workflow.

Main features:
- Responsive reference-video preview with synchronized playback, scrubbing, exact source metadata, and live updates for `force_rate`, `skip_first_frames`, `frame_load_cap`, and `select_every_nth`.
- Manual Cut workflow and draggable segment boundaries, with a strict 60-frame minimum, unique segment colors, deletion gap repair, and 1–100 equal-segment generation.
- Per-segment ordered reference groups with drag sorting, adaptive thumbnails, original-resolution labels, person/background tagging, and forward inheritance when later segments leave references empty.
- Single-reference mode sends only the first image and forces it to be a normal person reference. Multi Ref mode injects the complete ordered group and synchronizes SCAIL-2 Colored Mask identity settings.
- Global motion/expression versus character-replacement control, with replacement state propagated to every segment, Colored Mask Advanced, and the SCAIL-2 transition node.
- Per-segment Positive prompts exposed through a new `STRING` output. Empty later prompts inherit the most recent prompt; the first segment is required.
- Two transition behaviors: ON retains visual/latent smoothing; OFF runs every segment independently from its own references and contiguous source-motion frames, then hard-concatenates the exact visible ranges.
- Guide-frame extraction at the playhead plus a visual reference-scale editor. References can be overlaid, dragged, scaled, and saved at their original resolution with gray padding or crop-on-zoom.
- Live per-segment Video Combine previews, browser client binding, bilingual Chinese/English UI, responsive node sizing, resumable checkpoints, audio alignment, and final video merging.

Restart ComfyUI and refresh the frontend after updating because this release changes both Python node schemas/routes and JavaScript UI extensions.

## Update 2026-JUL-01

## Summary--
Fix Load Video start-frame offsets, remember the most recently used segment count across workflows, and support source videos without audio streams.

## Description--
This update improves segmented queue timeline accuracy and everyday workflow usability.

Changes include:
- Preserved the original `VHS_LoadVideo.skip_first_frames` value when the queue creates per-segment jobs. Segment offsets are now added to the configured source offset instead of replacing it, preventing skipped opening frames from incorrectly becoming missing frames at the end.
- Applied the same corrected source offset to audio slicing so picture and sound remain aligned.
- Saved the most recently selected segment count in browser-local settings and restored it after switching workflows, instead of resetting the segment control to the default value of `2`.
- Added audio-stream detection before creating `VHS_LoadAudioUpload` nodes. Videos without an audio stream now continue through the queue as silent video instead of failing during audio extraction.
- Removed stale audio inputs from full and trimmed segment outputs when the source is silent.

Restart ComfyUI and refresh the frontend after updating so both backend and UI changes take effect.

## Update 2026-JUN-30

## Summary--
Add resumable folder-based image processing, automatic per-image queue continuation, progress reporting, footer branding, and source-aware output folders for Flux.2 Klein batch restyling workflows.

## Description--
This update adds a production-oriented image batch pipeline alongside the existing WanAnimate and SCAIL queue tools.

New nodes:
- `LH Images Folder Loader`: Loads images without normalizing their original dimensions. Its resumable sequential mode records the folder image count, current index, manifest, active file, and independent run ID. Interrupted jobs resume from the last successfully saved image.
- `LH Save Image (Passthrough)`: Saves each completed image before automatically queuing the next one. It prevents stale duplicate jobs, preserves browser preview updates, expands date tokens, records checkpoints, and creates a source-aware output directory named `<source-folder>-f2kmd`.
- `LH Image Footer Bar`: Optionally adds a solid white bar with black text or a solid black bar with white text. The text, bar height, font size, and padding are configurable, with automatic fitting for long account names and URLs.

Included workflow:
- `example_workflows/f2k-reskin.json`: A basic Flux.2 Klein 9B folder-restyling workflow with automatic aspect-preserving ~1.5 MP sizing, resumable one-image-at-a-time processing, progress display, optional footer branding, and organized output folders.

Reliability improvements:
- Each automatic queue step receives an explicit image index so ComfyUI cannot reuse the previous image cache.
- Each reset creates a new run ID, allowing the same folder to be processed repeatedly without colliding with duplicate protection from an older run.
- Checkpoints advance only after a successful save. Stale prompts cannot move progress backward or save duplicate output.
- Folder sorting, start index, and load cap are included in the checkpoint identity.
- The loader displays current/total progress, percentage, and active filename directly on the node.
- Runtime checkpoint files are excluded from Git.

## Update 2026-JUN-26

## Summary--
Add a production-tested SCAIL-2 Multi Reference segment workflow with grouped references, bilingual controls, startup flash suppression, and safer transition continuity.

## Description--
This update focuses on the new SCAIL-2 multi-reference segmented queue path and the issues found during longer real workflow tests.

Changes include:
- Added grouped Multi Reference support inside `WanAni SQR`, allowing each segment to use its own ordered reference image group while keeping the normal segmented queue flow.
- Added `Wan SQR Multi Reference` improvements for width/height input, crop-to-video-aspect handling, keep mode, and optional `match and fill` padding for mixed-size reference images.
- Added `LH Resolution Setting` with bilingual landscape/portrait choices, standard presets, and slightly smaller `safe` presets from 480p upward.
- Added top-bar toggles for Multi Ref, Startup Fix, Replacement, and Transition, plus a `中/EN` language switch for the WanAni SQR interface.
- Synced Multi Ref and Replacement toggles to SCAIL-2 colored mask identity/replacement behavior.
- Added single-person multi-reference and multi-person multi-reference colored mask modes, with preview support for every reference mask instead of only the first mask.
- Added clean background reference support for SCAIL-2 Multi Reference groups. Each reference image can be marked as `BG`; BG references are passed to the colored-mask node as background indexes and receive full-white reference masks, matching SCAIL-2's official background-reference semantics.
- Added a Startup Fix path for SCAIL-2 Multi Reference: the first segment can prepend 9 repeated first frames, extend generation length, then hide those startup frames from the visible output to reduce reference-image flashes.
- Aligned the internal transition source after Startup Fix so later segments use the visible timeline instead of the hidden startup buffer.
- Preserved 17/16-frame SCAIL-2 transition behavior while avoiding duplicated motion on transition segments.
- Improved latent/RGB transition handling for SCAIL-2 segmented runs, including safer latent carry and transition source preparation.
- Improved frontend state persistence so workflow switching is less likely to reset toggles, selected images, or resume-video state.
- Avoided extension conflicts by ensuring WanAni SQR uses its own S&R node name and by removing duplicate backup-extension loading from the active test setup.
- Added `WAN ANI DIRECTOR` as a separate experimental director-style node without changing the existing WanAni SQR node.

Testing notes:
- The new Startup Fix path is intended for SCAIL-2 Multi Reference runs where reference images may briefly flash at the beginning.
- Mark only clean background references as `BG`; character reference images should keep the normal semantic color mask so SCAIL-2 does not treat unrelated backgrounds as target content.
- For normal transition segments, Startup Fix is skipped so the 17/16 transition path remains responsible for motion continuity.
- If a workflow appears to lose the new UI controls after updating, restart ComfyUI and clear browser cache; duplicate backup copies of this extension inside `custom_nodes` can load older frontend scripts.

## Update 2026-JUN-24

## Summary--
Improve WanAni SQR state reliability, SCAIL-2 transition shape safety, multi-reference mask pairing, and resolution preset clarity.

## Description--
This update focuses on stability and workflow safety after the recent SCAIL-2 multi-reference and segmented transition work.

Changes include:
- Fixed Resume Video behavior so a stored resume path only takes effect when Resume is explicitly enabled.
- Improved frontend state persistence around resume selection, workflow switching, and top toggle button alignment.
- Added safer boolean/integer parsing for older workflows or widget-order edge cases.
- Prevented invalid empty segment generation when segment count exceeds usable frame count.
- Added standard and smaller `safe` resolution presets to `LH Resolution Setting`, with explicit width x height labels and bilingual landscape/portrait options.
- Aligned SCAIL-2 reference and driving masks to actual latent dimensions to avoid shape mismatches such as `53 vs 54`.
- Repeated the last available reference mask when a multi-reference batch has more images than masks.
- Updated WanAnimate transition latent sizing to follow actual VAE-encoded dimensions.
- Added a dedicated internal full-segment transition source per segment so later segments can reliably use the previous segment as transition material.

Safe resolution note:
- Standard presets are preserved, for example `16:9 1080p - 1920 x 1080`.
- Extra safe presets are slightly smaller and aligned to safer multiples, for example `16:9 1080p safe - 1920 x 1072`.
- For SCAIL-2 and WanAnimate transition workflows, width and height values divisible by 16 are recommended; 32-multiple sizes are even safer.

## Integrated Transition Nodes

The functionality previously provided by the separate `SQR-WAN-Transition`
plugin is now included directly in this project:

- `SQR WanAnimate Transition To Video`
- `SQR SCAIL2 Transition To Video`
- Animation and Replacement strategies for SCAIL2
- Model-specific transition loading, trimming, merging, and audio alignment

Only `ComfyUI-WanAni-SQR` should be enabled. Keeping the old standalone
transition plugin enabled at the same time can register duplicate node names.

## ✨ Key Features
- Segmented Generation: Automatically split long videos to avoid out-of-memory errors
- Seamless Transitions: Use last frame of previous segment for smooth continuity
- Auto Scene Switch: Support multi-reference images for style/character changes
- Breakpoint Resume: Continue from any segment after interruption
- Auto Merge: Automatically combine clips into a complete video
- Audio Sync: Auto-extract and align audio from source video
- Preview Mode: Check segment plan before rendering

## 📦 Installation
cd ComfyUI/custom_nodes

git clone https://github.com/zere111ai/ComfyUI-WanAni-SQR.git

## 📢 Changelog

### [v2.4] - 2026-04-06
**Core Update: Adaptive Enhancement & UI/UX Optimization**
- **ComfyUI Port Auto-Recognition**: Automatically adapt to local usage and remote calls (RH adaptation pending KJ's wrapper node merge)
- **UI Style Unification**: Modified and unified the style of partial button UI elements
- **Execution Mode Highlight**: Added edge highlight distinction for execution modes, with toggle switch in settings
- **Slider UI for Segmentation**: Replaced segment count/start segment input with draggable sliders, optimized maximum segment count settings for better usability
- **Native Popup Optimization**: Removed redundant built-in selectors, only retained Windows (local) or browser (remote) native popups for selecting images/videos
- **Reference Image Management**: 
  - Drag to sort selected reference images (hold left click)
  - Remove images (right click)
  - Duplicate images (left click) to reuse the same image multiple times (no need to replace images for unchanged scenes/styles)
- **File Naming Optimization**: Replaced random run identifiers in `sqr_cut_*`/`sqr_trans_*`/`sqr_merged_*` with sortable timestamps (time code format), maintaining anti-overwrite capability while improving file identification; breakpoint resume logic adapted accordingly
- **Dependency & Log Enhancement**: Added cv2 missing error logging, specified `opencv-python>=4.8` dependency

### [v2.0] - 2026-04-03
**Core Update: Multi-Task Parallel Queue Support**
- **New Task Queue**: Support for simultaneous submission of multiple generation tasks.
- **Random Interleaved Sampling**: Implemented random interleaved sampling logic between different tasks.
- **Dynamic Priority Merging**: "First-finished, first-merged" strategy to optimize workflow.

**Bug Fixes:**
- **Fixed Preview Error**: Resolved the issue where previews in the image selection box displayed incorrectly.
- **Fixed Segment Misalignment**: Corrected the potential misalignment between segmented samples during multi-task parallel processing.
- **Fixed Video Overwriting**: Resolved a critical bug where final video merges could be overwritten during multi-task execution.

## 🚀 Quick Start
1. Connect frame_count and fps from Load Video to this node
2. Set segment count, turn off Run to preview the plan first
3. Bind nodes via buttons:
   - Source Video Node (Load Video)
   - Output Node (VHS_VideoCombine)
   - Motion Embedding Node (WanVideoAnimateEmbeds)
4. Turn on Run to start automatic generation

## 🛠 Modes
- Preview: Show segment plan only, no rendering
- New Generation: Render from segment 1 and auto-merge
- Resume: Continue from interrupted video seamlessly

## 📌 FAQ
- Node ID empty: Bind required nodes using the on-node buttons
- Resume after interruption: Set start segment → select last video → enable resume → run
- Output path: output/sqr_merged_xxx.mp4
- ffmpeg missing: Install ffmpeg and add to system PATH

## 👥 Authors
FX-FeiHou & XueZi & wuwukaka

## 📄 License
MIT License

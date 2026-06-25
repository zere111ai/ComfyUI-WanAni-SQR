# ComfyUI-WanAni-SQR

# [简体中文](README_CN.md) 

An automated long-video segment queue runner focused on ComfyUI core WanAnimate workflows, supporting segmented generation, transition injection, auto scene switching, breakpoint resuming, auto merging, and audio sync.

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
- Added a Startup Fix path for SCAIL-2 Multi Reference: the first segment can prepend 9 repeated first frames, extend generation length, then hide those startup frames from the visible output to reduce reference-image flashes.
- Aligned the internal transition source after Startup Fix so later segments use the visible timeline instead of the hidden startup buffer.
- Preserved 17/16-frame SCAIL-2 transition behavior while avoiding duplicated motion on transition segments.
- Improved latent/RGB transition handling for SCAIL-2 segmented runs, including safer latent carry and transition source preparation.
- Improved frontend state persistence so workflow switching is less likely to reset toggles, selected images, or resume-video state.
- Avoided extension conflicts by ensuring WanAni SQR uses its own S&R node name and by removing duplicate backup-extension loading from the active test setup.
- Added `WAN ANI DIRECTOR` as a separate experimental director-style node without changing the existing WanAni SQR node.

Testing notes:
- The new Startup Fix path is intended for SCAIL-2 Multi Reference runs where reference images may briefly flash at the beginning.
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

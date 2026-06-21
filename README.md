# ComfyUI-WanAni-SQR

# [简体中文](README_CN.md) 

An automated long-video segment queue runner focused on ComfyUI core WanAnimate workflows, supporting segmented generation, transition injection, auto scene switching, breakpoint resuming, auto merging, and audio sync.

## Update 2026-JUN-21

## Summary--
Add segmented multi-reference grouping controls, synchronized SCAIL-2 mode toggles, persistent WanAni SQR UI state, and robust multi-reference image size handling.
## Description--
This update improves WanAni SQR multi-reference workflows for SCAIL-2 segmented generation. The SQR node now supports grouped reference images per segment, with each segment able to receive its own multi-reference set. Multi Ref ON/OFF synchronizes SCAIL-2 Colored Mask identity mode between single_person_multi_reference and multi_person, while the new Replacement ON/OFF button synchronizes replacement_mode across the advanced colored mask and SCAIL-2 transition nodes. The SQR frontend state is persisted more reliably when switching workflows, reducing unexpected toggle resets, missing images, or resume-video activation.
Wan SQR Multi Reference now includes match_and_fill, which pads mixed-size reference images to the largest group canvas using a white background before keep/crop processing. This prevents mixed-resolution reference batches from failing in keep mode while preserving image content without scaling.


## New Nodes 2026-JUN-17

Add SCAIL-2 multi-reference support to WanAni SQR with segment-queue integration and resolution-aware reference handling.
Description
This update adds an experimental SCAIL-2 multi-reference pipeline for WanAni SQR.

Changes include:
Added Wan SQR Multi Reference node for ordered multi-image reference batching.
Added LH Resolution Setting node with landscape/portrait presets, common aspect ratios, 480p-to-4K options, and manual width/height override.
Added Multi Ref ON/OFF toggle to the WanAni SQR top bar.
When Multi Ref ON, WanAni SQR now sends its selected reference image list into Wan SQR Multi Reference in order.
When Multi Ref OFF, WanAni SQR keeps the original per-segment single-reference behavior.
Added SCAIL-2 colored mask advanced mode for single-person multi-reference and multi-person grouping.
Added official-style SCAIL-2 additional reference image/mask support.
Added reference batch splitting for main reference and additional references.
Fixed SCAIL-2 context-window mask slicing for multi-reference timelines.
Updated SCAIL-2 transition handling to use the 17/16 frame transition path with latent carry support.
Improved reference mask preview so all reference masks can be viewed, not only the main reference mask.
Updated frontend node ID selection and SQR controls for multi-reference workflows.

## Integrated Transition Nodes 2026-JUN-14

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

### 20260614
中文：
新增 SCAIL2 Transition 节点识别与动态过渡时长支持。队列会根据 WanAnimate 或 SCAIL2 自动调整过渡视频读取、裁切、合并及音频偏移，同时包含节点界面、图片加载和 Node ID 稳定性优化。

English:
Added SCAIL2 Transition node detection and model-specific transition timing. The queue now automatically adjusts transition loading, trimming, merging, and audio offsets for WanAnimate and SCAIL2, with additional UI, image loading, and Node ID reliability improvements.



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

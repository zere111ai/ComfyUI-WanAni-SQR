const { app } = window.comfyAPI.app;

const CUTTER_STYLE_ID = "lh-video-cutter-style";
function cutterStyles() {
    if (document.getElementById(CUTTER_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = CUTTER_STYLE_ID;
    style.textContent = `
      .lhvc{font:12px system-ui;color:#ddd;display:flex;flex-direction:column;gap:8px;padding:3px;box-sizing:border-box}.lhvc *{box-sizing:border-box}
      .lhvc-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.lhvc button,.lhvc input,.lhvc select{border:1px solid #444;background:#242424;color:#ddd;border-radius:5px;padding:5px 7px;font-size:11px}.lhvc button{cursor:pointer}.lhvc button:hover{border-color:#888}.lhvc button.primary{background:#246044;border-color:#58bd88}.lhvc button.danger{color:#ffaaa0}.lhvc-execute{justify-content:center;padding-top:2px}.lhvc-execute button{min-width:120px}
      .lhvc-path{flex:1;min-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8fd;font:11px ui-monospace,monospace}.lhvc-video{width:100%;height:300px;min-height:150px;max-height:460px;background:#080808;border-radius:7px;object-fit:contain}.lhvc-canvas,.lhvc-wave{width:100%;background:#111;border:1px solid #444;border-radius:6px;cursor:ew-resize;touch-action:none}.lhvc-canvas{height:78px}.lhvc-wave{height:72px}.lhvc-canvas:focus,.lhvc-wave:focus{outline:1px solid #8fd;outline-offset:1px}.lhvc-wave-label{display:flex;align-items:center;gap:6px;color:#8ab;font-size:10px;margin-bottom:-5px}
      .lhvc-segments{display:flex;gap:5px;overflow-x:auto;padding:2px}.lhvc-chip{min-width:170px;position:relative;text-align:left;padding-right:25px!important;white-space:pre-line}.lhvc-chip.selected{border-color:#fff;box-shadow:0 0 0 2px #ffffff55 inset,0 0 9px #ffffff44}.lhvc-chip.playhead{border-color:#ffd166;background:#493d20;box-shadow:0 0 0 2px #ffd16688 inset,0 0 10px #ffd16666}.lhvc-chip.selected.playhead{box-shadow:0 0 0 2px #fff inset,0 0 0 4px #ffd16688 inset,0 0 12px #ffd16688}.lhvc-chip i{position:absolute;right:0;top:0;width:24px;height:100%;display:flex;align-items:center;justify-content:center;background:#421f1f;font-style:normal}.lhvc-chip-meta{display:flex;align-items:center;gap:4px;margin-top:4px}.lhvc-chip-meta input[type=text]{min-width:80px;width:112px;padding:3px 5px}.lhvc-chip-meta input[type=checkbox]{margin:0}.lhvc-nav input[type=number],.lhvc-nav input[type=text]{width:82px}.lhvc-nav input[type=range]{width:105px;padding:0}.lhvc-settings label,.lhvc-project label{display:flex;align-items:center;gap:5px;color:#aaa}.lhvc-settings input{width:140px}.lhvc-settings input[data-setting="output_subfolder"]{width:280px}.lhvc-project-path{flex:1;min-width:240px}.lhvc-project-name{width:150px}.lhvc-project-status{color:#8ab;font-size:10px;min-width:120px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lhvc-status{color:#aaa;font-size:10px;flex:1}
      .lhvc-browser{position:fixed;inset:0;z-index:100050;background:#000c;display:flex;align-items:center;justify-content:center}.lhvc-browser-box{width:min(760px,92vw);max-height:86vh;background:#181818;border:1px solid #555;border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:8px}.lhvc-list{display:flex;flex-direction:column;gap:4px;overflow:auto;min-height:260px}.lhvc-list button{text-align:left}.lhvc-browser-head{display:flex;align-items:center;gap:6px}.lhvc-browser-head span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8fd}
    `;
    document.head.appendChild(style);
}

const widget = (node, name) => node.widgets?.find(item => item.name === name);
const value = (node, name, fallback = "") => widget(node, name)?.value ?? fallback;
function setValue(node, name, next) { const item = widget(node, name); if (!item) return; item.value = next; item.callback?.(next); }
function hideWidget(item) { if (!item) return; item.hidden = true; item.computeSize = () => [0, -4]; item.draw = () => {}; if (item.element) item.element.style.display = "none"; }
const clamp = (number, min, max) => Math.max(min, Math.min(max, number));
const segmentColor = index => `hsl(${(index * 71 + 142) % 360} 58% 45%)`;
let activeCutter = null;

class LHVideoCutterUI {
    constructor(node, host, domWidget) {
        this.node = node;
        this.host = host;
        this.domWidget = domWidget;
        this.fps = 0;
        this.totalFrames = 0;
        this.playhead = 0;
        this.scrubbing = false;
        this.resumeAfterScrub = false;
        this.seekFrame = 0;
        this.selectedSegment = 0;
        this.sceneThreshold = 0.38;
        this.minDetectedFrames = 30;
        this.cuts = [];
        this.segmentMeta = {};
        this.detectedCuts = [];
        this.timelineZoom = 1;
        this.viewStart = 0;
        this.activeCutIndex = -1;
        this.snapEnabled = true;
        this.playMode = "normal";
        this.exportAudio = false;
        this.audioFormat = "mp3";
        this.audioBitrate = "192k";
        this.exportScope = "checked";
        this.projectDirectory = "";
        this.projectFilename = "LH_Video_Cutter_Task";
        this.saveOnlySegment = null;
        this.undoStack = [];
        this.redoStack = [];
        this.pointerInside = false;
        this.readCuts();
        this.build();
        this.loadVideo();
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(host);
    }

    readCuts() {
        try {
            const data = JSON.parse(String(value(this.node, "cuts_data", "{}")));
            this.cuts = Array.isArray(data.cuts) ? data.cuts.map(Number).filter(Number.isFinite) : [];
            this.fps = Number(data.fps) || 0;
            this.totalFrames = Number(data.total_frames) || 0;
            this.sceneThreshold = clamp(Number(data.scene_threshold) || 0.38, 0.12, 0.8);
            this.minDetectedFrames = clamp(Math.round(Number(data.min_detected_frames) || 30), 1, 99999);
            this.segmentMeta = data.segment_meta && typeof data.segment_meta === "object" ? data.segment_meta : {};
            this.snapEnabled = data.snap_enabled !== false;
            this.playMode = ["normal", "pause", "loop"].includes(data.play_mode) ? data.play_mode : "normal";
            this.exportAudio = data.export_audio === true;
            this.audioFormat = ["mp3", "wav"].includes(data.audio_format) ? data.audio_format : "mp3";
            this.audioBitrate = ["96k", "128k", "192k", "256k", "320k"].includes(data.audio_bitrate) ? data.audio_bitrate : "192k";
            this.exportScope = data.export_scope === "selected" ? "selected" : "checked";
            this.projectDirectory = String(data.project_directory || "");
            this.projectFilename = String(data.project_filename || "LH_Video_Cutter_Task");
        } catch { this.cuts = []; }
    }

    normalizeMeta() {
        const next = {};
        for (const start of [0, ...this.cuts]) {
            const saved = this.segmentMeta[String(start)] || {};
            next[String(start)] = {name: String(saved.name || ""), enabled: saved.enabled !== false};
        }
        this.segmentMeta = next;
    }

    persist() {
        this.cuts = [...new Set(this.cuts.map(Math.round).filter(frame => frame > 0 && frame < this.totalFrames))].sort((a, b) => a - b);
        this.normalizeMeta();
        setValue(this.node, "cuts_data", JSON.stringify({version: 2, cuts: this.cuts, fps: this.fps, total_frames: this.totalFrames, scene_threshold: this.sceneThreshold, min_detected_frames: this.minDetectedFrames, segment_meta: this.segmentMeta, snap_enabled: this.snapEnabled, play_mode: this.playMode, export_audio: this.exportAudio, audio_format: this.audioFormat, audio_bitrate: this.audioBitrate, export_scope: this.exportScope, project_directory: this.projectDirectory, project_filename: this.projectFilename, save_selected_segment: this.saveOnlySegment}));
        this.node.setDirtyCanvas?.(true, true);
    }

    build() {
        cutterStyles();
        this.host.innerHTML = "";
        const root = document.createElement("div");
        root.className = "lhvc";
        root.innerHTML = `<div class="lhvc-row"><b>LH VIDEO CUTTER</b><button data-browse>选择视频</button><span class="lhvc-path"></span></div>`;
        this.root = root;
        this.pathLabel = root.querySelector(".lhvc-path");
        this.video = document.createElement("video");
        this.video.className = "lhvc-video";
        this.video.controls = true;
        this.video.preload = "metadata";
        root.appendChild(this.video);
        this.canvas = document.createElement("canvas");
        this.canvas.className = "lhvc-canvas";
        root.appendChild(this.canvas);
        const waveLabel = document.createElement("div");
        waveLabel.className = "lhvc-wave-label";
        waveLabel.innerHTML = `<span>音频波形</span><span data-wave-status>等待加载</span>`;
        root.appendChild(waveLabel);
        this.waveStatus = waveLabel.querySelector("[data-wave-status]");
        this.waveCanvas = document.createElement("canvas");
        this.waveCanvas.className = "lhvc-wave";
        this.waveCanvas.tabIndex = 0;
        root.appendChild(this.waveCanvas);
        const nav = document.createElement("div");
        nav.className = "lhvc-row lhvc-nav";
        nav.innerHTML = `<button data-prev-cut title="快捷键 [">◀ 上一切点</button><button data-next-cut title="快捷键 ]">下一切点 ▶</button><label>帧 <input data-frame type="number" min="0" step="1"></label><label>时间 <input data-time type="text" placeholder="00:00.0"></label><button data-cut-left title="Alt+←">切点 -1帧</button><button data-cut-right title="Alt+→">切点 +1帧</button><label>缩放 <input data-zoom type="range" min="1" max="20" step="1" value="1"><span data-zoom-value>1×</span></label><label><input data-snap type="checkbox" checked>切点吸附</label><label>段播放 <select data-play-mode><option value="normal">普通</option><option value="pause">段尾暂停</option><option value="loop">循环当前段</option></select></label>`;
        root.appendChild(nav);
        this.frameInput = nav.querySelector("[data-frame]");
        this.timeInput = nav.querySelector("[data-time]");
        this.zoomInput = nav.querySelector("[data-zoom]");
        this.zoomValue = nav.querySelector("[data-zoom-value]");
        this.snapInput = nav.querySelector("[data-snap]");
        this.playModeInput = nav.querySelector("[data-play-mode]");

        const controls = document.createElement("div");
        controls.className = "lhvc-row";
        controls.innerHTML = `<button data-prev-frame title="后退 1 帧">◀ 1帧</button><button data-next-frame title="前进 1 帧">1帧 ▶</button><button data-cut title="快捷键 C">Cut / 切片</button><button data-detect>检测切镜</button><label>阈值 <input data-scene-threshold type="number" min="0.12" max="0.8" step="0.01" value="${this.sceneThreshold.toFixed(2)}" style="width:62px"></label><label>最短帧数 <input data-min-detected type="number" min="1" step="1" value="${this.minDetectedFrames}" style="width:62px"></label><button data-delete-selected class="danger">删除选中段（与右侧合并）</button><button data-clear class="danger">清除切点</button><button data-undo title="Ctrl+Z">撤销</button><button data-redo title="Ctrl+Shift+Z / Ctrl+Y">恢复</button><span class="lhvc-status"></span>`;
        root.appendChild(controls);
        this.status = controls.querySelector(".lhvc-status");
        this.segmentBox = document.createElement("div");
        this.segmentBox.className = "lhvc-segments";
        root.appendChild(this.segmentBox);

        const settings = document.createElement("div");
        settings.className = "lhvc-row lhvc-settings";
        settings.innerHTML = `<label>输出目录 <input data-setting="output_subfolder"></label><button data-output-browse>浏览…</button><label>文件前缀 <input data-setting="filename_prefix"></label><label>切割方式 <select data-setting="cut_mode"><option value="accurate_h264">精确 H.264（逐帧）</option><option value="fast_stream_copy">快速无损复制（关键帧近似）</option></select></label><span class="lhvc-project-status" data-output-status></span>`;
        root.appendChild(settings);
        this.settings = settings;
        for (const input of settings.querySelectorAll("[data-setting]")) {
            input.value = value(this.node, input.dataset.setting, "");
            input.onchange = () => setValue(this.node, input.dataset.setting, input.value);
        }
        this.outputDirectoryInput = settings.querySelector('[data-setting="output_subfolder"]');
        this.outputStatus = settings.querySelector("[data-output-status]");
        settings.querySelector("[data-output-browse]").onclick = () => this.browseOutputDirectory();
        const audioSettings = document.createElement("div");
        audioSettings.className = "lhvc-row lhvc-settings";
        audioSettings.innerHTML = `<label><input data-export-audio type="checkbox">单独保存音频</label><label>音频格式 <select data-audio-format><option value="mp3">MP3</option><option value="wav">WAV</option></select></label><label>码率 <select data-audio-bitrate><option value="96k">96 kbps</option><option value="128k">128 kbps</option><option value="192k">192 kbps</option><option value="256k">256 kbps</option><option value="320k">320 kbps</option></select></label>`;
        root.appendChild(audioSettings);
        this.exportAudioInput = audioSettings.querySelector("[data-export-audio]");
        this.audioFormatInput = audioSettings.querySelector("[data-audio-format]");
        this.audioBitrateInput = audioSettings.querySelector("[data-audio-bitrate]");
        this.exportAudioInput.checked = this.exportAudio;
        this.audioFormatInput.value = this.audioFormat;
        this.audioBitrateInput.value = this.audioBitrate;
        this.syncAudioControls = () => {
            this.audioFormatInput.disabled = !this.exportAudio;
            this.audioBitrateInput.disabled = !this.exportAudio || this.audioFormat === "wav";
            this.audioBitrateInput.title = this.audioFormat === "wav" ? "WAV 为无压缩 PCM，不使用码率设置" : "";
        };
        this.exportAudioInput.onchange = () => { this.exportAudio = this.exportAudioInput.checked; this.syncAudioControls(); this.persist(); };
        this.audioFormatInput.onchange = () => { this.audioFormat = this.audioFormatInput.value; this.syncAudioControls(); this.persist(); };
        this.audioBitrateInput.onchange = () => { this.audioBitrate = this.audioBitrateInput.value; this.persist(); };
        this.syncAudioControls();
        const projectRow = document.createElement("div");
        projectRow.className = "lhvc-row lhvc-project";
        projectRow.innerHTML = `<b>任务文件</b><label>路径 <input class="lhvc-project-path" data-project-directory placeholder="留空时保存到 ComfyUI/output/LH_Video_Cutter_Projects"></label><button data-project-browse>浏览…</button><label>文件名 <input class="lhvc-project-name" data-project-filename></label><button data-project-save>保存任务</button><button data-project-load>加载任务</button><span class="lhvc-project-status"></span>`;
        root.appendChild(projectRow);
        this.projectDirectoryInput = projectRow.querySelector("[data-project-directory]");
        this.projectFilenameInput = projectRow.querySelector("[data-project-filename]");
        this.projectStatus = projectRow.querySelector(".lhvc-project-status");
        this.projectDirectoryInput.value = this.projectDirectory;
        this.projectFilenameInput.value = this.projectFilename;
        this.projectDirectoryInput.onchange = () => { this.projectDirectory = this.projectDirectoryInput.value.trim(); this.persist(); };
        this.projectFilenameInput.onchange = () => { this.projectFilename = this.projectFilenameInput.value.trim() || "LH_Video_Cutter_Task"; this.projectFilenameInput.value = this.projectFilename; this.persist(); };
        projectRow.querySelector("[data-project-browse]").onclick = () => this.browseProjectDirectory();
        projectRow.querySelector("[data-project-save]").onclick = () => this.saveProject();
        projectRow.querySelector("[data-project-load]").onclick = () => this.loadProject();
        const executeRow = document.createElement("div");
        executeRow.className = "lhvc-row lhvc-execute";
        executeRow.innerHTML = `<b>保存范围</b><label><input type="radio" data-export-scope value="selected">仅保存选中段</label><label><input type="radio" data-export-scope value="checked">保存勾选分段</label><button class="primary" data-execute-save>执行保存</button>`;
        root.appendChild(executeRow);
        this.exportScopeInputs = [...executeRow.querySelectorAll("[data-export-scope]")];
        for (const input of this.exportScopeInputs) {
            input.name = `lhvc-export-scope-${this.node.id}`;
            input.checked = input.value === this.exportScope;
            input.onchange = () => {
                if (!input.checked) return;
                this.exportScope = input.value;
                this.persist();
            };
        }
        executeRow.querySelector("[data-execute-save]").onclick = () => this.save(this.exportScope === "selected");

        root.querySelector("[data-browse]").onclick = () => this.openBrowser();
        nav.querySelector("[data-prev-cut]").onclick = () => this.jumpCut(-1);
        nav.querySelector("[data-next-cut]").onclick = () => this.jumpCut(1);
        nav.querySelector("[data-cut-left]").onclick = () => this.nudgeCut(-1);
        nav.querySelector("[data-cut-right]").onclick = () => this.nudgeCut(1);
        this.frameInput.onchange = () => this.seekToFrame(Number(this.frameInput.value));
        this.timeInput.onchange = () => this.seekToFrame(this.parseTime(this.timeInput.value) * this.fps);
        this.zoomInput.oninput = () => this.setTimelineZoom(Number(this.zoomInput.value));
        this.snapInput.checked = this.snapEnabled;
        this.snapInput.onchange = () => { this.snapEnabled = this.snapInput.checked; this.persist(); };
        this.playModeInput.value = this.playMode;
        this.playModeInput.onchange = () => { this.playMode = this.playModeInput.value; this.playbackSegment = this.segmentAtFrame(this.playhead); this.persist(); };
        controls.querySelector("[data-prev-frame]").onclick = () => this.stepFrame(-1);
        controls.querySelector("[data-next-frame]").onclick = () => this.stepFrame(1);
        controls.querySelector("[data-cut]").onclick = () => this.addCut();
        controls.querySelector("[data-detect]").onclick = () => this.detectCuts();
        this.sceneThresholdInput = controls.querySelector("[data-scene-threshold]");
        this.minDetectedInput = controls.querySelector("[data-min-detected]");
        this.sceneThresholdInput.onchange = event => { this.sceneThreshold = clamp(Number(event.target.value) || 0.38, 0.12, 0.8); event.target.value = this.sceneThreshold.toFixed(2); this.persist(); };
        this.minDetectedInput.onchange = event => { this.minDetectedFrames = clamp(Math.round(Number(event.target.value) || 30), 1, Math.max(1, this.totalFrames || 99999)); event.target.value = this.minDetectedFrames; this.persist(); };
        this.deleteSelectedButton = controls.querySelector("[data-delete-selected]");
        this.deleteSelectedButton.onclick = () => this.mergeSelectedRight();
        controls.querySelector("[data-clear]").onclick = () => { if (!this.cuts.length) return; this.pushUndo(); this.cuts = []; this.activeCutIndex = -1; this.selectedSegment = 0; this.persist(); this.render(); };
        this.undoButton = controls.querySelector("[data-undo]");
        this.redoButton = controls.querySelector("[data-redo]");
        this.undoButton.onclick = () => this.undo();
        this.redoButton.onclick = () => this.redo();
        this.canvas.onpointerdown = event => {
            if (!this.totalFrames) return;
            event.preventDefault();
            this.canvas.focus({preventScroll: true});
            this.scrubbing = true;
            this.resumeAfterScrub = !this.video.paused;
            if (this.resumeAfterScrub) this.video.pause();
            const rect = this.canvas.getBoundingClientRect();
            const view = this.viewBounds(), cursorX = (this.playhead - view.start) / Math.max(1, view.span) * rect.width;
            this.fineScrub = Math.abs(event.clientX - rect.left - cursorX) <= 12;
            const pointerFrame = view.start + (event.clientX - rect.left) / Math.max(1, rect.width) * view.span;
            let nearest = -1;
            for (let index = 0; index < this.cuts.length; index++) {
                if (nearest < 0 || Math.abs(this.cuts[index] - pointerFrame) < Math.abs(this.cuts[nearest] - pointerFrame)) nearest = index;
            }
            this.activeCutIndex = nearest >= 0 && Math.abs(this.cuts[nearest] - pointerFrame) / view.span * rect.width <= 8 ? nearest : -1;
            this.scrubStartX = event.clientX;
            this.scrubStartFrame = this.playhead;
            this.canvas.setPointerCapture(event.pointerId);
            this.scrubToPointer(event);
        };
        this.canvas.onpointermove = event => { if (this.scrubbing) this.scrubToPointer(event); };
        const finishScrub = event => {
            if (!this.scrubbing) return;
            this.scrubbing = false;
            if (this.canvas.hasPointerCapture?.(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
            if (this.fps > 0) this.video.currentTime = this.playhead / this.fps;
            if (this.resumeAfterScrub) this.video.play().catch(() => {});
            this.resumeAfterScrub = false;
            this.selectedSegment = this.segmentAtFrame(this.playhead);
            this.playbackSegment = this.selectedSegment;
            this.render();
        };
        this.canvas.onpointerup = finishScrub;
        this.canvas.onpointercancel = finishScrub;
        this.canvas.onlostpointercapture = event => { if (this.scrubbing) finishScrub(event); };
        const scrubWave = event => {
            const rect = this.waveCanvas.getBoundingClientRect(), view = this.viewBounds(), position = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
            this.playhead = clamp(Math.round(view.start + position * view.span), 0, Math.max(0, this.totalFrames - 1));
            this.playbackSegment = this.segmentAtFrame(this.playhead);
            if (this.fps > 0) this.video.currentTime = this.playhead / this.fps;
            this.draw();
        };
        this.waveCanvas.onpointerdown = event => {
            if (!this.totalFrames) return;
            event.preventDefault();
            this.video.pause();
            this.waveDragging = true;
            this.waveCanvas.setPointerCapture(event.pointerId);
            scrubWave(event);
        };
        this.waveCanvas.onpointermove = event => { if (this.waveDragging) scrubWave(event); };
        const finishWave = event => {
            if (!this.waveDragging) return;
            this.waveDragging = false;
            if (this.waveCanvas.hasPointerCapture?.(event.pointerId)) this.waveCanvas.releasePointerCapture(event.pointerId);
            this.selectedSegment = this.segmentAtFrame(this.playhead);
            this.render();
        };
        this.waveCanvas.onpointerup = finishWave;
        this.waveCanvas.onpointercancel = finishWave;
        this.canvas.tabIndex = 0;
        this.canvas.onkeydown = event => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            this.stepFrame(event.key === "ArrowLeft" ? -1 : 1);
        };
        this.canvas.onwheel = event => {
            event.preventDefault();
            if (event.ctrlKey) {
                const next = clamp(this.timelineZoom + (event.deltaY < 0 ? 1 : -1), 1, 20);
                this.zoomInput.value = next;
                this.setTimelineZoom(next);
            } else if (this.timelineZoom > 1) {
                const view = this.viewBounds();
                this.viewStart = clamp(view.start + Math.sign(event.deltaY) * Math.max(1, Math.round(view.span * .12)), 0, Math.max(0, this.totalFrames - view.span));
                this.draw();
            }
        };
        this.waveCanvas.onwheel = this.canvas.onwheel;
        this.video.onplay = () => this.playbackSegment = this.segmentAtFrame(clamp(Math.round(this.video.currentTime * Math.max(.001, this.fps)), 0, Math.max(0, this.totalFrames - 1)));
        this.video.ontimeupdate = () => {
            if (this.scrubbing || this.fps <= 0) return;
            this.playhead = clamp(Math.round(this.video.currentTime * this.fps), 0, Math.max(0, this.totalFrames - 1));
            if (!this.video.paused && this.playMode !== "normal") {
                const boundaries = [0, ...this.cuts, this.totalFrames], segment = clamp(this.playbackSegment ?? this.segmentAtFrame(this.playhead), 0, boundaries.length - 2);
                if (this.playhead >= boundaries[segment + 1] - 1) {
                    if (this.playMode === "loop") {
                        this.playhead = boundaries[segment];
                        this.video.currentTime = this.playhead / this.fps;
                    } else {
                        this.playhead = Math.max(boundaries[segment], boundaries[segment + 1] - 1);
                        this.video.currentTime = this.playhead / this.fps;
                        this.video.pause();
                    }
                }
            }
            this.ensurePlayheadVisible();
            this.draw();
        };
        this.root.onpointerenter = () => this.pointerInside = true;
        this.root.onpointerleave = () => this.pointerInside = false;
        this.root.onpointerdown = () => activeCutter = this;
        this.keyHandler = event => this.handleShortcut(event);
        window.addEventListener("keydown", this.keyHandler, true);
        this.host.appendChild(root);
        this.render();
    }

    async loadVideo() {
        const path = String(value(this.node, "video_path", "") || "");
        this.pathLabel.textContent = path || "未选择视频";
        this.pathLabel.title = path;
        if (!path) return;
        this.video.src = `/sqr/video_file?file=${encodeURIComponent(path)}`;
        this.video.load();
        try {
            const response = await fetch(`/sqr/video_info?file=${encodeURIComponent(path)}`);
            const data = await response.json();
            if (!data.ok) throw new Error(data.error || "video info failed");
            const changed = this.totalFrames !== Number(data.frames) || Math.abs(this.fps - Number(data.fps)) > 0.001;
            this.totalFrames = Number(data.frames) || 0;
            this.fps = Number(data.fps) || 0;
            if (changed) {
                this.cuts = this.cuts.filter(frame => frame > 0 && frame < this.totalFrames);
                this.viewStart = 0;
                this.timelineZoom = 1;
                if (this.zoomInput) this.zoomInput.value = 1;
                if (this.zoomValue) this.zoomValue.textContent = "1×";
            }
            this.persist();
            this.render();
            this.loadWaveform(path);
        } catch (error) { this.status.textContent = `视频读取失败：${error}`; }
    }

    loadWaveform(path) {
        const token = Symbol();
        this.waveformToken = token;
        this.waveformImage = null;
        this.waveStatus.textContent = "生成中…";
        const image = new Image();
        image.onload = () => {
            if (this.waveformToken !== token) return;
            this.waveformImage = image;
            this.waveStatus.textContent = "已同步";
            this.draw();
        };
        image.onerror = () => {
            if (this.waveformToken !== token) return;
            this.waveformImage = null;
            this.waveStatus.textContent = "未检测到音轨";
            this.draw();
        };
        image.src = `/sqr/audio_waveform?file=${encodeURIComponent(path)}&width=2400`;
    }

    addCut() {
        if (!this.totalFrames || this.playhead <= 0 || this.playhead >= this.totalFrames) return;
        const frame = this.snapEnabled ? this.snapFrame(this.playhead) : this.playhead;
        if (frame <= 0 || frame >= this.totalFrames || this.cuts.includes(frame)) return;
        this.pushUndo();
        this.cuts.push(frame);
        this.persist();
        this.activeCutIndex = this.cuts.indexOf(frame);
        this.playhead = frame;
        this.selectedSegment = this.segmentAtFrame(frame);
        this.render();
    }

    parseTime(text) {
        const parts = String(text || "").trim().split(":").map(Number);
        if (parts.some(part => !Number.isFinite(part))) return 0;
        if (parts.length === 1) return Math.max(0, parts[0]);
        return Math.max(0, parts.slice(0, -1).reduce((seconds, part) => seconds * 60 + part, 0) * 60 + parts.at(-1));
    }

    seekToFrame(frame) {
        if (!this.totalFrames) return;
        this.video.pause();
        this.playhead = clamp(Math.round(Number(frame) || 0), 0, Math.max(0, this.totalFrames - 1));
        this.playbackSegment = this.segmentAtFrame(this.playhead);
        this.selectedSegment = this.playbackSegment;
        if (this.fps > 0) this.video.currentTime = this.playhead / this.fps;
        this.ensurePlayheadVisible();
        this.render();
    }

    jumpCut(direction) {
        if (!this.cuts.length) return;
        let index;
        if (direction < 0) {
            index = this.cuts.length - 1;
            while (index >= 0 && this.cuts[index] >= this.playhead) index--;
            if (index < 0) index = 0;
        } else {
            index = 0;
            while (index < this.cuts.length && this.cuts[index] <= this.playhead) index++;
            if (index >= this.cuts.length) index = this.cuts.length - 1;
        }
        this.activeCutIndex = index;
        this.seekToFrame(this.cuts[index]);
    }

    nearestCutIndex() {
        if (!this.cuts.length) return -1;
        let best = 0;
        for (let index = 1; index < this.cuts.length; index++) {
            if (Math.abs(this.cuts[index] - this.playhead) < Math.abs(this.cuts[best] - this.playhead)) best = index;
        }
        return best;
    }

    nudgeCut(direction) {
        const index = this.activeCutIndex >= 0 && this.activeCutIndex < this.cuts.length ? this.activeCutIndex : this.nearestCutIndex();
        if (index < 0) return;
        const oldFrame = this.cuts[index], min = (this.cuts[index - 1] || 0) + 1, max = (this.cuts[index + 1] || this.totalFrames) - 1;
        const nextFrame = clamp(oldFrame + direction, min, max);
        if (nextFrame === oldFrame) return;
        this.pushUndo();
        const meta = this.segmentMeta[String(oldFrame)];
        this.cuts[index] = nextFrame;
        if (meta) {
            delete this.segmentMeta[String(oldFrame)];
            this.segmentMeta[String(nextFrame)] = meta;
        }
        this.activeCutIndex = index;
        this.playhead = nextFrame;
        this.persist();
        if (this.fps > 0) this.video.currentTime = nextFrame / this.fps;
        this.render();
    }

    snapFrame(frame) {
        const candidates = [...this.cuts, ...this.detectedCuts];
        if (this.fps > 0) {
            const second = frame / this.fps;
            candidates.push(Math.round(Math.floor(second) * this.fps), Math.round(Math.ceil(second) * this.fps));
        }
        const threshold = Math.max(3, Math.round(this.fps * .12));
        let best = frame, distance = threshold + 1;
        for (const candidate of candidates) {
            const current = Math.abs(candidate - frame);
            if (candidate > 0 && candidate < this.totalFrames && current < distance) {
                best = candidate;
                distance = current;
            }
        }
        return distance <= threshold ? best : frame;
    }

    viewBounds() {
        const span = Math.max(1, Math.ceil(this.totalFrames / Math.max(1, this.timelineZoom)));
        const start = clamp(Math.round(this.viewStart), 0, Math.max(0, this.totalFrames - span));
        return {start, end: Math.min(this.totalFrames, start + span), span};
    }

    ensurePlayheadVisible() {
        const {start, end, span} = this.viewBounds();
        if (this.playhead < start || this.playhead >= end) this.viewStart = clamp(this.playhead - span * .08, 0, Math.max(0, this.totalFrames - span));
    }

    setTimelineZoom(zoom) {
        const old = this.viewBounds();
        this.timelineZoom = clamp(Math.round(zoom) || 1, 1, 20);
        const span = Math.max(1, Math.ceil(this.totalFrames / this.timelineZoom));
        const anchor = clamp(this.playhead, old.start, old.end);
        this.viewStart = clamp(anchor - span / 2, 0, Math.max(0, this.totalFrames - span));
        this.zoomValue.textContent = `${this.timelineZoom}×`;
        this.draw();
    }

    historyState() {
        return {cuts: [...this.cuts], segmentMeta: JSON.parse(JSON.stringify(this.segmentMeta)), selectedSegment: this.selectedSegment, playhead: this.playhead};
    }

    pushUndo() {
        this.undoStack.push(this.historyState());
        if (this.undoStack.length > 10) this.undoStack.shift();
        this.redoStack = [];
        this.updateHistoryButtons();
    }

    restoreHistory(state) {
        this.cuts = [...state.cuts];
        this.segmentMeta = JSON.parse(JSON.stringify(state.segmentMeta || {}));
        this.activeCutIndex = -1;
        this.selectedSegment = state.selectedSegment;
        this.playhead = clamp(state.playhead, 0, Math.max(0, this.totalFrames - 1));
        if (this.fps > 0) this.video.currentTime = this.playhead / this.fps;
        this.persist();
        this.render();
    }

    undo() {
        const state = this.undoStack.pop();
        if (!state) return;
        this.redoStack.push(this.historyState());
        if (this.redoStack.length > 10) this.redoStack.shift();
        this.restoreHistory(state);
    }

    redo() {
        const state = this.redoStack.pop();
        if (!state) return;
        this.undoStack.push(this.historyState());
        if (this.undoStack.length > 10) this.undoStack.shift();
        this.restoreHistory(state);
    }

    updateHistoryButtons() {
        if (this.undoButton) this.undoButton.disabled = !this.undoStack.length;
        if (this.redoButton) this.redoButton.disabled = !this.redoStack.length;
    }

    isShortcutActive() {
        const selected = app.canvas?.selected_nodes;
        const selectedNodes = selected ? Object.values(selected) : [];
        return this.pointerInside || activeCutter === this || document.activeElement === this.canvas || (selectedNodes.length === 1 && selectedNodes[0] === this.node);
    }

    handleShortcut(event) {
        if (!this.isShortcutActive()) return;
        const target = event.target;
        if (target?.matches?.("input,textarea,select,[contenteditable=true]")) return;
        if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "z") {
            event.preventDefault();
            event.stopPropagation();
            event.shiftKey ? this.redo() : this.undo();
            return;
        }
        if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "y") {
            event.preventDefault();
            event.stopPropagation();
            this.redo();
            return;
        }
        if (event.altKey && !event.ctrlKey && !event.metaKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
            event.preventDefault();
            event.stopPropagation();
            this.nudgeCut(event.key === "ArrowLeft" ? -1 : 1);
            return;
        }
        if (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key === "[" || event.key === "]")) {
            event.preventDefault();
            event.stopPropagation();
            this.jumpCut(event.key === "[" ? -1 : 1);
            return;
        }
        if (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
            event.preventDefault();
            event.stopPropagation();
            this.stepFrame(event.key === "ArrowLeft" ? -1 : 1);
            return;
        }
        if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key.toLowerCase() === "c") {
            event.preventDefault();
            event.stopPropagation();
            this.addCut();
            return;
        }
        if (event.code === "Space" && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            event.stopPropagation();
            if (this.video.paused) this.video.play().catch(() => {});
            else this.video.pause();
            return;
        }
        if ((event.key === "Backspace" || event.key === "Delete") && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            event.stopPropagation();
            this.deleteSegmentAtPlayhead();
        }
    }

    segmentAtFrame(frame) {
        let index = 0;
        while (index < this.cuts.length && frame >= this.cuts[index]) index++;
        return index;
    }

    mergeSelectedRight() {
        if (this.selectedSegment < 0 || this.selectedSegment >= this.cuts.length) return;
        this.pushUndo();
        this.cuts.splice(this.selectedSegment, 1);
        this.activeCutIndex = -1;
        this.selectedSegment = clamp(this.selectedSegment, 0, this.cuts.length);
        this.persist();
        this.render();
    }

    deleteSegmentAtPlayhead() {
        if (!this.cuts.length) return;
        const segment = this.segmentAtFrame(this.playhead);
        const cutIndex = segment < this.cuts.length ? segment : segment - 1;
        if (cutIndex < 0) return;
        this.pushUndo();
        this.cuts.splice(cutIndex, 1);
        this.activeCutIndex = -1;
        this.selectedSegment = clamp(Math.min(segment, cutIndex), 0, this.cuts.length);
        this.persist();
        this.render();
    }

    scrubToPointer(event) {
        const rect = this.canvas.getBoundingClientRect();
        if (this.fineScrub) {
            this.playhead = clamp(this.scrubStartFrame + Math.round(event.clientX - this.scrubStartX), 0, Math.max(0, this.totalFrames - 1));
        } else {
            const position = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
            const view = this.viewBounds();
            this.playhead = clamp(Math.round(view.start + position * view.span), 0, Math.max(0, this.totalFrames - 1));
        }
        this.seekFrame = this.playhead;
        this.draw();
        if (this.seekRequest) return;
        this.seekRequest = requestAnimationFrame(() => {
            this.seekRequest = 0;
            if (this.fps > 0 && Number.isFinite(this.video.duration)) this.video.currentTime = this.seekFrame / this.fps;
        });
    }

    stepFrame(direction) {
        if (!this.totalFrames) return;
        this.video.pause();
        this.playhead = clamp(this.playhead + direction, 0, Math.max(0, this.totalFrames - 1));
        this.playbackSegment = this.segmentAtFrame(this.playhead);
        if (this.fps > 0) this.video.currentTime = this.playhead / this.fps;
        this.selectedSegment = this.segmentAtFrame(this.playhead);
        this.ensurePlayheadVisible();
        this.render();
        this.canvas.focus({preventScroll: true});
    }

    async detectCuts() {
        const path = String(value(this.node, "video_path", "") || "");
        if (!path || !this.fps || !this.totalFrames) return;
        this.status.textContent = "正在检测切镜…";
        try {
            const response = await fetch("/sqr/detect_cuts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({video: path, start_time: 0, end_time: this.totalFrames / this.fps, threshold: this.sceneThreshold})});
            const data = await response.json();
            if (!data.ok) throw new Error(data.error || "detect failed");
            const candidates = (data.cuts || []).map(item => Math.round(Number(item.time_seconds) * this.fps)).filter(frame => frame > 0 && frame < this.totalFrames).sort((a, b) => a - b);
            const filtered = [];
            let previous = 0;
            for (const frame of candidates) {
                if (frame - previous < this.minDetectedFrames || this.totalFrames - frame < this.minDetectedFrames) continue;
                filtered.push(frame);
                previous = frame;
            }
            this.detectedCuts = [...filtered];
            if (filtered.length === this.cuts.length && filtered.every((frame, index) => frame === this.cuts[index])) {
                this.status.textContent = `检测结果未变化，共 ${this.cuts.length + 1} 段`;
                return;
            }
            this.pushUndo();
            this.cuts = filtered;
            this.activeCutIndex = -1;
            this.selectedSegment = 0;
            this.persist();
            this.render();
            this.status.textContent = `检测到 ${this.cuts.length} 个切镜，共 ${this.cuts.length + 1} 段`;
        } catch (error) { this.status.textContent = `检测失败：${error}`; }
    }

    setProjectPath(path) {
        const normalized = String(path || ""), separator = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
        if (separator >= 0) {
            this.projectDirectory = normalized.slice(0, separator);
            this.projectFilename = normalized.slice(separator + 1).replace(/\.json$/i, "") || "LH_Video_Cutter_Task";
            this.projectDirectoryInput.value = this.projectDirectory;
            this.projectFilenameInput.value = this.projectFilename;
        }
    }

    async browseProjectDirectory() {
        this.projectStatus.textContent = "正在选择…";
        try {
            const response = await fetch("/sqr/select_save_directory", {method: "POST"}), data = await response.json();
            if (!data.ok) throw new Error(data.error || "directory picker failed");
            if (data.path) {
                this.projectDirectory = data.path;
                this.projectDirectoryInput.value = data.path;
                this.persist();
            }
            this.projectStatus.textContent = "";
        } catch (error) {
            this.projectStatus.textContent = `选择失败：${error}`;
        }
    }

    async browseOutputDirectory() {
        this.outputStatus.textContent = "正在选择…";
        try {
            const response = await fetch("/sqr/select_save_directory", {method: "POST"}), data = await response.json();
            if (!data.ok) throw new Error(data.error || "directory picker failed");
            if (data.path) {
                this.outputDirectoryInput.value = data.path;
                setValue(this.node, "output_subfolder", data.path);
            }
            this.outputStatus.textContent = "";
        } catch (error) {
            this.outputStatus.textContent = `选择失败：${error}`;
        }
    }

    async saveProject() {
        const videoPath = String(value(this.node, "video_path", "") || "");
        if (!videoPath) { alert("请先选择目标视频"); return; }
        this.projectDirectory = this.projectDirectoryInput.value.trim();
        this.projectFilename = this.projectFilenameInput.value.trim() || "LH_Video_Cutter_Task";
        this.persist();
        let cutsData;
        try { cutsData = JSON.parse(String(value(this.node, "cuts_data", "{}"))); }
        catch { alert("当前分段数据无法保存"); return; }
        this.projectStatus.textContent = "正在保存…";
        try {
            const response = await fetch("/sqr/video_cutter/save_project", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    directory: this.projectDirectory,
                    filename: this.projectFilename,
                    video_path: videoPath,
                    cuts_data: cutsData,
                    output_subfolder: value(this.node, "output_subfolder", "LH_Video_Cutter"),
                    filename_prefix: value(this.node, "filename_prefix", "segment"),
                    cut_mode: value(this.node, "cut_mode", "accurate_h264"),
                    ui_state: {playhead: this.playhead, selected_segment: this.selectedSegment, timeline_zoom: this.timelineZoom, view_start: this.viewStart},
                }),
            });
            const data = await response.json();
            if (!data.ok) throw new Error(data.error || "save failed");
            this.setProjectPath(data.path);
            this.persist();
            this.projectStatus.textContent = `已保存：${data.path}`;
        } catch (error) {
            this.projectStatus.textContent = `保存失败：${error}`;
        }
    }

    async loadProject() {
        this.projectDirectory = this.projectDirectoryInput.value.trim();
        this.projectFilename = this.projectFilenameInput.value.trim() || "LH_Video_Cutter_Task";
        this.projectStatus.textContent = "正在读取…";
        try {
            const response = await fetch("/sqr/video_cutter/load_project", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    directory: this.projectDirectory,
                    filename: this.projectFilename,
                    current_video_path: value(this.node, "video_path", ""),
                }),
            });
            const data = await response.json();
            if (!data.ok) throw new Error(data.error || "load failed");
            const project = data.project;
            this.video.pause();
            setValue(this.node, "video_path", project.video_path);
            setValue(this.node, "cuts_data", JSON.stringify(project.cuts_data));
            setValue(this.node, "output_subfolder", project.output_subfolder || "LH_Video_Cutter");
            setValue(this.node, "filename_prefix", project.filename_prefix || "segment");
            setValue(this.node, "cut_mode", project.cut_mode || "accurate_h264");
            this.readCuts();
            this.setProjectPath(data.path);
            const uiState = project.ui_state && typeof project.ui_state === "object" ? project.ui_state : {};
            this.playhead = clamp(Math.round(Number(uiState.playhead) || 0), 0, Math.max(0, this.totalFrames - 1));
            this.selectedSegment = clamp(Math.round(Number(uiState.selected_segment) || 0), 0, this.cuts.length);
            this.activeCutIndex = -1;
            this.timelineZoom = clamp(Math.round(Number(uiState.timeline_zoom) || 1), 1, 20);
            this.viewStart = Math.max(0, Math.round(Number(uiState.view_start) || 0));
            this.undoStack = [];
            this.redoStack = [];
            this.sceneThresholdInput.value = this.sceneThreshold.toFixed(2);
            this.minDetectedInput.value = this.minDetectedFrames;
            this.snapInput.checked = this.snapEnabled;
            this.playModeInput.value = this.playMode;
            this.exportAudioInput.checked = this.exportAudio;
            this.audioFormatInput.value = this.audioFormat;
            this.audioBitrateInput.value = this.audioBitrate;
            for (const input of this.exportScopeInputs) input.checked = input.value === this.exportScope;
            this.syncAudioControls();
            this.zoomInput.value = this.timelineZoom;
            this.zoomValue.textContent = `${this.timelineZoom}×`;
            for (const input of this.settings.querySelectorAll("[data-setting]")) input.value = value(this.node, input.dataset.setting, "");
            this.persist();
            await this.loadVideo();
            if (this.fps > 0) this.video.currentTime = this.playhead / this.fps;
            this.render();
            this.projectStatus.textContent = `已加载：${data.path}`;
        } catch (error) {
            this.projectStatus.textContent = `加载失败：${error}`;
        }
    }

    async save(selectedOnly = false) {
        if (!value(this.node, "video_path", "") || !this.totalFrames) { alert("请先选择有效视频"); return; }
        if (!selectedOnly && !Object.values(this.segmentMeta).some(meta => meta.enabled !== false)) { alert("请至少勾选一个需要保存的分段"); return; }
        this.saveOnlySegment = selectedOnly ? this.selectedSegment : null;
        this.persist();
        setValue(this.node, "save_segments", true);
        try { await app.queuePrompt(0, 1); }
        finally {
            setValue(this.node, "save_segments", false);
            this.saveOnlySegment = null;
            this.persist();
        }
    }

    render() {
        const boundaries = [0, ...this.cuts, this.totalFrames];
        this.normalizeMeta();
        this.selectedSegment = clamp(this.selectedSegment, 0, Math.max(0, boundaries.length - 2));
        this.segmentBox.innerHTML = "";
        for (let index = 0; index < Math.max(0, boundaries.length - 1); index++) {
            const button = document.createElement("button");
            button.className = "lhvc-chip";
            button.dataset.segmentIndex = index;
            button.classList.toggle("selected", index === this.selectedSegment);
            button.classList.toggle("playhead", index === this.segmentAtFrame(this.playhead));
            button.style.borderLeft = `5px solid ${segmentColor(index)}`;
            button.textContent = `片段 ${index + 1}\n${boundaries[index]}–${boundaries[index + 1]}f`;
            const meta = this.segmentMeta[String(boundaries[index])];
            const metaRow = document.createElement("span");
            metaRow.className = "lhvc-chip-meta";
            const enabled = document.createElement("input");
            enabled.type = "checkbox";
            enabled.checked = meta.enabled !== false;
            enabled.title = "是否保存此分段";
            enabled.onclick = event => event.stopPropagation();
            enabled.onchange = event => { event.stopPropagation(); this.pushUndo(); meta.enabled = enabled.checked; this.persist(); this.render(); };
            const name = document.createElement("input");
            name.type = "text";
            name.value = meta.name || "";
            name.placeholder = "分段名称";
            name.title = "用于输出文件名";
            name.onclick = event => event.stopPropagation();
            name.onchange = event => { event.stopPropagation(); this.pushUndo(); meta.name = name.value.trim(); this.persist(); };
            metaRow.append(enabled, name);
            metaRow.onclick = event => event.stopPropagation();
            button.appendChild(metaRow);
            if (index > 0) {
                const close = document.createElement("i");
                close.textContent = "×";
                close.title = "移除此切点并合并左右片段";
                close.onclick = event => { event.stopPropagation(); this.pushUndo(); this.cuts.splice(index - 1, 1); this.activeCutIndex = -1; this.selectedSegment = clamp(index - 1, 0, this.cuts.length); this.persist(); this.render(); };
                button.appendChild(close);
            }
            button.onclick = () => { this.selectedSegment = index; this.playhead = boundaries[index]; this.playbackSegment = index; if (this.fps > 0) this.video.currentTime = this.playhead / this.fps; this.ensurePlayheadVisible(); this.render(); };
            this.segmentBox.appendChild(button);
        }
        if (this.deleteSelectedButton) {
            const canMergeRight = this.selectedSegment < boundaries.length - 2;
            this.deleteSelectedButton.disabled = !canMergeRight;
            this.deleteSelectedButton.title = canMergeRight ? "移除右侧切点，将选中段与右侧段合并" : "最后一段没有右侧片段";
        }
        this.updateHistoryButtons();
        const enabledCount = boundaries.slice(0, -1).filter(start => this.segmentMeta[String(start)]?.enabled !== false).length;
        this.status.textContent = this.totalFrames ? `${this.totalFrames} 帧 · ${this.fps.toFixed(3)} FPS · ${boundaries.length - 1} 段 · 已勾选 ${enabledCount} 段 · 空格播放/暂停 · Ctrl+Z 撤销` : "请选择视频";
        this.draw();
        requestAnimationFrame(() => this.resize());
    }

    updatePlayheadHighlight() {
        const current = this.segmentAtFrame(this.playhead);
        for (const button of this.segmentBox?.querySelectorAll("[data-segment-index]") || []) {
            const active = Number(button.dataset.segmentIndex) === current;
            button.classList.toggle("playhead", active);
            if (active && current !== this.highlightedSegment) {
                if (button.offsetLeft < this.segmentBox.scrollLeft) this.segmentBox.scrollLeft = button.offsetLeft;
                else if (button.offsetLeft + button.offsetWidth > this.segmentBox.scrollLeft + this.segmentBox.clientWidth) this.segmentBox.scrollLeft = button.offsetLeft + button.offsetWidth - this.segmentBox.clientWidth;
            }
        }
        this.highlightedSegment = current;
    }

    draw() {
        if (!this.canvas) return;
        this.updatePlayheadHighlight();
        const ratio = window.devicePixelRatio || 1, width = Math.max(1, this.canvas.clientWidth), height = Math.max(1, this.canvas.clientHeight);
        this.canvas.width = Math.round(width * ratio); this.canvas.height = Math.round(height * ratio);
        const context = this.canvas.getContext("2d"); context.scale(ratio, ratio); context.clearRect(0, 0, width, height);
        const rulerHeight = 28;
        context.fillStyle = "#171717"; context.fillRect(0, 0, width, rulerHeight);
        const view = this.viewBounds(), frameToX = frame => (frame - view.start) / Math.max(1, view.span) * width;
        const boundaries = [0, ...this.cuts, this.totalFrames || 1];
        const playheadSegment = this.segmentAtFrame(this.playhead);
        for (let index = 0; index < boundaries.length - 1; index++) {
            const x = frameToX(boundaries[index]), right = frameToX(boundaries[index + 1]);
            if (right < 0 || x > width) continue;
            context.fillStyle = segmentColor(index); context.globalAlpha = index === playheadSegment ? 0.95 : 0.62; context.fillRect(x, rulerHeight, Math.max(1, right - x), height - rulerHeight); context.globalAlpha = 1;
            if (index === playheadSegment) {
                context.fillStyle = "rgba(255,209,102,.16)"; context.fillRect(x, rulerHeight, Math.max(1, right - x), height - rulerHeight);
                context.strokeStyle = "#ffd166"; context.lineWidth = 2; context.strokeRect(x + 1, rulerHeight + 1, Math.max(1, right - x - 2), Math.max(1, height - rulerHeight - 2));
            }
            const visibleLeft = Math.max(0, x), visibleRight = Math.min(width, right);
            if (visibleRight - visibleLeft > 54) { context.fillStyle = "#fff"; context.font = "10px sans-serif"; context.textAlign = "center"; context.fillText(`${boundaries[index]}–${boundaries[index + 1]}f`, (visibleLeft + visibleRight) / 2, rulerHeight + (height - rulerHeight) / 2 + 4); }
        }
        this.drawRuler(context, width, rulerHeight, view);
        if (this.activeCutIndex >= 0 && this.activeCutIndex < this.cuts.length) {
            const cutX = frameToX(this.cuts[this.activeCutIndex]);
            if (cutX >= 0 && cutX <= width) {
                context.strokeStyle = "#6ee7ff"; context.lineWidth = 2; context.setLineDash([4, 3]); context.beginPath(); context.moveTo(cutX, rulerHeight); context.lineTo(cutX, height); context.stroke(); context.setLineDash([]);
            }
        }
        const cursor = frameToX(this.playhead);
        context.strokeStyle = "#ff3030"; context.lineWidth = 3; context.beginPath(); context.moveTo(cursor, 0); context.lineTo(cursor, height); context.stroke();
        context.fillStyle = "#ff3030"; context.beginPath(); context.moveTo(cursor - 5, 0); context.lineTo(cursor + 5, 0); context.lineTo(cursor, 7); context.closePath(); context.fill();
        if (this.activeCutIndex >= 0 && this.activeCutIndex < this.cuts.length) {
            const cutX = frameToX(this.cuts[this.activeCutIndex]);
            if (cutX >= 0 && cutX <= width) {
                context.strokeStyle = "#6ee7ff"; context.lineWidth = 3; context.beginPath(); context.arc(cutX, rulerHeight + 8, 6, 0, Math.PI * 2); context.stroke();
            }
        }
        const cursorText = `${this.formatTime(this.playhead / Math.max(0.001, this.fps))} / ${this.playhead}f`;
        context.font = "10px ui-monospace,monospace"; const textWidth = context.measureText(cursorText).width + 8;
        const labelX = clamp(cursor - textWidth / 2, 1, width - textWidth - 1);
        context.fillStyle = "rgba(120,0,0,.9)"; context.fillRect(labelX, height - 16, textWidth, 15);
        context.fillStyle = "#fff"; context.textAlign = "center"; context.fillText(cursorText, labelX + textWidth / 2, height - 5);
        if (this.frameInput && document.activeElement !== this.frameInput) this.frameInput.value = this.playhead;
        if (this.timeInput && document.activeElement !== this.timeInput) this.timeInput.value = this.formatTime(this.playhead / Math.max(.001, this.fps));
        this.drawWaveform(view);
    }

    drawWaveform(view = this.viewBounds()) {
        if (!this.waveCanvas) return;
        const ratio = window.devicePixelRatio || 1, width = Math.max(1, this.waveCanvas.clientWidth), height = Math.max(1, this.waveCanvas.clientHeight);
        this.waveCanvas.width = Math.round(width * ratio);
        this.waveCanvas.height = Math.round(height * ratio);
        const context = this.waveCanvas.getContext("2d");
        context.scale(ratio, ratio);
        context.fillStyle = "#101318";
        context.fillRect(0, 0, width, height);
        if (!this.totalFrames) return;
        const frameToX = frame => (frame - view.start) / Math.max(1, view.span) * width;
        const boundaries = [0, ...this.cuts, this.totalFrames], playheadSegment = this.segmentAtFrame(this.playhead);
        for (let index = 0; index < boundaries.length - 1; index++) {
            const left = frameToX(boundaries[index]), right = frameToX(boundaries[index + 1]);
            if (right < 0 || left > width) continue;
            context.fillStyle = segmentColor(index);
            context.globalAlpha = index === playheadSegment ? .3 : .13;
            context.fillRect(left, 0, Math.max(1, right - left), height);
        }
        context.globalAlpha = 1;
        if (this.waveformImage) {
            const sourceX = view.start / this.totalFrames * this.waveformImage.naturalWidth;
            const sourceWidth = Math.max(1, view.span / this.totalFrames * this.waveformImage.naturalWidth);
            context.save();
            context.globalCompositeOperation = "screen";
            context.drawImage(this.waveformImage, sourceX, 0, sourceWidth, this.waveformImage.naturalHeight, 0, 0, width, height);
            context.restore();
        } else {
            context.fillStyle = "#667";
            context.font = "10px sans-serif";
            context.textAlign = "center";
            context.fillText(this.waveStatus?.textContent || "音频波形", width / 2, height / 2 + 4);
        }
        for (const cut of this.cuts) {
            const x = frameToX(cut);
            if (x < 0 || x > width) continue;
            context.strokeStyle = "#ddd";
            context.globalAlpha = .65;
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(x, 0);
            context.lineTo(x, height);
            context.stroke();
        }
        context.globalAlpha = 1;
        const currentLeft = frameToX(boundaries[playheadSegment]), currentRight = frameToX(boundaries[playheadSegment + 1]);
        context.strokeStyle = "#ffd166";
        context.lineWidth = 2;
        context.strokeRect(currentLeft + 1, 1, Math.max(1, currentRight - currentLeft - 2), height - 2);
        const cursor = frameToX(this.playhead);
        context.strokeStyle = "#ff3030";
        context.lineWidth = 3;
        context.beginPath();
        context.moveTo(cursor, 0);
        context.lineTo(cursor, height);
        context.stroke();
    }

    formatTime(seconds) {
        const safe = Math.max(0, Number(seconds) || 0), minutes = Math.floor(safe / 60), remainder = safe - minutes * 60;
        return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(1).padStart(4, "0")}`;
    }

    drawRuler(context, width, rulerHeight, view = this.viewBounds()) {
        if (!this.totalFrames || !this.fps) return;
        const targetTicks = Math.max(2, Math.floor(width / 105)), rawStep = view.span / targetTicks;
        const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, rawStep))), normalized = rawStep / magnitude;
        const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
        const minorStep = Math.max(1, Math.round(step / 5));
        context.textAlign = "left"; context.font = "9px ui-monospace,monospace";
        const firstFrame = Math.ceil(view.start / minorStep) * minorStep;
        for (let frame = firstFrame; frame <= view.end; frame += minorStep) {
            const x = (frame - view.start) / Math.max(1, view.span) * width, major = frame % step === 0;
            context.strokeStyle = major ? "#aaa" : "#555"; context.lineWidth = 1; context.beginPath(); context.moveTo(x, rulerHeight); context.lineTo(x, major ? 15 : 21); context.stroke();
            if (major) { context.fillStyle = "#bbb"; const label = `${this.formatTime(frame / this.fps)} · ${frame}f`; context.fillText(label, clamp(x + 3, 2, Math.max(2, width - 82)), 11); }
        }
        context.fillStyle = "#ddd"; context.textAlign = "right"; context.fillText(`${this.formatTime(view.end / this.fps)} · ${view.end}f`, width - 3, 25);
    }

    resize() {
        if (!this.video) return;
        const width = Math.max(360, this.host.clientWidth || this.node.size?.[0] || 760);
        this.video.style.height = `${clamp(Math.round(width * 0.46), 170, 460)}px`;
        this.domWidget.computeSize = current => [current, clamp(Math.round(width * 0.46), 170, 460) + 490];
        this.draw();
    }

    async openBrowser() {
        document.querySelector(".lhvc-browser")?.remove();
        const overlay = document.createElement("div"); overlay.className = "lhvc-browser";
        const box = document.createElement("div"); box.className = "lhvc-browser-box";
        const head = document.createElement("div"); head.className = "lhvc-browser-head";
        const back = document.createElement("button"); back.textContent = "←";
        const pathLabel = document.createElement("span");
        const close = document.createElement("button"); close.textContent = "关闭"; close.onclick = () => overlay.remove();
        head.append(back, pathLabel, close); box.appendChild(head);
        const list = document.createElement("div"); list.className = "lhvc-list"; box.appendChild(list); overlay.appendChild(box); document.body.appendChild(overlay);
        let current = "", parent = "";
        const load = async path => {
            list.textContent = "加载中…";
            const response = await fetch(`/sqr/browse_videos${path ? `?path=${encodeURIComponent(path)}` : ""}`), data = await response.json();
            list.innerHTML = "";
            if (data.type === "roots") {
                pathLabel.textContent = "选择位置"; back.disabled = true;
                for (const root of data.roots || []) add(root.label, () => load(root.path));
                return;
            }
            current = data.path || path; parent = data.parent || ""; pathLabel.textContent = current; back.disabled = false;
            for (const folder of data.folders || []) add(`📁 ${folder}`, () => load(`${current.replace(/[\\/]$/, "")}\\${folder}`));
            for (const file of data.videos || []) add(`🎬 ${file}`, () => { const selected = `${current.replace(/[\\/]$/, "")}\\${file}`; setValue(this.node, "video_path", selected); this.cuts = []; this.segmentMeta = {}; this.detectedCuts = []; this.playhead = 0; this.selectedSegment = 0; this.activeCutIndex = -1; this.timelineZoom = 1; this.viewStart = 0; this.undoStack = []; this.redoStack = []; this.persist(); overlay.remove(); this.loadVideo(); });
        };
        const add = (label, action) => { const button = document.createElement("button"); button.textContent = label; button.onclick = action; list.appendChild(button); };
        back.onclick = () => load(parent || "");
        overlay.onclick = event => { if (event.target === overlay) overlay.remove(); };
        load("");
    }

    destroy() {
        this.resizeObserver?.disconnect();
        if (this.keyHandler) window.removeEventListener("keydown", this.keyHandler, true);
        if (activeCutter === this) activeCutter = null;
    }
}

app.registerExtension({
    name: "WanAniSQR.LHVideoCutter",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "LHVideoCutter") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const result = created?.apply(this, arguments);
            this.size = [Math.max(760, this.size?.[0] || 0), Math.max(890, this.size?.[1] || 0)];
            for (const name of ["video_path", "cuts_data", "output_subfolder", "filename_prefix", "cut_mode", "save_segments"]) hideWidget(widget(this, name));
            const host = document.createElement("div");
            const domWidget = this.addDOMWidget("lh_video_cutter_ui", "lh_video_cutter_ui", host, {getValue: () => "", setValue: () => {}});
            domWidget.computeSize = width => [width, 850];
            setTimeout(() => { this._lhVideoCutter?.destroy(); this._lhVideoCutter = new LHVideoCutterUI(this, host, domWidget); }, 0);
            return result;
        };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function() { this._lhVideoCutter?.destroy(); return removed?.apply(this, arguments); };
    },
});

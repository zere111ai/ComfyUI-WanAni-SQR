import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAMES = new Set(["LHWANSVIDirectorTimeline", "LHWANCWDirectorTimeline"]);
const MIN_REF_FRAMES = 5;
const MAX_REF_FRAMES = 81;
const SNAP_FRAMES = 2;
const PX_PER_FRAME_MIN = 0.25;

function isCWNode(node) {
    return node?.comfyClass === "LHWANCWDirectorTimeline" || node?.type === "LHWANCWDirectorTimeline";
}

function widget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function viewUrl(filename) {
    return `/view?filename=${encodeURIComponent(filename)}&type=input`;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

async function uploadImage(file) {
    const body = new FormData();
    body.append("image", file);
    body.append("type", "input");
    body.append("overwrite", "false");
    const resp = await api.fetchApi("/upload/image", { method: "POST", body });
    if (!resp.ok) throw new Error(`upload failed: ${resp.status}`);
    const data = await resp.json();
    const name = data.name || data.filename || file.name;
    const subfolder = (data.subfolder || "").replace(/\\/g, "/").replace(/\/$/, "");
    return subfolder ? `${subfolder}/${name}` : name;
}

function parseTimeline(node) {
    const dataWidget = widget(node, "timeline_data");
    const totalWidget = widget(node, "total_frames");
    const durationWidget = widget(node, "duration_seconds");
    const fpsWidget = widget(node, "frame_rate");
    const maxWidget = widget(node, "max_segment_frames");
    const contextWidget = widget(node, "context_window_frames");
    const overlapWidget = widget(node, "context_overlap_frames");
    const frameRate = Number(fpsWidget?.value || 16);
    const totalFrames = durationWidget ? Math.max(1, Math.round(Number(durationWidget.value || 5) * frameRate)) : Number(totalWidget?.value || 243);
    const contextFrames = Number((contextWidget || maxWidget)?.value || 81);
    const fallback = {
        version: 1,
        kind: isCWNode(node) ? "LH_WAN_CW_DIRECTOR_TIMELINE" : "LH_WAN_SVI_DIRECTOR_TIMELINE",
        frameRate,
        totalFrames,
        timelineFrames: totalFrames,
        maxSegmentFrames: contextFrames,
        contextWindowFrames: contextFrames,
        contextOverlapFrames: Number(overlapWidget?.value || 16),
        width: 832,
        height: 480,
        timelineHeightScale: 1,
        matchFirstImageAspect: false,
        keepTimelineSeconds: false,
        aspectPixelPreset: "720p",
        enablePromptRelay: true,
        globalPrompt: "",
        refs: [],
    };
    try {
        const parsed = { ...fallback, ...(JSON.parse(dataWidget?.value || "{}")) };
        if (isCWNode(node)) {
            parsed.frameRate = frameRate;
            parsed.durationSeconds = Number(durationWidget?.value || parsed.durationSeconds || 5);
            parsed.totalFrames = Math.max(1, Math.round(parsed.durationSeconds * frameRate));
            parsed.timelineFrames = parsed.totalFrames;
            parsed.maxSegmentFrames = contextFrames;
            parsed.contextWindowFrames = contextFrames;
            parsed.contextOverlapFrames = Number(overlapWidget?.value || parsed.contextOverlapFrames || 16);
        }
        return normalizeTimeline(parsed, node);
    } catch {
        return normalizeTimeline(fallback, node);
    }
}

function normalizeTimeline(data, node) {
    const totalWidget = widget(node, "total_frames");
    const durationWidget = widget(node, "duration_seconds");
    const fpsWidget = widget(node, "frame_rate");
    const maxWidget = widget(node, "max_segment_frames");
    const contextWidget = widget(node, "context_window_frames");
    const overlapWidget = widget(node, "context_overlap_frames");
    const cw = isCWNode(node);
    const frameRate = Math.max(1, Number(data.frameRate || fpsWidget?.value || 16));
    const durationSeconds = cw
        ? Math.max(0.25, Math.min(60, Number(data.durationSeconds || durationWidget?.value || 5)))
        : Number(data.durationSeconds || 0);
    const totalFrames = cw
        ? Math.max(MIN_REF_FRAMES, Math.round(durationSeconds * frameRate))
        : Math.max(1, Math.round(Number(data.totalFrames || totalWidget?.value || 243)));
    const maxSegmentFrames = Math.max(MIN_REF_FRAMES, Math.min(MAX_REF_FRAMES, Math.round(Number(data.contextWindowFrames || data.maxSegmentFrames || contextWidget?.value || maxWidget?.value || 81))));
    const maxRefFrames = cw ? totalFrames : maxSegmentFrames;
    const contextOverlapFrames = Math.max(0, Math.min(maxSegmentFrames - 1, Math.round(Number(data.contextOverlapFrames || overlapWidget?.value || 16))));
    const refs = Array.isArray(data.refs) ? data.refs : [];
    const refMaxEnd = refs.reduce((max, ref) => Math.max(max, Math.round(Number(ref?.endFrame || 0))), 0);
    const timelineFrames = Math.max(totalFrames, refMaxEnd, Math.round(Number(data.timelineFrames || totalFrames)));
    data.version = 1;
    data.kind = cw ? "LH_WAN_CW_DIRECTOR_TIMELINE" : "LH_WAN_SVI_DIRECTOR_TIMELINE";
    data.totalFrames = totalFrames;
    data.timelineFrames = timelineFrames;
    data.frameRate = frameRate;
    data.durationSeconds = cw ? durationSeconds : totalFrames / frameRate;
    data.timelineSeconds = cw ? data.durationSeconds : timelineFrames / frameRate;
    data.maxSegmentFrames = maxSegmentFrames;
    if (cw) {
        data.contextWindowFrames = maxSegmentFrames;
        data.contextOverlapFrames = contextOverlapFrames;
    } else {
        delete data.contextWindowFrames;
        delete data.contextOverlapFrames;
    }
    data.width = Math.max(16, Math.round(Number(data.width || 832) / 16) * 16);
    data.height = Math.max(16, Math.round(Number(data.height || 480) / 16) * 16);
    data.timelineHeightScale = Math.max(1, Math.min(4, Number(data.timelineHeightScale || 1)));
    data.matchFirstImageAspect = !!data.matchFirstImageAspect;
    data.keepTimelineSeconds = !!data.keepTimelineSeconds;
    data.aspectPixelPreset = data.aspectPixelPreset === "480p" ? "480p" : "720p";
    data.enablePromptRelay = data.enablePromptRelay !== false;
    data.globalPrompt = data.globalPrompt || data.global_prompt || "";
    data.refs = refs.map((ref, i) => {
        const start = Math.max(0, Math.min(timelineFrames - MIN_REF_FRAMES, Math.round(Number(ref.startFrame ?? 0))));
        const rawEnd = Math.max(start + MIN_REF_FRAMES, Math.min(timelineFrames, Math.round(Number(ref.endFrame ?? start + maxSegmentFrames))));
        const end = Math.min(rawEnd, start + maxRefFrames);
        return {
            id: ref.id || `ref_${i + 1}`,
            label: ref.label || `Reference ${i + 1}`,
            image: ref.image || "",
            endImage: ref.endImage || ref.end_image || "",
            extraImages: Array.isArray(ref.extraImages) ? ref.extraImages.slice(0, 3) : [],
            startFrame: start,
            endFrame: end,
            strength: Number(ref.strength ?? 1),
            startStrength: Math.max(0, Math.min(1, Number(ref.startStrength ?? ref.strength ?? 1))),
            endStrength: Math.max(0, Math.min(1, Number(ref.endStrength ?? ref.strength ?? 1))),
            epsilon: Math.max(0.000001, Math.min(1, Number(ref.epsilon ?? 0.001))),
            continuePrevious: i > 0 && !!ref.continuePrevious,
            prompt: ref.prompt || "",
        };
    });
    data.refs.sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    data.refs = data.refs.map((ref, index) => ({
        ...ref,
        continuePrevious: index > 0 && !!ref.continuePrevious,
    }));
    let cursor = 0;
    data.refs = data.refs.map((ref) => {
        const len = Math.max(MIN_REF_FRAMES, Math.min(maxRefFrames, ref.endFrame - ref.startFrame));
        const start = Math.max(cursor, Math.min(ref.startFrame, Math.max(0, timelineFrames - len)));
        const end = Math.min(timelineFrames, start + len);
        cursor = end;
        return { ...ref, startFrame: start, endFrame: end };
    }).filter((ref) => ref.endFrame - ref.startFrame >= MIN_REF_FRAMES);
    // SVI may extend the visible timeline when a segment is moved past the
    // configured duration, but an old cached timeline length must not keep
    // an empty tail alive after the segments have been shortened or deleted.
    data.timelineFrames = cw ? data.totalFrames : Math.max(data.totalFrames, ...data.refs.map((ref) => ref.endFrame));
    data.timelineSeconds = cw ? data.durationSeconds : data.timelineFrames / frameRate;
    return data;
}

function writeTimeline(node, data) {
    const normalized = normalizeTimeline(data, node);
    const totalWidget = widget(node, "total_frames");
    const durationWidget = widget(node, "duration_seconds");
    const fpsWidget = widget(node, "frame_rate");
    const maxWidget = widget(node, "max_segment_frames");
    const contextWidget = widget(node, "context_window_frames");
    const overlapWidget = widget(node, "context_overlap_frames");
    const dataWidget = widget(node, "timeline_data");
    if (totalWidget) totalWidget.value = normalized.totalFrames;
    if (durationWidget) durationWidget.value = normalized.durationSeconds;
    if (fpsWidget) fpsWidget.value = normalized.frameRate;
    if (maxWidget) maxWidget.value = normalized.maxSegmentFrames;
    if (contextWidget) contextWidget.value = normalized.contextWindowFrames;
    if (overlapWidget) overlapWidget.value = normalized.contextOverlapFrames;
    if (dataWidget) dataWidget.value = JSON.stringify(normalized);
    app.graph?.setDirtyCanvas(true, true);
}

function round16(value) {
    return Math.max(16, Math.round(Number(value || 16) / 16) * 16);
}

function clampEpsilon(value) {
    const parsed = Number(String(value ?? "").trim());
    if (!Number.isFinite(parsed)) return 0.001;
    return Math.max(0.000001, Math.min(1, parsed));
}

function formatEpsilon(value) {
    return clampEpsilon(value).toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}

function updateAspectButtons(root, data) {
    root.querySelectorAll("[data-preset]").forEach((button) => {
        button.disabled = !data.matchFirstImageAspect;
        button.classList.toggle("active", data.aspectPixelPreset === button.dataset.preset);
    });
}

function previousRef(data, ref) {
    const refs = [...(data.refs || [])].sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    const index = refs.findIndex((item) => item.id === ref.id);
    return index > 0 ? refs[index - 1] : null;
}

function nextRef(data, ref) {
    const refs = [...(data.refs || [])].sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    const index = refs.findIndex((item) => item.id === ref.id);
    return index >= 0 && index < refs.length - 1 ? refs[index + 1] : null;
}

function snapFrame(frame, data, ignoreId = null) {
    const rounded = Math.round(frame);
    const points = [0, data.totalFrames, data.timelineFrames || data.totalFrames];
    for (const ref of data.refs || []) {
        if (ref.id === ignoreId) continue;
        points.push(ref.startFrame, ref.endFrame);
    }
    for (const point of points) {
        if (Math.abs(rounded - point) <= SNAP_FRAMES) return point;
    }
    return rounded;
}

function moveBounds(data, ref) {
    const len = ref.endFrame - ref.startFrame;
    const prev = previousRef(data, ref);
    const next = nextRef(data, ref);
    const min = prev ? prev.endFrame : 0;
    const max = (next ? next.startFrame : (data.timelineFrames || data.totalFrames)) - len;
    return { min, max: Math.max(min, max) };
}

function firstFreeRange(data, preferredStart = 0) {
    const refs = [...(data.refs || [])].sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    const gaps = [];
    let cursor = 0;
    for (const ref of refs) {
        if (ref.startFrame - cursor >= MIN_REF_FRAMES) gaps.push({ start: cursor, end: ref.startFrame });
        cursor = Math.max(cursor, ref.endFrame);
    }
    const timelineFrames = Math.max(data.totalFrames, data.timelineFrames || data.totalFrames, cursor);
    if (timelineFrames - cursor >= MIN_REF_FRAMES) gaps.push({ start: cursor, end: timelineFrames });
    const defaultLength = data.maxSegmentFrames;
    if (!gaps.length) {
        const start = Math.max(cursor, Math.round(preferredStart));
        return { start, end: start + defaultLength };
    }
    const containing = gaps.find((gap) => preferredStart >= gap.start && preferredStart <= gap.end - MIN_REF_FRAMES);
    const gap = containing || gaps[gaps.length - 1];
    const start = Math.max(gap.start, Math.min(Math.round(preferredStart), gap.end - MIN_REF_FRAMES));
    return { start, end: Math.min(gap.end, start + defaultLength) };
}

function duplicateRefToRight(data, refId) {
    const refs = [...(data.refs || [])].sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    const index = refs.findIndex((ref) => ref.id === refId);
    if (index < 0) return null;
    const source = refs[index];
    const len = source.endFrame - source.startFrame;
    const insertStart = source.endFrame;
    const insertEnd = insertStart + len;
    for (let i = index + 1; i < refs.length; i++) {
        refs[i].startFrame += len;
        refs[i].endFrame += len;
    }
    data.timelineFrames = Math.max(data.timelineFrames || data.totalFrames, ...refs.map((ref) => ref.endFrame), insertEnd);
    const duplicate = {
        ...source,
        id: `ref_${Date.now()}_${Math.round(Math.random() * 1000)}`,
        label: `${source.label || "Reference"} copy`,
        startFrame: insertStart,
        endFrame: insertEnd,
    };
    refs.splice(index + 1, 0, duplicate);
    data.refs = refs;
    return duplicate;
}

function reorderRefsByDraggedCenter(data, refId, centerFrame) {
    const selected = (data.refs || []).find((ref) => ref.id === refId);
    if (!selected) return;
    const others = (data.refs || []).filter((ref) => ref.id !== refId).sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    let insertIndex = 0;
    for (const ref of others) {
        const mid = (ref.startFrame + ref.endFrame) / 2;
        if (centerFrame > mid) insertIndex++;
    }
    const ordered = [...others];
    ordered.splice(insertIndex, 0, selected);
    const original = [...(data.refs || [])].sort((a, b) => a.startFrame - b.startFrame || a.endFrame - b.endFrame);
    const gaps = [];
    let oldCursor = 0;
    for (const ref of original) {
        gaps.push(Math.max(0, ref.startFrame - oldCursor));
        oldCursor = Math.max(oldCursor, ref.endFrame);
    }
    let cursor = 0;
    const maxRefFrames = data.kind === "LH_WAN_CW_DIRECTOR_TIMELINE" ? data.totalFrames : data.maxSegmentFrames;
    data.refs = ordered.map((ref, index) => {
        cursor += gaps[index] || 0;
        const len = Math.max(MIN_REF_FRAMES, Math.min(maxRefFrames, ref.endFrame - ref.startFrame));
        const start = cursor;
        const end = start + len;
        cursor = end;
        return { ...ref, startFrame: start, endFrame: end };
    });
    data.timelineFrames = Math.max(data.timelineFrames || data.totalFrames, cursor);
}

function canPlaceRefAt(data, refId, startFrame) {
    const selected = (data.refs || []).find((ref) => ref.id === refId);
    if (!selected) return false;
    const len = selected.endFrame - selected.startFrame;
    const start = Math.max(0, Math.round(startFrame));
    const end = start + len;
    return !(data.refs || []).some((ref) => {
        if (ref.id === refId) return false;
        return start < ref.endFrame && end > ref.startFrame;
    });
}

function slotValue(ref, slot) {
    if (!ref) return "";
    if (slot.startsWith("extraImages.")) {
        const index = Number(slot.split(".")[1] || 0);
        return Array.isArray(ref.extraImages) ? (ref.extraImages[index] || "") : "";
    }
    return ref[slot] || "";
}

function setSlotValue(ref, slot, value) {
    if (slot.startsWith("extraImages.")) {
        const index = Number(slot.split(".")[1] || 0);
        ref.extraImages = Array.isArray(ref.extraImages) ? ref.extraImages.slice(0, 3) : [];
        while (ref.extraImages.length <= index) ref.extraImages.push("");
        ref.extraImages[index] = value || "";
        return;
    }
    ref[slot] = value || "";
}

function makeDirectorUI(node) {
    const root = document.createElement("div");
    const windowEvents = new AbortController();
    root.className = "lh-wan-svi-director";
    root.innerHTML = `
        <style>
        .lh-wan-svi-director{position:relative;font:12px Arial,sans-serif;color:#ddd;background:#1f2228;border:1px solid #3a3f48;border-radius:6px;padding:8px;box-sizing:border-box}
        .lh-wan-svi-director *{box-sizing:border-box}
        .lh-wan-svi-director .bar{display:grid;grid-template-columns:repeat(4,minmax(92px,1fr)) auto auto;gap:6px;align-items:end;margin-bottom:8px}
        .lh-wan-svi-director .bar.sizebar{grid-template-columns:repeat(3,minmax(92px,1fr)) auto auto auto}
        .lh-wan-svi-director .timeline-actions{display:flex;justify-content:center;gap:8px;margin:6px 0 8px}
        .lh-wan-svi-director .field{display:flex;flex-direction:column;gap:3px;color:#aab2c0}
        .lh-wan-svi-director input,.lh-wan-svi-director textarea{background:#11151a;color:#ddd;border:1px solid #4b5563;border-radius:4px;padding:4px 6px;font:12px Arial,sans-serif}
        .lh-wan-svi-director input[type="range"]{padding:0}
        .lh-wan-svi-director .toggle{display:flex;align-items:center;gap:6px;color:#c8d0dc;min-height:42px}
        .lh-wan-svi-director .toggle input{width:auto}
        .lh-wan-svi-director .field input{width:100%}
        .lh-wan-svi-director button{background:#2d6cdf;color:white;border:0;border-radius:4px;padding:5px 9px;cursor:pointer}
        .lh-wan-svi-director button.secondary{background:#333a46}
        .lh-wan-svi-director button.active{outline:2px solid #ffd166}
        .lh-wan-svi-director button:disabled{opacity:.55;cursor:default}
        .lh-wan-svi-director .timeline-wrap{position:relative;background:#111317;border:1px solid #343a45;border-radius:4px;overflow:hidden}
        .lh-wan-svi-director .ruler{position:relative;height:24px;margin-left:104px;background:#20242c;border-bottom:1px solid #343a45}
        .lh-wan-svi-director .tick{position:absolute;top:0;height:24px;border-left:1px solid #47505c;color:#aab2c0;font-size:10px;padding-left:3px}
        .lh-wan-svi-director .tracks{display:grid;grid-template-columns:104px 1fr;min-height:150px}
        .lh-wan-svi-director .track-label{display:flex;align-items:center;justify-content:center;border-right:1px solid #303640;border-bottom:1px solid #252a32;color:#c8d0dc;font-weight:700}
        .lh-wan-svi-director .main-track,.lh-wan-svi-director .audio-track{position:relative;border-bottom:1px solid #252a32;background:#15181e;min-height:96px}
        .lh-wan-svi-director .process-cursor{position:absolute;top:0;bottom:0;width:2px;background:#22c55e;box-shadow:0 0 8px #22c55e;z-index:3}
        .lh-wan-svi-director .process-cursor span{position:absolute;top:2px;left:5px;color:#86efac;font-size:10px;white-space:nowrap;text-shadow:0 1px 2px #000}
        .lh-wan-svi-director .audio-track{min-height:48px;background:#12151a}
        .lh-wan-svi-director .drop-hint{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#66717f;pointer-events:none}
        .lh-wan-svi-director .drop-on .drop-hint{color:#d5e6ff;background:rgba(45,108,223,.18);border:1px dashed #7eb0ff}
        .lh-wan-svi-director .ref{position:absolute;top:8px;height:80px;border-radius:4px;background:#2a2f37;border:1px solid #6b7280;color:white;overflow:hidden;cursor:grab;box-shadow:0 1px 3px rgba(0,0,0,.4)}
        .lh-wan-svi-director .ref.resize-left,.lh-wan-svi-director .ref.resize-right{cursor:ew-resize}
        .lh-wan-svi-director .ref.selected{outline:2px solid #ffd166}
        .lh-wan-svi-director .ref img{width:100%;height:100%;object-fit:contain;background:#0c0f13;display:block}
        .lh-wan-svi-director .ref.prompt-only{background:#202733;border-color:#7c8aa0}
        .lh-wan-svi-director .ref.prompt-only .prompt-fill{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:8px;text-align:center;color:#cdd6e3;background:linear-gradient(135deg,#202733,#121821);font-size:11px}
        .lh-wan-svi-director .ref .cap{position:absolute;left:0;right:0;top:0;padding:3px 5px;background:linear-gradient(rgba(0,0,0,.72),rgba(0,0,0,0));font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .lh-wan-svi-director .ref:before,.lh-wan-svi-director .ref:after{content:"";position:absolute;top:0;width:8px;height:100%;background:rgba(255,255,255,.22)}
        .lh-wan-svi-director .ref:before{left:0}.lh-wan-svi-director .ref:after{right:0}
        .lh-wan-svi-director .audio-placeholder{position:absolute;left:14px;top:14px;color:#697484}
        .lh-wan-svi-director .meta{padding:7px;color:#aab2c0;line-height:1.35;border-top:1px solid #252a32}
        .lh-wan-svi-director .warnings{display:none;padding:7px 9px;color:#ffd7a3;background:#332614;border-top:1px solid #67491f;line-height:1.45;white-space:pre-line}
        .lh-wan-svi-director .prompt-grid{display:grid;grid-template-columns:1fr;gap:8px;margin-top:8px}
        .lh-wan-svi-director .prompt-card{background:#15181e;border:1px solid #303640;border-radius:4px;padding:7px}
        .lh-wan-svi-director .prompt-title{color:#aab2c0;font-size:11px;margin-bottom:5px;text-transform:uppercase}
        .lh-wan-svi-director .segment-prompt-title,.lh-wan-svi-director .global-prompt-title{display:flex;align-items:center;justify-content:space-between;gap:8px}
        .lh-wan-svi-director .segment-prompt-title .toggle,.lh-wan-svi-director .global-prompt-title .toggle{font-size:12px;text-transform:none;color:#d9e1ec;white-space:nowrap;min-height:0}
        .lh-wan-svi-director textarea{width:100%;height:72px;resize:vertical}
        .lh-wan-svi-director .prompt-controls{display:grid;grid-template-columns:220px 1fr;gap:8px;align-items:start}
        .lh-wan-svi-director .strength-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px}
        .lh-wan-svi-director .strength-row input{width:100%}
        .lh-wan-svi-director .frame-ref-editor{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:8px;margin-top:8px}
        .lh-wan-svi-director .frame-slot{min-height:160px;border:1px solid #3f4a5a;border-radius:4px;background:#10151c;display:grid;grid-template-rows:auto 1fr auto;overflow:hidden}
        .lh-wan-svi-director .frame-slot.drag-over{outline:2px solid #93c5fd}
        .lh-wan-svi-director .frame-slot-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;color:#cbd5e1;font-size:11px;text-transform:uppercase}
        .lh-wan-svi-director .frame-slot-preview{min-height:112px;display:flex;align-items:center;justify-content:center;background:#0b0f14;color:#7c8aa0;font-size:12px}
        .lh-wan-svi-director .frame-slot-preview img{width:100%;height:100%;max-height:150px;object-fit:contain;display:block}
        .lh-wan-svi-director .frame-slot-actions{display:flex;gap:6px;padding:6px}
        .lh-wan-svi-director .frame-slot-actions button{flex:1;padding:5px 6px;font-size:11px}
        .lh-wan-svi-director .ctx-menu{position:absolute;z-index:999999;background:#171b22;border:1px solid #48515f;border-radius:4px;box-shadow:0 6px 18px rgba(0,0,0,.42);padding:4px;display:none}
        .lh-wan-svi-director .ctx-menu button{display:block;width:140px;text-align:left;background:transparent;color:#d9e1ec;padding:7px 8px}
        .lh-wan-svi-director .ctx-menu button:hover{background:#2d3645}
        </style>
        <div class="bar">
            <label class="field">total frames <input data-k="totalFrames" type="number" min="1"></label>
            <label class="field">total time <input data-k="durationSeconds" type="number" min="0.01" step="0.01"></label>
            <label class="field">fps <input data-k="frameRate" type="number" min="1" step="0.01"></label>
            <label class="field">max/seg <input data-k="maxSegmentFrames" type="number" min="5" max="81"></label>
            <input data-file-picker type="file" accept="image/*" style="display:none">
            <input data-replace-picker type="file" accept="image/*" style="display:none">
            <input data-segment-image-picker type="file" accept="image/*" style="display:none">
        </div>
        <div class="bar sizebar">
            <label class="field">width <input data-k="width" type="number" min="16" step="16"></label>
            <label class="field">height <input data-k="height" type="number" min="16" step="16"></label>
            <label class="field">timeline height <input data-k="timelineHeightScale" type="range" min="1" max="4" step="0.05"></label>
            <label class="toggle"><input data-k="matchFirstImageAspect" type="checkbox"> keep first image ratio</label>
            <label class="toggle"><input data-k="keepTimelineSeconds" type="checkbox"> FPS change keeps time</label>
            <button class="secondary" data-preset="720p">720</button>
            <button class="secondary" data-preset="480p">480</button>
        </div>
        <div class="timeline-actions">
            <button class="secondary" data-act="add-prompt">Add Prompt</button>
            <button data-act="add">Add Image</button>
            <button class="secondary" data-act="delete">Delete</button>
        </div>
        <div class="timeline-wrap">
            <div class="ruler"></div>
            <div class="tracks">
                <div class="track-label">MAIN</div>
                <div class="main-track"><div class="drop-hint">Drop images here or use Add Image</div></div>
                <div class="track-label">AUDIO</div>
                <div class="audio-track"><div class="audio-placeholder">Audio track reserved</div></div>
            </div>
            <div class="meta"></div>
            <div class="warnings"></div>
        </div>
        <div class="prompt-grid">
            <div class="prompt-card">
                <div class="prompt-title global-prompt-title"><span>Global Prompt</span><label class="toggle"><input data-k="enablePromptRelay" type="checkbox"> 启用 Prompt Relay</label></div>
                <textarea data-prompt="global" placeholder="Global prompt shared by the timeline..."></textarea>
            </div>
            <div class="prompt-card">
                <div class="prompt-title segment-prompt-title"><span>Selected Image Prompt</span><label class="toggle"><input data-k="continuePrevious" type="checkbox"> 与上一段接续</label></div>
                <div class="prompt-controls">
                    <div>
                        <label class="field">prompt relay epsilon <input data-k="segmentEpsilon" type="text" inputmode="decimal"></label>
                        <div class="strength-row">
                            <label class="field">start frame strength <input data-k="startStrength" type="range" min="0" max="1" step="0.01"></label>
                            <label class="field">end frame strength <input data-k="endStrength" type="range" min="0" max="1" step="0.01"></label>
                        </div>
                    </div>
                    <textarea data-prompt="segment" placeholder="Select an image segment and write its prompt..."></textarea>
                </div>
            </div>
            <div class="prompt-card">
                <div class="prompt-title">Selected Frame References</div>
                <div class="frame-ref-editor">
                    <div class="frame-slot" data-frame-slot="image" draggable="true">
                        <div class="frame-slot-head"><span>Start frame</span></div>
                        <div class="frame-slot-preview" data-frame-preview="image"></div>
                        <div class="frame-slot-actions"><button data-frame-act="upload" data-slot="image">Upload</button><button data-frame-act="delete" data-slot="image">Delete</button></div>
                    </div>
                    <div class="frame-slot" data-frame-slot="endImage" draggable="true">
                        <div class="frame-slot-head"><span>End frame</span></div>
                        <div class="frame-slot-preview" data-frame-preview="endImage"></div>
                        <div class="frame-slot-actions"><button data-frame-act="upload" data-slot="endImage">Upload</button><button data-frame-act="delete" data-slot="endImage">Delete</button></div>
                    </div>
                    <div class="frame-slot" data-frame-slot="extraImages.0" draggable="true">
                        <div class="frame-slot-head"><span>Reserved 3</span></div>
                        <div class="frame-slot-preview" data-frame-preview="extraImages.0"></div>
                        <div class="frame-slot-actions"><button data-frame-act="upload" data-slot="extraImages.0">Upload</button><button data-frame-act="delete" data-slot="extraImages.0">Delete</button></div>
                    </div>
                    <div class="frame-slot" data-frame-slot="extraImages.1" draggable="true">
                        <div class="frame-slot-head"><span>Reserved 4</span></div>
                        <div class="frame-slot-preview" data-frame-preview="extraImages.1"></div>
                        <div class="frame-slot-actions"><button data-frame-act="upload" data-slot="extraImages.1">Upload</button><button data-frame-act="delete" data-slot="extraImages.1">Delete</button></div>
                    </div>
                    <div class="frame-slot" data-frame-slot="extraImages.2" draggable="true">
                        <div class="frame-slot-head"><span>Reserved 5</span></div>
                        <div class="frame-slot-preview" data-frame-preview="extraImages.2"></div>
                        <div class="frame-slot-actions"><button data-frame-act="upload" data-slot="extraImages.2">Upload</button><button data-frame-act="delete" data-slot="extraImages.2">Delete</button></div>
                    </div>
                </div>
            </div>
        </div>
        <div class="ctx-menu">
            <button data-ctx="replace">Change image</button>
            <button data-ctx="duplicate">Duplicate</button>
            <button data-ctx="delete">Delete segment</button>
        </div>
    `;
    root._selectedRefId = null;

    const ruler = root.querySelector(".ruler");
    const mainTrack = root.querySelector(".main-track");
    const meta = root.querySelector(".meta");
    const warnings = root.querySelector(".warnings");
    const segmentPrompt = root.querySelector('[data-prompt="segment"]');
    const segmentEpsilon = root.querySelector('[data-k="segmentEpsilon"]');
    const startStrength = root.querySelector('[data-k="startStrength"]');
    const endStrength = root.querySelector('[data-k="endStrength"]');
    const globalPrompt = root.querySelector('[data-prompt="global"]');
    const filePicker = root.querySelector("[data-file-picker]");
    const replacePicker = root.querySelector("[data-replace-picker]");
    const segmentImagePicker = root.querySelector("[data-segment-image-picker]");
    const ctxMenu = root.querySelector(".ctx-menu");

    function trackWidth() {
        if (mainTrack.clientWidth > 32) return mainTrack.clientWidth;
        if (ruler.clientWidth > 32) return ruler.clientWidth;
        return Math.max(320, root.clientWidth - 122);
    }

    function localXIn(element, clientX) {
        const rect = element.getBoundingClientRect();
        const cssWidth = element.clientWidth || rect.width || 1;
        const scale = rect.width ? cssWidth / rect.width : 1;
        return (clientX - rect.left) * scale;
    }

    function frameFromEvent(event, data) {
        const width = trackWidth();
        const x = Math.max(0, Math.min(width, localXIn(mainTrack, event.clientX)));
        return Math.round(x / Math.max(PX_PER_FRAME_MIN, width / (data.timelineFrames || data.totalFrames)));
    }

    function render() {
        const data = parseTimeline(node);
        root.querySelector('[data-k="totalFrames"]').value = data.totalFrames;
        root.querySelector('[data-k="durationSeconds"]').value = data.durationSeconds.toFixed(2);
        root.querySelector('[data-k="frameRate"]').value = data.frameRate;
        root.querySelector('[data-k="maxSegmentFrames"]').value = data.maxSegmentFrames;
        root.querySelector('[data-k="width"]').value = data.width;
        root.querySelector('[data-k="height"]').value = data.height;
        root.querySelector('[data-k="timelineHeightScale"]').value = data.timelineHeightScale;
        root.querySelector('[data-k="matchFirstImageAspect"]').checked = data.matchFirstImageAspect;
        root.querySelector('[data-k="keepTimelineSeconds"]').checked = data.keepTimelineSeconds;
        root.querySelector('[data-k="enablePromptRelay"]').checked = data.enablePromptRelay;
        updateAspectButtons(root, data);
        ruler.innerHTML = "";
        mainTrack.querySelectorAll(".ref").forEach((el) => el.remove());
        mainTrack.querySelectorAll(".process-cursor").forEach((el) => el.remove());
        const scale = data.timelineHeightScale;
        mainTrack.style.minHeight = `${Math.round(96 * scale)}px`;
        root.querySelector(".audio-track").style.minHeight = `${Math.round(48 * scale)}px`;
        const width = trackWidth();
        const timelineFrames = data.timelineFrames || data.totalFrames;
        const pxPerFrame = Math.max(PX_PER_FRAME_MIN, width / timelineFrames);
        const step = Math.max(1, Math.round(data.frameRate));
        for (let f = 0; f <= timelineFrames; f += step) {
            const tick = document.createElement("div");
            tick.className = "tick";
            tick.style.left = `${Math.min(width - 1, f * pxPerFrame)}px`;
            tick.textContent = `${(f / data.frameRate).toFixed(0)}s`;
            ruler.appendChild(tick);
        }
        if (timelineFrames > data.totalFrames) {
            const cursor = document.createElement("div");
            cursor.className = "process-cursor";
            cursor.style.left = `${Math.min(width - 1, data.totalFrames * pxPerFrame)}px`;
            cursor.innerHTML = "<span>process end</span>";
            mainTrack.appendChild(cursor);
        }
        for (const ref of data.refs) {
            const previewImage = ref.image || ref.endImage || "";
            const el = document.createElement("div");
            el.className = `ref${previewImage ? "" : " prompt-only"}${root._selectedRefId === ref.id ? " selected" : ""}`;
            el.dataset.id = ref.id;
            el.style.left = `${ref.startFrame * pxPerFrame}px`;
            el.style.width = `${Math.max(28, (ref.endFrame - ref.startFrame) * pxPerFrame)}px`;
            el.style.height = `${Math.round(80 * scale)}px`;
            const len = ref.endFrame - ref.startFrame;
            el.title = `${ref.label}: ${ref.startFrame}-${ref.endFrame} (${len} frames)`;
            const promptPreview = escapeHtml((ref.prompt || ref.label || "Prompt only").slice(0, 80));
            el.innerHTML = `${previewImage ? `<img draggable="false" src="${viewUrl(previewImage)}">` : `<div class="prompt-fill">${promptPreview || "Prompt only"}</div>`}<div class="cap">${escapeHtml(ref.label)} · ${len}f</div>`;
            mainTrack.appendChild(el);
        }
        const selected = data.refs.find((ref) => ref.id === root._selectedRefId) || data.refs[0];
        if (!root._selectedRefId && selected) root._selectedRefId = selected.id;
        segmentPrompt.disabled = !selected;
        segmentEpsilon.disabled = !selected;
        const continuePrevious = root.querySelector('[data-k="continuePrevious"]');
        if (continuePrevious) {
            continuePrevious.disabled = !selected;
            continuePrevious.checked = !!selected?.continuePrevious;
        }
        if (document.activeElement !== segmentEpsilon) {
            segmentEpsilon.value = formatEpsilon(selected?.epsilon ?? 0.001);
        }
        for (const input of [startStrength, endStrength]) {
            const key = input?.dataset.k;
            if (input) {
                input.disabled = !selected;
                input.value = selected ? String(Math.max(0, Math.min(1, Number(selected[key] ?? 1)))) : "1";
                input.title = input.value;
            }
        }
        segmentPrompt.value = selected?.prompt || "";
        for (const slot of ["image", "endImage", "extraImages.0", "extraImages.1", "extraImages.2"]) {
            const slotEl = root.querySelector(`[data-frame-slot="${slot}"]`);
            const preview = root.querySelector(`[data-frame-preview="${slot}"]`);
            const name = slotValue(selected, slot);
            if (slotEl) slotEl.classList.toggle("empty", !name);
            if (preview) {
                preview.innerHTML = name
                    ? `<img draggable="false" src="${viewUrl(name)}" title="${escapeHtml(name)}">`
                    : `<span>${selected ? "No image" : "No segment selected"}</span>`;
            }
            root.querySelectorAll(`[data-frame-act][data-slot="${slot}"]`).forEach((button) => {
                button.disabled = !selected || (button.dataset.frameAct === "delete" && !name);
                if (button.dataset.frameAct === "upload") button.textContent = name ? "Change" : "Upload";
            });
        }
        globalPrompt.value = data.globalPrompt || "";
        const segs = Math.max(1, Math.ceil(data.totalFrames / data.maxSegmentFrames));
        const modeText = data.kind === "LH_WAN_CW_DIRECTOR_TIMELINE"
            ? `CW ${data.contextWindowFrames || data.maxSegmentFrames}f / overlap ${data.contextOverlapFrames ?? 16}f`
            : `${segs} SVI segment(s)`;
        const visualSuffix = timelineFrames > data.totalFrames ? ` · timeline ${timelineFrames}f / ${data.timelineSeconds.toFixed(2)}s` : "";
        meta.textContent = `${data.totalFrames} frames / ${data.durationSeconds.toFixed(2)}s @ ${data.frameRate} fps${visualSuffix} · ${data.width}x${data.height} · ${modeText} · ${data.refs.length} segment(s)`;

        const issues = [];
        if (data.kind === "LH_WAN_SVI_DIRECTOR_TIMELINE") {
            if (segs > 12) issues.push(`${segs} SVI segments requested; the current workflow processes only 12.`);
            if (data.maxSegmentFrames % 4 !== 1) issues.push("Max/seg should normally be 4n+1 for Wan temporal alignment.");
            for (let chunk = 0; chunk < Math.min(segs, 12); chunk++) {
                const start = chunk * data.maxSegmentFrames;
                const end = Math.min(data.totalFrames, start + data.maxSegmentFrames);
                const imageRefs = data.refs.filter((ref) =>
                    (ref.image || ref.endImage) && ref.startFrame < end && ref.endFrame > start
                );
                if (imageRefs.length > 1) {
                    issues.push(`Chunk ${chunk + 1} contains ${imageRefs.length} image-reference segments; only the primary one can condition I2V.`);
                }
            }
        }
        if (data.refs.some((ref) => (ref.extraImages || []).some(Boolean))) {
            issues.push("Reserved image slots 3-5 are saved but are not used by sampling yet.");
        }
        warnings.textContent = issues.slice(0, 5).join("\n");
        warnings.style.display = issues.length ? "block" : "none";
    }

    async function addImageFile(file, preferredStart = 0) {
        const data = parseTimeline(node);
        const range = firstFreeRange(data, preferredStart);
        if (!range) {
            alert("No free MAIN timeline space. Each image segment needs at least 5 frames.");
            return;
        }
        const button = root.querySelector('[data-act="add"]');
        button.disabled = true;
        button.textContent = "Uploading...";
        try {
            const image = await uploadImage(file);
            const ref = {
                id: `ref_${Date.now()}_${Math.round(Math.random() * 1000)}`,
                label: file.name.replace(/\.[^.]+$/, "") || `Reference ${data.refs.length + 1}`,
                image,
                endImage: "",
                extraImages: [],
                startFrame: range.start,
                endFrame: range.end,
                strength: 1,
                startStrength: 1,
                endStrength: 1,
                epsilon: 0.001,
                continuePrevious: false,
                prompt: "",
            };
            data.refs.push(ref);
            data.timelineFrames = Math.max(data.timelineFrames || data.totalFrames, ref.endFrame);
            root._selectedRefId = ref.id;
            writeTimeline(node, data);
            render();
        } catch (err) {
            console.error("[LH WAN SVI Director] image upload failed", err);
            alert(`Image upload failed: ${err.message || err}`);
        } finally {
            button.disabled = false;
            button.textContent = "Add Image";
        }
    }

    function addPromptSegment(preferredStart = 0) {
        const data = parseTimeline(node);
        const range = firstFreeRange(data, preferredStart);
        if (!range) return;
        const ref = {
            id: `ref_${Date.now()}_${Math.round(Math.random() * 1000)}`,
            label: `Prompt ${data.refs.length + 1}`,
            image: "",
            endImage: "",
            extraImages: [],
            startFrame: range.start,
            endFrame: range.end,
            strength: 1,
            startStrength: 1,
            endStrength: 1,
            epsilon: 0.001,
            continuePrevious: data.refs.length > 0,
            prompt: "",
        };
        data.refs.push(ref);
        data.timelineFrames = Math.max(data.timelineFrames || data.totalFrames, ref.endFrame);
        root._selectedRefId = ref.id;
        writeTimeline(node, data);
        render();
    }

    root.querySelectorAll("input").forEach((input) => {
        if (input.dataset.filePicker !== undefined || input.dataset.replacePicker !== undefined) return;
        if (input.dataset.segmentImagePicker !== undefined) return;
        if (input.dataset.k === "segmentEpsilon") return;
        if (input.dataset.k === "startStrength" || input.dataset.k === "endStrength") return;
        if (input.dataset.k === "continuePrevious") return;
        if (input.dataset.k === "enablePromptRelay") return;
        input.addEventListener("change", () => {
            const data = parseTimeline(node);
            if (input.dataset.k === "durationSeconds") {
                data.durationSeconds = Math.max(0.25, Math.min(60, Number(input.value) || data.durationSeconds || 5));
                data.totalFrames = Math.max(1, Math.round(data.durationSeconds * data.frameRate));
                data.timelineFrames = data.totalFrames;
            } else if (input.dataset.k === "frameRate") {
                const oldSeconds = data.durationSeconds || (data.totalFrames / data.frameRate);
                const oldFrameRate = data.frameRate;
                const newFrameRate = Math.max(1, Number(input.value));
                const ratio = newFrameRate / oldFrameRate;
                data.frameRate = newFrameRate;
                data.durationSeconds = oldSeconds;
                data.totalFrames = Math.max(1, Math.round(oldSeconds * newFrameRate));
                if (data.keepTimelineSeconds) {
                    data.timelineFrames = Math.max(data.totalFrames, Math.round((data.timelineFrames || data.totalFrames) * ratio));
                    data.refs = data.refs.map((ref) => ({
                        ...ref,
                        startFrame: Math.round(ref.startFrame * ratio),
                        endFrame: Math.round(ref.endFrame * ratio),
                    }));
                }
                if (data.kind === "LH_WAN_CW_DIRECTOR_TIMELINE") data.timelineFrames = data.totalFrames;
            } else if (input.dataset.k === "width" || input.dataset.k === "height") {
                data[input.dataset.k] = round16(input.value);
            } else if (input.dataset.k === "timelineHeightScale") {
                data.timelineHeightScale = Math.max(1, Math.min(4, Number(input.value)));
            } else if (input.dataset.k === "matchFirstImageAspect") {
                data.matchFirstImageAspect = input.checked;
            } else if (input.dataset.k === "keepTimelineSeconds") {
                data.keepTimelineSeconds = input.checked;
            } else {
                data[input.dataset.k] = Number(input.value);
            }
            writeTimeline(node, data);
            render();
        });
    });

    root.querySelectorAll("[data-preset]").forEach((button) => {
        button.addEventListener("click", () => {
            const data = parseTimeline(node);
            if (!data.matchFirstImageAspect) return;
            data.aspectPixelPreset = button.dataset.preset;
            writeTimeline(node, data);
            render();
        });
    });

    segmentPrompt.addEventListener("input", () => {
        const data = parseTimeline(node);
        const ref = data.refs.find((item) => item.id === root._selectedRefId);
        if (ref) {
            ref.prompt = segmentPrompt.value;
            writeTimeline(node, data);
        }
    });

    segmentEpsilon.addEventListener("input", () => {
        const data = parseTimeline(node);
        const ref = data.refs.find((item) => item.id === root._selectedRefId);
        if (ref) {
            ref.epsilon = clampEpsilon(segmentEpsilon.value || 0.001);
            writeTimeline(node, data);
        }
    });

    segmentEpsilon.addEventListener("change", () => {
        const data = parseTimeline(node);
        const ref = data.refs.find((item) => item.id === root._selectedRefId);
        if (ref) {
            ref.epsilon = clampEpsilon(segmentEpsilon.value || 0.001);
            segmentEpsilon.value = formatEpsilon(ref.epsilon);
            writeTimeline(node, data);
        }
    });

    const continuePreviousInput = root.querySelector('[data-k="continuePrevious"]');
    continuePreviousInput?.addEventListener("change", (event) => {
        const data = parseTimeline(node);
        const ref = data.refs.find((item) => item.id === root._selectedRefId);
        if (!ref) return;
        ref.continuePrevious = !!event.target.checked;
        writeTimeline(node, data);
        render();
    });

    const enablePromptRelayInput = root.querySelector('[data-k="enablePromptRelay"]');
    enablePromptRelayInput?.addEventListener("change", (event) => {
        const data = parseTimeline(node);
        data.enablePromptRelay = !!event.target.checked;
        writeTimeline(node, data);
        render();
    });

    for (const input of [startStrength, endStrength]) {
        input.addEventListener("input", () => {
            const data = parseTimeline(node);
            const ref = data.refs.find((item) => item.id === root._selectedRefId);
            if (!ref) return;
            const value = Math.max(0, Math.min(1, Number(input.value || 0)));
            ref[input.dataset.k] = value;
            input.title = value.toFixed(2);
            writeTimeline(node, data);
        });
    }

    globalPrompt.addEventListener("input", () => {
        const data = parseTimeline(node);
        data.globalPrompt = globalPrompt.value;
        writeTimeline(node, data);
    });

    root.querySelector('[data-act="add"]').addEventListener("click", () => {
        filePicker.value = "";
        filePicker.click();
    });

    root.querySelector('[data-act="add-prompt"]').addEventListener("click", () => {
        addPromptSegment(parseTimeline(node).refs.at(-1)?.endFrame || 0);
    });

    root.querySelector('[data-act="delete"]').addEventListener("click", () => {
        const data = parseTimeline(node);
        if (!root._selectedRefId) return;
        data.refs = data.refs.filter((ref) => ref.id !== root._selectedRefId);
        root._selectedRefId = data.refs[0]?.id || null;
        writeTimeline(node, data);
        render();
    });

    filePicker.addEventListener("change", async () => {
        const file = filePicker.files?.[0];
        if (file) await addImageFile(file, parseTimeline(node).refs.at(-1)?.endFrame || 0);
    });

    replacePicker.addEventListener("change", async () => {
        const file = replacePicker.files?.[0];
        if (!file || !root._ctxRefId) return;
        const data = parseTimeline(node);
        const ref = data.refs.find((item) => item.id === root._ctxRefId);
        if (!ref) return;
        try {
            ref.image = await uploadImage(file);
            ref.label = file.name.replace(/\.[^.]+$/, "") || ref.label;
            root._selectedRefId = ref.id;
            writeTimeline(node, data);
            render();
        } catch (err) {
            console.error("[LH WAN SVI Director] image replace failed", err);
            alert(`Image replace failed: ${err.message || err}`);
        }
    });

    async function setSelectedFrameImage(slot, file) {
        const data = parseTimeline(node);
        const ref = data.refs.find((item) => item.id === root._selectedRefId);
        if (!ref || !file) return;
        const image = await uploadImage(file);
        setSlotValue(ref, slot, image);
        if (slot === "image") ref.label = file.name.replace(/\.[^.]+$/, "") || ref.label;
        writeTimeline(node, data);
        render();
    }

    segmentImagePicker.addEventListener("change", async () => {
        const file = segmentImagePicker.files?.[0];
        const slot = root._segmentImageSlot || "image";
        segmentImagePicker.value = "";
        if (!file) return;
        try {
            await setSelectedFrameImage(slot, file);
        } catch (err) {
            console.error("[LH WAN SVI Director] segment image upload failed", err);
            alert(`Image upload failed: ${err.message || err}`);
        }
    });

    root.querySelectorAll("[data-frame-act]").forEach((button) => {
        button.addEventListener("click", () => {
            const slot = button.dataset.slot;
            const action = button.dataset.frameAct;
            const data = parseTimeline(node);
            const ref = data.refs.find((item) => item.id === root._selectedRefId);
            if (!ref || !slot) return;
            if (action === "upload") {
                root._segmentImageSlot = slot;
                segmentImagePicker.click();
                return;
            }
            if (action === "delete") {
                setSlotValue(ref, slot, "");
                writeTimeline(node, data);
                render();
            }
        });
    });

    root.querySelectorAll("[data-frame-slot]").forEach((slotEl) => {
        slotEl.addEventListener("dragstart", (event) => {
            event.dataTransfer?.setData("text/plain", slotEl.dataset.frameSlot);
        });
        slotEl.addEventListener("dragover", (event) => {
            event.preventDefault();
            slotEl.classList.add("drag-over");
        });
        slotEl.addEventListener("dragleave", () => slotEl.classList.remove("drag-over"));
        slotEl.addEventListener("drop", async (event) => {
            event.preventDefault();
            slotEl.classList.remove("drag-over");
            const targetSlot = slotEl.dataset.frameSlot;
            const files = [...(event.dataTransfer?.files || [])].filter((file) => file.type.startsWith("image/"));
            if (files[0]) {
                try {
                    await setSelectedFrameImage(targetSlot, files[0]);
                } catch (err) {
                    console.error("[LH WAN SVI Director] segment image drop failed", err);
                    alert(`Image upload failed: ${err.message || err}`);
                }
                return;
            }
            const sourceSlot = event.dataTransfer?.getData("text/plain");
            const validSlots = ["image", "endImage", "extraImages.0", "extraImages.1", "extraImages.2"];
            if (!sourceSlot || sourceSlot === targetSlot || !validSlots.includes(sourceSlot)) return;
            const data = parseTimeline(node);
            const ref = data.refs.find((item) => item.id === root._selectedRefId);
            if (!ref) return;
            const tmp = slotValue(ref, sourceSlot);
            setSlotValue(ref, sourceSlot, slotValue(ref, targetSlot));
            setSlotValue(ref, targetSlot, tmp);
            writeTimeline(node, data);
            render();
        });
    });

    mainTrack.addEventListener("dragover", (event) => {
        if ([...(event.dataTransfer?.items || [])].some((item) => item.type?.startsWith("image/"))) {
            event.preventDefault();
            mainTrack.classList.add("drop-on");
        }
    });

    mainTrack.addEventListener("dragleave", () => mainTrack.classList.remove("drop-on"));

    mainTrack.addEventListener("drop", async (event) => {
        event.preventDefault();
        mainTrack.classList.remove("drop-on");
        const data = parseTimeline(node);
        const preferredStart = frameFromEvent(event, data);
        const files = [...(event.dataTransfer?.files || [])].filter((file) => file.type.startsWith("image/"));
        for (const file of files) {
            await addImageFile(file, preferredStart);
        }
    });

    let drag = null;
    mainTrack.addEventListener("mousedown", (event) => {
        const el = event.target.closest(".ref");
        if (!el) return;
        const data = parseTimeline(node);
        const ref = data.refs.find((r) => r.id === el.dataset.id);
        if (!ref) return;
        root._selectedRefId = ref.id;
        const pxPerFrame = Math.max(PX_PER_FRAME_MIN, trackWidth() / (data.timelineFrames || data.totalFrames));
        const x = localXIn(mainTrack, event.clientX);
        const localX = x - ref.startFrame * pxPerFrame;
        const segmentPx = Math.max(1, (ref.endFrame - ref.startFrame) * pxPerFrame);
        const edgePx = segmentPx * 0.2;
        const mode = localX < edgePx ? "left" : (ref.endFrame - ref.startFrame) * pxPerFrame - localX < edgePx ? "right" : "move";
        drag = { id: ref.id, mode, startX: x, startFrame: ref.startFrame, endFrame: ref.endFrame, pxPerFrame };
        event.preventDefault();
        render();
    });

    mainTrack.addEventListener("contextmenu", (event) => {
        const el = event.target.closest(".ref");
        if (!el) return;
        event.preventDefault();
        root._ctxRefId = el.dataset.id;
        root._selectedRefId = el.dataset.id;
        ctxMenu.style.left = `${localXIn(root, event.clientX)}px`;
        const rootRect = root.getBoundingClientRect();
        const rootCssHeight = root.clientHeight || rootRect.height || 1;
        const rootScaleY = rootRect.height ? rootCssHeight / rootRect.height : 1;
        ctxMenu.style.top = `${(event.clientY - rootRect.top) * rootScaleY}px`;
        ctxMenu.style.display = "block";
        render();
    });

    ctxMenu.addEventListener("click", (event) => {
        const action = event.target.closest("button")?.dataset.ctx;
        if (!action) return;
        ctxMenu.style.display = "none";
        if (action === "replace") {
            replacePicker.value = "";
            replacePicker.click();
            return;
        }
        const data = parseTimeline(node);
        if (action === "duplicate") {
            const duplicate = duplicateRefToRight(data, root._ctxRefId);
            if (duplicate) root._selectedRefId = duplicate.id;
            writeTimeline(node, data);
            render();
            return;
        }
        data.refs = data.refs.filter((ref) => ref.id !== root._ctxRefId);
        root._selectedRefId = data.refs[0]?.id || null;
        writeTimeline(node, data);
        render();
    });

    window.addEventListener("click", (event) => {
        if (!ctxMenu.contains(event.target)) ctxMenu.style.display = "none";
    }, { signal: windowEvents.signal });

    mainTrack.addEventListener("mousemove", (event) => {
        if (drag) return;
        for (const item of mainTrack.querySelectorAll(".ref")) item.classList.remove("resize-left", "resize-right");
        const el = event.target.closest(".ref");
        if (!el) return;
        const data = parseTimeline(node);
        const ref = data.refs.find((r) => r.id === el.dataset.id);
        if (!ref) return;
        const pxPerFrame = Math.max(PX_PER_FRAME_MIN, trackWidth() / (data.timelineFrames || data.totalFrames));
        const x = localXIn(mainTrack, event.clientX);
        const localX = x - ref.startFrame * pxPerFrame;
        const segmentPx = Math.max(1, (ref.endFrame - ref.startFrame) * pxPerFrame);
        const edgePx = segmentPx * 0.2;
        if (localX < edgePx) el.classList.add("resize-left");
        else if ((ref.endFrame - ref.startFrame) * pxPerFrame - localX < edgePx) el.classList.add("resize-right");
    });

    window.addEventListener("mousemove", (event) => {
        if (!drag) return;
        const data = parseTimeline(node);
        const ref = data.refs.find((r) => r.id === drag.id);
        if (!ref) return;
        const x = localXIn(mainTrack, event.clientX);
        const delta = Math.round((x - drag.startX) / drag.pxPerFrame);
        if (drag.mode === "move") {
            const len = drag.endFrame - drag.startFrame;
            const targetStart = Math.max(0, Math.round(drag.startFrame + delta));
            if (canPlaceRefAt(data, ref.id, targetStart)) {
                ref.startFrame = targetStart;
                ref.endFrame = targetStart + len;
                data.timelineFrames = Math.max(data.timelineFrames || data.totalFrames, ref.endFrame);
            } else {
                reorderRefsByDraggedCenter(data, ref.id, drag.startFrame + len / 2 + delta);
            }
        } else if (drag.mode === "left") {
            const prev = previousRef(data, ref);
            const minStart = prev ? prev.endFrame : 0;
            const maxStart = ref.endFrame - MIN_REF_FRAMES;
            const raw = snapFrame(drag.startFrame + delta, data, ref.id);
            ref.startFrame = Math.max(minStart, Math.min(maxStart, raw));
        } else {
            const next = nextRef(data, ref);
            const minEnd = ref.startFrame + MIN_REF_FRAMES;
            const maxLen = data.kind === "LH_WAN_CW_DIRECTOR_TIMELINE" ? data.totalFrames : data.maxSegmentFrames;
            const maxEnd = Math.min(ref.startFrame + maxLen, next ? next.startFrame : (data.timelineFrames || data.totalFrames));
            const raw = snapFrame(drag.endFrame + delta, data, ref.id);
            ref.endFrame = Math.max(minEnd, Math.min(maxEnd, raw));
        }
        writeTimeline(node, data);
        render();
    }, { signal: windowEvents.signal });

    window.addEventListener("mouseup", () => { drag = null; }, { signal: windowEvents.signal });
    if (typeof ResizeObserver !== "undefined") {
        const observer = new ResizeObserver(() => render());
        observer.observe(root);
        root._directorResizeObserver = observer;
    }
    setTimeout(render, 0);
    setTimeout(render, 120);
    root._renderDirector = render;
    root._destroyDirector = () => {
        windowEvents.abort();
        root._directorResizeObserver?.disconnect();
    };
    return root;
}

app.registerExtension({
    name: "LH.WAN.SVI.DirectorTimeline",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!NODE_NAMES.has(nodeData.name)) return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            const timelineWidget = widget(this, "timeline_data");
            if (timelineWidget) timelineWidget.type = "hidden";
            const root = makeDirectorUI(this);
            if (typeof this.addDOMWidget === "function") {
                this.addDOMWidget("director_timeline", "LHDirectorTimeline", root, {
                    serialize: false,
                    getValue: () => "",
                    setValue: () => {},
                });
            }
            this.setSize([820, 560]);
            return result;
        };
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            setTimeout(() => {
                const dom = this.widgets?.find((w) => w.name === "director_timeline")?.element;
                dom?._renderDirector?.();
            }, 0);
            return result;
        };
        const onRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            const dom = this.widgets?.find((w) => w.name === "director_timeline")?.element;
            dom?._destroyDirector?.();
            return onRemoved?.apply(this, arguments);
        };
    },
});
